# SPDX-License-Identifier: GPL-2.0-only
"""Thread-safe serial capture for QEMU guest marker checks."""

from __future__ import annotations

import re
import time
from pathlib import Path
from re import Pattern
from threading import Condition, Thread
from typing import IO


class SerialLog:
    """Capture guest serial output while waiting for markers."""

    def __init__(self, stream: IO[bytes], path: Path) -> None:
        self._stream = stream
        self._file = path.open("xb")
        self._condition = Condition()
        self.text = ""
        self._error: BaseException | None = None
        self._eof = False
        self._thread = Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            while True:
                chunk = self._stream.read(4096)
                if not chunk:
                    with self._condition:
                        self._eof = True
                        self._condition.notify_all()
                    return
                self._file.write(chunk)
                self._file.flush()
                with self._condition:
                    self.text += chunk.decode("utf-8", errors="replace")
                    self._condition.notify_all()
        except Exception as error:  # noqa: BLE001
            with self._condition:
                self._error = error
                self._condition.notify_all()

    def wait_for(self, token: str, timeout: float) -> bool:
        """Wait for a literal marker, returning false at the deadline."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while token not in self.text:
                self._raise_if_closed(token)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def wait_for_status(self, marker: str, timeout: float) -> int:
        """Wait for a `MARKER:<integer>` guest status line."""
        pattern = re.compile(rf"(?m)^(?:~ #[ \t]*)?{re.escape(marker)}:(\d+)\r?\n")
        match = self.wait_for_match(pattern, timeout)
        return int(match.group(1))

    def wait_for_match(self, pattern: Pattern[str], timeout: float) -> re.Match[str]:
        """Wait for a regex match and return the first match."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                match = pattern.search(self.text)
                if match:
                    return match
                self._raise_if_closed(pattern.pattern)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"serial marker timed out: {pattern.pattern}")
                self._condition.wait(remaining)

    def finish(self, timeout: float = 10) -> str:
        """Wait for QEMU stdout to close and return the complete transcript."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._eof and self._error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("serial reader did not reach EOF")
                self._condition.wait(remaining)
        self._thread.join(max(0, deadline - time.monotonic()))
        if self._thread.is_alive():
            raise TimeoutError("serial reader thread did not stop")
        if self._error:
            raise self._error
        return self.text

    def close_file(self) -> None:
        """Close the captured serial file after the reader has stopped."""
        self._file.close()

    def _raise_if_closed(self, token: str) -> None:
        if self._error:
            raise self._error
        if self._eof:
            raise RuntimeError(f"QEMU exited before serial marker {token!r}")
