# SPDX-License-Identifier: GPL-2.0-only
"""Shared runtime helpers for the synthetic lifecycle QEMU lanes."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO

MODULE_PATHS = {
    "snd.ko": "kernel/sound/core/snd.ko",
    "snd-timer.ko": "kernel/sound/core/snd-timer.ko",
    "snd-pcm.ko": "kernel/sound/core/snd-pcm.ko",
    "virtio_snd.ko": "kernel/sound/virtio/virtio_snd.ko",
}


def sha256(path: Path) -> str:
    """Return the SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_newc_members(initrd: Path) -> Iterator[tuple[str, bytes]]:
    """Yield names and contents from a gzip-compressed newc initrd."""
    archive = gzip.decompress(initrd.read_bytes())
    offset = 0
    while offset + 110 <= len(archive):
        header = archive[offset : offset + 110]
        if header[:6] not in (b"070701", b"070702"):
            raise ValueError(f"invalid newc header at offset {offset}")
        fields = [
            int(header[6 + index * 8 : 14 + index * 8], 16) for index in range(13)
        ]
        size, name_size = fields[6], fields[11]
        offset += 110
        if name_size < 1 or offset + name_size > len(archive):
            raise ValueError("invalid newc filename length")
        name_raw = archive[offset : offset + name_size]
        if not name_raw.endswith(b"\0"):
            raise ValueError("newc filename is not NUL terminated")
        name = name_raw[:-1].decode("utf-8", errors="surrogateescape")
        offset = (offset + name_size + 3) & ~3
        if offset + size > len(archive):
            raise ValueError(f"truncated newc member: {name}")
        contents = archive[offset : offset + size]
        offset = (offset + size + 3) & ~3
        if name == "TRAILER!!!":
            return
        yield name, contents
    raise ValueError("newc archive is missing TRAILER!!!")


def validate_packaged_modules(
    modules: Path, release: str, initrd: Path
) -> dict[str, str]:
    """Require the initrd module bytes to match the explicit module directory."""
    expected = {
        f"lib/modules/{release}/{relative}": modules / relative
        for relative in MODULE_PATHS.values()
    }
    module_prefix = f"lib/modules/{release}/"
    required_members = set(expected) | {
        f"{module_prefix}modules.dep",
        f"{module_prefix}modules.dep.bin",
    }
    members: dict[str, bytes] = {}
    for name, contents in read_newc_members(initrd):
        if name in required_members:
            if name in members:
                raise AssertionError(f"initrd contains duplicate member {name}")
            members[name] = contents
    hashes: dict[str, str] = {}
    for member, source in expected.items():
        if not source.is_file():
            raise FileNotFoundError(source)
        packaged = members.get(member)
        if packaged is None:
            raise AssertionError(f"initrd is missing required module {member}")
        source_hash = sha256(source)
        packaged_hash = hashlib.sha256(packaged).hexdigest()
        if source_hash != packaged_hash:
            raise AssertionError(f"initrd module {member} differs from {source}")
        hashes[member] = source_hash
    modules_dep = members.get(f"{module_prefix}modules.dep")
    modules_dep_bin = members.get(f"{module_prefix}modules.dep.bin")
    if not modules_dep or not modules_dep_bin:
        raise AssertionError(
            "initrd must contain non-empty modules.dep and modules.dep.bin"
        )
    dep_lines = modules_dep.decode("utf-8").splitlines()
    virtio_line = next(
        (
            line
            for line in dep_lines
            if line.startswith(f"{MODULE_PATHS['virtio_snd.ko']}:")
        ),
        None,
    )
    if virtio_line is None or any(
        required not in virtio_line
        for required in (MODULE_PATHS["snd-pcm.ko"], MODULE_PATHS["snd.ko"])
    ):
        raise AssertionError(
            "modules.dep does not map virtio_snd to the packaged ALSA core"
        )
    pcm_line = next(
        (
            line
            for line in dep_lines
            if line.startswith(f"{MODULE_PATHS['snd-pcm.ko']}:")
        ),
        None,
    )
    if pcm_line is None or MODULE_PATHS["snd-timer.ko"] not in pcm_line:
        raise AssertionError(
            "modules.dep does not map snd-pcm to the packaged snd-timer"
        )
    return hashes


