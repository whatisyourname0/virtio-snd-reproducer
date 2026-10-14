"""Focused tests for complete guest status-line parsing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from support.serial_log import SerialLog


def test_status_waits_for_a_newline_after_a_partial_stream_chunk(
    tmp_path: Path,
) -> None:
    """A partial status number is not accepted before its line terminator."""
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb", buffering=0)
    serial = SerialLog(stream, tmp_path / "serial.log")
    try:
        os.write(write_fd, b"~ # echo DIRECT_RESET_STOP_CHILD_EXIT_RC:137\r\n")
        with pytest.raises(TimeoutError):
            serial.wait_for_status("DIRECT_RESET_STOP_CHILD_EXIT_RC", timeout=0.05)

        os.write(write_fd, b"DIRECT_RESET_STOP_CHILD_EXIT_RC:1")
        assert serial.wait_for("DIRECT_RESET_STOP_CHILD_EXIT_RC:1", timeout=2)
        with pytest.raises(TimeoutError):
            serial.wait_for_status("DIRECT_RESET_STOP_CHILD_EXIT_RC", timeout=0.05)

        os.write(write_fd, b"37\r\n")
        assert (
            serial.wait_for_status("DIRECT_RESET_STOP_CHILD_EXIT_RC", timeout=2) == 137
        )
    finally:
        os.close(write_fd)
        serial.finish(timeout=2)
        serial.close_file()
