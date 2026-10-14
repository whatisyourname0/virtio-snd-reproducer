# SPDX-License-Identifier: GPL-2.0-only
"""Focused runtime-helper regressions for lifecycle fixtures."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime import QMP, ProcessDeadline, SerialLog


def test_status_waits_for_a_newline_after_a_partial_stream_chunk(
    tmp_path: Path,
) -> None:
    """A partial integer at the end of a stream chunk is not a full status."""
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb", buffering=0)
    serial = SerialLog(stream, tmp_path / "serial.log")
    try:
        os.write(write_fd, b"~ # echo LIFECYCLE_SIGNAL_RC:$?\r\n")
        with pytest.raises(TimeoutError):
            serial.wait_for_status("LIFECYCLE_SIGNAL_RC", timeout=0.05)

        os.write(write_fd, b"LIFECYCLE_SIGNAL_RC:1")
        assert serial.wait_for("LIFECYCLE_SIGNAL_RC:1", timeout=2)
        with pytest.raises(TimeoutError):
            serial.wait_for_status("LIFECYCLE_SIGNAL_RC", timeout=0.05)

        os.write(write_fd, b"37\r\n")
        assert serial.wait_for_status("LIFECYCLE_SIGNAL_RC", timeout=2) == 137
    finally:
        os.close(write_fd)
        serial.finish(timeout=2)
        serial.close()


def test_process_deadline_terminates_the_owned_child() -> None:
    """A short QEMU wall timeout terminates its child and is reported."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    deadline = ProcessDeadline(process, timeout=0.05, grace=0.5)
    started = time.monotonic()
    deadline.start()
    try:
        process.wait(timeout=2)
        with pytest.raises(TimeoutError, match="0.05s wall timeout"):
            deadline.check()
        assert time.monotonic() - started < 2
    finally:
        deadline.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_wait_for_match_requires_the_complete_result_line(tmp_path: Path) -> None:
    """A result line is unavailable until its terminating newline arrives."""
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb", buffering=0)
    serial = SerialLog(stream, tmp_path / "serial.log")
    pattern = re.compile(r"(?m)^RESULT value=(\d+)\r?\n")
    try:
        os.write(write_fd, b"~ # echo RESULT value=$(some-command)\r\n")
        with pytest.raises(TimeoutError):
            serial.wait_for_match(pattern, timeout=0.05)

        os.write(write_fd, b"RESULT value=42")
        assert serial.wait_for("RESULT value=42", timeout=2)
        with pytest.raises(TimeoutError, match="did not arrive in full"):
            serial.wait_for_match(pattern, timeout=0.05)

        os.write(write_fd, b"\r\n")
        assert serial.wait_for_match(pattern, timeout=2).group(1) == "42"
    finally:
        os.close(write_fd)
        serial.finish(timeout=2)
        serial.close()


def test_qmp_command_has_a_bounded_no_response_wait() -> None:
    """A QMP peer that never replies cannot block beyond its socket timeout."""
    temporary = tempfile.TemporaryDirectory(prefix="qmp-", dir="/tmp")
    path = Path(temporary.name) / "qmp.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)
    received_command = threading.Event()

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            connection.sendall(b'{"QMP":{"version":{},"capabilities":[]}}\r\n')
            reader = connection.makefile("rb")
            capabilities = json.loads(reader.readline())
            connection.sendall(
                (json.dumps({"return": {}, "id": capabilities["id"]}) + "\r\n").encode()
            )
            reader.readline()
            received_command.set()
            time.sleep(0.2)

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    qmp = QMP(path, timeout=0.05)
    try:
        with pytest.raises(TimeoutError):
            qmp.command("query-status")
        assert received_command.is_set()
    finally:
        qmp.close()
        listener.close()
        server.join(timeout=2)
        temporary.cleanup()