def assert_no_kernel_diagnostics(text: str) -> None:
    """Fail on kernel warnings, KASAN reports, oopses, and panics."""
    markers = (
        "BUG:",
        "KASAN:",
        "WARNING:",
        "Oops:",
        "Kernel panic",
        "Internal error:",
        "Unable to handle kernel",
    )
    found = [
        line.strip()
        for line in text.splitlines()
        if any(mark in line for mark in markers)
    ]
    if found:
        raise AssertionError("kernel diagnostics found:\n" + "\n".join(found))


class SerialLog:
    """Read QEMU serial output without blocking guest-control operations."""

    def __init__(self, stream: IO[bytes], path: Path) -> None:
        self.stream = stream
        self.path = path
        self.logfile = path.open("xb")
        self.condition = threading.Condition()
        self.text = ""
        self.error: BaseException | None = None
        self.eof = False
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        try:
            while True:
                chunk = self.stream.read(4096)
                if not chunk:
                    with self.condition:
                        self.eof = True
                        self.condition.notify_all()
                    return
                self.logfile.write(chunk)
                self.logfile.flush()
                with self.condition:
                    self.text += chunk.decode("utf-8", errors="replace")
                    self.condition.notify_all()
        except Exception as error:  # noqa: BLE001
            self.error = error
            with self.condition:
                self.condition.notify_all()

    def wait_for(self, token: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self.condition:
            while token not in self.text:
                if self.error:
                    raise self.error
                if self.eof:
                    raise RuntimeError(f"QEMU exited before serial marker {token!r}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return True

    def wait_for_status(self, marker: str, timeout: float) -> int:
        pattern = re.compile(rf"(?m)^(?:~ #\s*)?{re.escape(marker)}:(\d+)\r?\n")
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                match = pattern.search(self.text)
                if match:
                    return int(match.group(1))
                if self.error:
                    raise self.error
                if self.eof:
                    raise RuntimeError(f"QEMU exited before status marker {marker!r}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"guest did not print status marker {marker!r}")
                self.condition.wait(remaining)

    def wait_for_match(self, pattern: re.Pattern[str], timeout: float) -> re.Match[str]:
        """Wait for a complete regex match in the serial stream."""
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                match = pattern.search(self.text)
                if match:
                    return match
                if self.error:
                    raise self.error
                if self.eof:
                    raise RuntimeError("QEMU exited before the serial result line")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("serial result line did not arrive in full")
                self.condition.wait(remaining)

    def finish(self, timeout: float = 10) -> str:
        deadline = time.monotonic() + timeout
        with self.condition:
            while not self.eof and self.error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("QEMU serial reader did not reach EOF")
                self.condition.wait(remaining)
        self.thread.join(max(0, deadline - time.monotonic()))
        if self.thread.is_alive():
            raise TimeoutError("QEMU serial reader did not stop")
        if self.error:
            raise self.error
        self.logfile.flush()
        return self.text

    def close(self) -> None:
        self.logfile.close()


class QMP:
    """Small synchronous QMP client for the virtio-snd test properties."""

    def __init__(self, path: Path, timeout: float = 20) -> None:
        if timeout <= 0:
            raise ValueError("QMP timeout must be positive")
        deadline = time.monotonic() + timeout
        while True:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(min(1, max(0.01, deadline - time.monotonic())))
            try:
                sock.connect(str(path))
                self.sock = sock
                break
            except (FileNotFoundError, ConnectionRefusedError, TimeoutError) as error:
                sock.close()
                if time.monotonic() >= deadline:
                    raise TimeoutError("QMP socket did not become available") from error
                time.sleep(min(0.1, deadline - time.monotonic()))
        self.sock.settimeout(min(10, timeout))
        self.file = self.sock.makefile("rwb", buffering=0)
        self.next_id = 1
        self._read_object()
        self.command("qmp_capabilities")

    def _read_object(self) -> dict[str, object]:
        while True:
            line = self.file.readline()
            if not line:
                raise RuntimeError("QMP connection closed")
            reply = json.loads(line)
            if "event" not in reply:
                return reply

    def command(self, name: str, arguments: dict[str, object] | None = None) -> object:
        request_id = self.next_id
        self.next_id += 1
        request: dict[str, object] = {"execute": name, "id": request_id}
        if arguments is not None:
            request["arguments"] = arguments
        self.file.write(json.dumps(request).encode("utf-8") + b"\r\n")
        while True:
            reply = self._read_object()
            if reply.get("id") == request_id:
                if "error" in reply:
                    raise RuntimeError(f"QMP {name} failed: {reply['error']}")
                return reply.get("return")

    def get(self, prop: str) -> object:
        return self.command(
            "qom-get",
            {
                "path": "/machine/peripheral/snd0/virtio-backend",
                "property": prop,
            },
        )

    def set(self, prop: str, value: int) -> object:
        return self.command(
            "qom-set",
            {
                "path": "/machine/peripheral/snd0/virtio-backend",
                "property": prop,
                "value": value,
            },
        )

    def close(self) -> None:
        self.file.close()
        self.sock.close()


class ProcessDeadline:
    """Terminate one owned subprocess when its wall-clock budget expires."""

    def __init__(
        self, process: subprocess.Popen[bytes], timeout: float, grace: float = 5
    ) -> None:
        if timeout <= 0:
            raise ValueError("process timeout must be positive")
        self.process = process
        self.timeout = timeout
        self.grace = grace
        self.expired = threading.Event()
        self.cancelled = threading.Event()
        self.deadline: float | None = None
        self.thread = threading.Thread(
            target=self._watch, name="qemu-wall-timeout", daemon=True
        )

    def start(self) -> None:
        """Start the timeout watcher."""
        self.deadline = time.monotonic() + self.timeout
        self.thread.start()

    def _watch(self) -> None:
        if self.cancelled.wait(self.timeout) or self.process.poll() is not None:
            return
        self.expired.set()
        try:
            self.process.terminate()
        except ProcessLookupError:
            return
        try:
            self.process.wait(timeout=self.grace)
        except subprocess.TimeoutExpired:
            try:
                self.process.kill()
            except ProcessLookupError:
                return
            self.process.wait()

    def check(self) -> None:
        """Raise when the process exceeded its configured wall timeout."""
        if self.expired.is_set() or (
            self.deadline is not None
            and time.monotonic() >= self.deadline
            and self.process.poll() is None
        ):
            raise TimeoutError(f"QEMU exceeded its {self.timeout:g}s wall timeout")

    def remaining(self) -> float:
        """Return the wall-time budget still available to a blocking operation."""
        self.check()
        if self.deadline is None:
            raise RuntimeError("process deadline has not started")
        return max(0, self.deadline - time.monotonic())

    def close(self) -> None:
        """Stop the watcher and wait for any timeout cleanup to finish."""
        self.cancelled.set()
        if self.thread.ident is not None:
            self.thread.join(self.grace + 1)


def send_guest_command(process: subprocess.Popen[bytes], command: str) -> None:
    """Write a shell command to the QEMU serial console."""
    if process.stdin is None:
        raise RuntimeError("QEMU serial input is unavailable")
    process.stdin.write(command.encode("utf-8") + b"\n")
    process.stdin.flush()


def qmp_integer(qmp: QMP, prop: str) -> int:
    """Read a QMP integer property and reject an unexpected JSON type."""
    value = qmp.get(prop)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"QMP {prop} returned {value!r}, expected an integer")
    return value


def qmp_int_until(qmp: QMP, prop: str, expected: int, timeout: float) -> int:
    """Poll a QMP integer property until it reaches a threshold."""
    deadline = time.monotonic() + timeout
    latest: int | None = None
    while time.monotonic() < deadline:
        latest = qmp_integer(qmp, prop)
        if latest >= expected:
            return latest
        time.sleep(0.1)
    raise TimeoutError(f"QMP {prop} stayed at {latest}, expected >= {expected}")
