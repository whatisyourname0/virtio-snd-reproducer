#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Boot baseline/candidate ARM64 KASAN guests and run the PCM lifetime case."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time

from runtime_common import add_runtime_arguments, log_path, qmp_socket_path, runtime_inputs

HOLD_MS = 10_000
GUEST_WAIT_SECONDS = 35


def assert_kernel_diagnostics(text: str, expected_kasan_pattern: str | None) -> list[str]:
    diagnostic_prefixes = (
        "BUG:", "WARNING:", "Oops:", "Kernel panic - not syncing:",
        "Internal error:", "Unable to handle kernel",
    )
    diagnostics = [
        line.strip()
        for line in text.splitlines()
        if any(prefix in line for prefix in diagnostic_prefixes)
    ]
    kasan = [line for line in diagnostics if "BUG: KASAN:" in line]
    expected = (
        [line for line in kasan if re.search(expected_kasan_pattern, line)]
        if expected_kasan_pattern
        else []
    )
    unexpected = [line for line in diagnostics if line not in expected]
    if expected_kasan_pattern and not expected:
        raise AssertionError(
            f"missing expected baseline KASAN signature {expected_kasan_pattern!r}"
        )
    if unexpected:
        raise AssertionError("unexpected kernel diagnostics:\n" + "\n".join(unexpected))
    return diagnostics


class SerialLog:
    def __init__(self, stream, logfile: Path) -> None:
        self.stream = stream
        self.logfile = logfile.open("wb")
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
                decoded = chunk.decode("utf-8", errors="replace")
                with self.condition:
                    self.text += decoded
                    self.condition.notify_all()
        except BaseException as error:  # surfaced to the waiting test thread
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

    def wait_for_count(self, token: str, count: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.text.count(token) < count:
                if self.error:
                    raise self.error
                if self.eof:
                    raise RuntimeError(
                        f"QEMU exited before serial marker {token!r} reached count {count}"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return True

    def wait_for_status(self, marker: str, timeout: float) -> int:
        pattern = re.compile(
            rf"(?m)^(?:~ #\s*)?{re.escape(marker)}:(\d+)\r?(?:\n|$)"
        )
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
                    raise TimeoutError(f"guest did not print complete status {marker!r}")
                self.condition.wait(remaining)

    def contains(self, token: str) -> bool:
        with self.condition:
            return token in self.text

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


class QMP:
    def __init__(self, path: str) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        deadline = time.monotonic() + 20
        while True:
            try:
                self.sock.connect(path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        self.file = self.sock.makefile("rwb", buffering=0)
        self.next_id = 1
        self._read_object()
        self.command("qmp_capabilities")

    def _read_object(self) -> dict:
        while True:
            line = self.file.readline()
            if not line:
                raise RuntimeError("QMP connection closed")
            obj = json.loads(line)
            if "event" not in obj:
                return obj

    def command(self, name: str, arguments: dict | None = None):
        request_id = self.next_id
        self.next_id += 1
        command = {"execute": name, "id": request_id}
        if arguments is not None:
            command["arguments"] = arguments
        self.file.write(json.dumps(command).encode() + b"\r\n")
        while True:
            reply = self._read_object()
            if reply.get("id") == request_id:
                if "error" in reply:
                    raise RuntimeError(f"QMP {name} failed: {reply['error']}")
                return reply.get("return")

    def get(self, prop: str):
        return self.command("qom-get", {
            "path": "/machine/peripheral/snd0/virtio-backend",
            "property": prop,
        })

    def set(self, prop: str, value):
        return self.command("qom-set", {
            "path": "/machine/peripheral/snd0/virtio-backend",
            "property": prop,
            "value": value,
        })

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def send_guest_command(process: subprocess.Popen, command: str) -> None:
    if process.stdin is None:
        raise RuntimeError("QEMU serial input is unavailable")
    process.stdin.write(command.encode() + b"\n")
    process.stdin.flush()


def qmp_int_until(qmp: QMP, prop: str, expected: int, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = int(qmp.get(prop))
        if last >= expected:
            return last
        time.sleep(0.1)
    raise TimeoutError(f"QMP {prop} stayed at {last}, expected >= {expected}")


def run(mode: str, args: argparse.Namespace) -> None:
    inputs = runtime_inputs(args, mode)
    serial_log_path = log_path(args, f"kasan-{mode}-runtime")
    qmp_path = str(qmp_socket_path(args, mode))

    argv = [
        str(inputs["qemu"]), "-machine", "virt", "-accel", args.accel, "-cpu", "host",
        "-smp", "2", "-m", "2048", "-kernel", str(inputs["image"]),
        "-initrd", str(inputs["initrd"]),
        "-append", "console=ttyAMA0 earlycon=pl011,0x09000000 loglevel=7 "
                   "panic_on_warn=0 kasan_multi_shot=1",
        "-audiodev", "none,id=snd",
        "-device", "virtio-sound-pci,id=snd0,streams=2,audiodev=snd",
        "-display", "none", "-monitor", "none", "-serial", "stdio",
        "-qmp", f"unix:{qmp_path},server=on,wait=off", "-no-reboot",
    ]

    print(f"[{mode}] booting patched QEMU, serial log: {serial_log_path}", flush=True)
    process = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, bufsize=0,
    )
    serial = SerialLog(process.stdout, serial_log_path)
    qmp = None
    try:
        if not serial.wait_for("VIRTIO_SND_GUEST_READY", GUEST_WAIT_SECONDS):
            raise TimeoutError("guest did not reach virtio-snd ready state")
        if not serial.wait_for("VIRTSND_LIFETIME OBSERVER_READY", 5):
            raise RuntimeError("guest observer did not load; inspect serial log")
        qmp = QMP(qmp_path)
        properties = qmp.command(
            "qom-list", {"path": "/machine/peripheral/snd0/virtio-backend"}
        )
        property_names = {item["name"] for item in properties}
        needed = {
            "x-test-hold-capture-ms", "x-test-capture-held",
            "x-test-capture-completed",
        }
        if not needed.issubset(property_names):
            raise RuntimeError(f"patched QEMU properties missing: {needed - property_names}")

        qmp.set("x-test-hold-capture-ms", HOLD_MS)
        print(f"[{mode}] armed one capture descriptor for {HOLD_MS} ms", flush=True)
        send_guest_command(
            process,
            "arecord -D hw:0,0 -f S16_LE -r 48000 -c 2 -d 1 "
            f"/tmp/virtio-snd-timeout-{mode}.wav; "
            "echo LIFETIME_ARECORD_RC:$?",
        )
        if not serial.wait_for("VIRTSND_LIFETIME CAPTURE_SG_SAVED", 10):
            raise TimeoutError("guest did not observe a real capture descriptor")
        qmp_int_until(qmp, "x-test-capture-held", 1, 10)
        if not serial.wait_for("VIRTSND_LIFETIME PCM_HW_FREE_RETURN", 15):
            raise TimeoutError("ALSA did not reach hw_free after the PCM drain timeout")
        if serial.wait_for_status("LIFETIME_ARECORD_RC", 10) != 0:
            raise TimeoutError("arecord did not return after the PCM drain timeout")
        if not serial.wait_for("VIRTSND_LIFETIME AFTER_RELEASE_TIMEOUT_END", 5):
            raise TimeoutError("guest did not finish sampling pages after hw_free returned")
        if qmp_int_until(qmp, "x-test-capture-held", 1, 2) != 1:
            raise AssertionError("QEMU did not hold exactly one capture descriptor before completion")
        if int(qmp.get("x-test-capture-completed")) != 0:
            raise AssertionError("QEMU completed the descriptor before the post-release sample")
        if not serial.wait_for("VIRTSND_LIFETIME AFTER_BACKEND_COMPLETION_END", 20):
            raise TimeoutError("guest did not observe post-completion capture pages")
        qmp_int_until(qmp, "x-test-capture-completed", 1, 2)
        time.sleep(0.25)

        log_text = serial.text
        saved = re.search(
            r"CAPTURE_SG_SAVED stream=(\d+) pages=(\d+) bytes=(\d+) first_pfn=([0-9a-f]+) first_ref=(-?\d+)",
            log_text,
        )
        if not saved or int(saved.group(2)) != 6 or int(saved.group(3)) != 24_000:
            raise AssertionError("observer did not save the expected six-page, 24000-byte descriptor")

        release_begin = re.search(
            r"AFTER_RELEASE_TIMEOUT_BEGIN stream=(\d+) pages=(\d+) bytes=(\d+)", log_text
        )
        completion_begin = re.search(
            r"AFTER_BACKEND_COMPLETION_BEGIN stream=(\d+) pages=(\d+) bytes=(\d+)", log_text
        )
        if not release_begin or not completion_begin:
            raise AssertionError("observer did not print both capture sampling phase headers")
        if release_begin.groups() != completion_begin.groups() or (
            release_begin.group(2), release_begin.group(3)
        ) != (saved.group(2), saved.group(3)):
            raise AssertionError("capture sample phases do not match the saved descriptor metadata")

        def page_samples(phase: str) -> list[tuple[int, int, int, int, int]]:
            pattern = re.compile(
                rf"{phase}_PAGE index=(\d+) pfn=([0-9a-f]+) ref=(-?\d+) offset=(\d+) value=([0-9a-f]{{2}})"
            )
            return [
                (
                    int(m.group(1)), int(m.group(2), 16), int(m.group(3)),
                    int(m.group(4)), int(m.group(5), 16),
                )
                for m in pattern.finditer(log_text)
            ]

        released = page_samples("AFTER_RELEASE_TIMEOUT")
        completed = page_samples("AFTER_BACKEND_COMPLETION")
        expected_indexes = list(range(6))
        if [sample[0] for sample in released] != expected_indexes:
            raise AssertionError("the timeout observer did not sample all six descriptor pages once")
        if [sample[0] for sample in completed] != expected_indexes:
            raise AssertionError("the completion observer did not sample all six descriptor pages once")
        first_pfn = int(saved.group(4), 16)
        for samples, phase in ((released, "timeout"), (completed, "completion")):
            if [sample[1] for sample in samples] != [first_pfn + i for i in expected_indexes]:
                raise AssertionError(f"the {phase} observer did not sample the original SG PFNs")
            if any(sample[3] != 0 for sample in samples):
                raise AssertionError(f"the {phase} observer did not sample each page at offset zero")
        if any(sample[4] != 0xA5 for sample in completed):
            raise AssertionError("the delayed descriptor did not write 0xa5 to all original SG pages")
        if mode == "baseline" and any(sample[2] != 0 for sample in released):
            raise AssertionError("baseline pages were expected to have refcount zero after ALSA free")
        if mode == "candidate" and any(sample[2] < 1 for sample in released + completed):
            raise AssertionError("candidate did not retain all SG pages through delayed completion")
        if log_text.count("VIRTSND_LIFETIME CAPTURE_SG_SAVED") != 1:
            raise AssertionError("capture observer was armed more than once")
        if log_text.count("VIRTSND_LIFETIME PCM_HW_FREE_RETURN") != 1:
            raise AssertionError("capture observer scheduled more than one lifetime sample")
        if qmp_int_until(qmp, "x-test-capture-held", 1, 2) != 1:
            raise AssertionError("QEMU did not hold exactly one capture descriptor")
        if qmp_int_until(qmp, "x-test-capture-completed", 1, 2) != 1:
            raise AssertionError("QEMU did not complete exactly one delayed descriptor")
        ordered_markers = (
            "VIRTSND_LIFETIME PCM_HW_FREE_RETURN",
            "VIRTSND_LIFETIME AFTER_RELEASE_TIMEOUT_BEGIN",
            "VIRTSND_LIFETIME AFTER_RELEASE_TIMEOUT_END",
            "qemu-system-aarch64: virtio-snd lifetime test: completed delayed capture descriptor",
            "VIRTSND_LIFETIME AFTER_BACKEND_COMPLETION_BEGIN",
            "VIRTSND_LIFETIME AFTER_BACKEND_COMPLETION_END",
        )
        positions = [log_text.find(marker) for marker in ordered_markers]
        if any(position < 0 for position in positions) or positions != sorted(positions):
            raise AssertionError(
                "capture release, delayed completion, and observer sampling markers were out of order"
            )

        print(f"[{mode}] late descriptor completed; exercising normal PCM recovery", flush=True)
        recovery = (
            "speaker-test -D hw:0,0 -c 2 -t sine -f 440 -l 1 >/tmp/playback.log 2>&1; "
            "echo DIRECT_RECOVERY_PLAYBACK_RC:$?; "
            "arecord -D hw:0,0 -f S16_LE -r 48000 -c 2 -d 1 "
            f"/tmp/virtio-snd-recovery-{mode}.wav >/tmp/capture.log 2>&1; "
            "echo DIRECT_RECOVERY_CAPTURE_RC:$?; "
            f"aplay -D hw:0,0 /tmp/virtio-snd-recovery-{mode}.wav >/tmp/aplay.log 2>&1; "
            "echo DIRECT_RECOVERY_APLAY_RC:$?; "
            f"test $(wc -c </tmp/virtio-snd-recovery-{mode}.wav) -gt 180000; "
            "echo DIRECT_RECOVERY_CAPTURE_SIZE_RC:$?"
        )
        send_guest_command(process, recovery)
        direct_statuses = {
            marker: serial.wait_for_status(marker, 25)
            for marker in (
                "DIRECT_RECOVERY_PLAYBACK_RC", "DIRECT_RECOVERY_CAPTURE_RC",
                "DIRECT_RECOVERY_APLAY_RC", "DIRECT_RECOVERY_CAPTURE_SIZE_RC",
            )
        }
        print(f"[{mode}] normal recovery statuses={direct_statuses}; stopping QEMU", flush=True)
        qmp.command("quit")
        if process.wait(timeout=10) != 0:
            raise RuntimeError("QEMU exited with a non-zero status")
        log_text = serial.finish()
        expected = r"use-after-free in read_capture_pages" if mode == "baseline" else None
        diagnostics = assert_kernel_diagnostics(log_text, expected)
        if mode == "baseline" and len(diagnostics) != 12:
            raise AssertionError(
                f"baseline expected exactly 12 observer UAF diagnostics (six pages at two phases), "
                f"found {len(diagnostics)}"
            )
        if any(status != 0 for status in direct_statuses.values()):
            raise AssertionError(f"normal PCM recovery failed: {direct_statuses}")
        if mode == "candidate" and diagnostics:
            raise AssertionError("candidate produced kernel diagnostics during fault or recovery")
        if mode == "baseline":
            print(f"[{mode}] expected baseline KASAN reports={len(diagnostics)}", flush=True)
        print(f"[{mode}] PASS; QEMU exited cleanly and complete serial log was checked", flush=True)
    finally:
        if qmp is not None:
            qmp.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        try:
            serial.finish(timeout=5)
        except Exception:
            pass
        if os.path.exists(qmp_path):
            os.unlink(qmp_path)
        serial.logfile.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    add_runtime_arguments(parser, mode=True)
    arguments = parser.parse_args()
    run(arguments.mode, arguments)
