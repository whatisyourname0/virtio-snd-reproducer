"""Small synchronous QMP client for fixture properties."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any


class QMP:
    """Connect to QEMU and run request-response QMP commands."""

    def __init__(self, path: Path, timeout: float = 10) -> None:
        if timeout <= 0:
            raise ValueError("QMP timeout must be positive")
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._timeout = timeout
        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"QMP socket did not become available: {path}")
                self._socket.settimeout(remaining)
                try:
                    self._socket.connect(str(path))
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            self._file = self._socket.makefile("rwb", buffering=0)
            self._next_id = 1
            self._read_object(deadline)
            self.command("qmp_capabilities")
        except BaseException:
            if hasattr(self, "_file"):
                self._file.close()
            self._socket.close()
            raise

    def _read_object(self, deadline: float) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("QMP response deadline expired")
            self._socket.settimeout(remaining)
            try:
                line = self._file.readline()
            except TimeoutError as error:
                raise TimeoutError("QMP response timed out") from error
            if not line:
                raise RuntimeError("QMP connection closed")
            result = json.loads(line)
            if "event" not in result:
                return result

    def command(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        deadline = time.monotonic() + self._timeout
        request_id = self._next_id
        self._next_id += 1
        request: dict[str, Any] = {"execute": name, "id": request_id}
        if arguments is not None:
            request["arguments"] = arguments
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"QMP {name} request deadline expired")
        self._socket.settimeout(remaining)
        self._file.write(json.dumps(request).encode("utf-8") + b"\r\n")
        while True:
            response = self._read_object(deadline)
            if response.get("id") == request_id:
                if "error" in response:
                    raise RuntimeError(f"QMP {name} failed: {response['error']}")
                return response.get("return")

    def get(self, property_name: str) -> Any:
        return self.command(
            "qom-get",
            {
                "path": "/machine/peripheral/snd0/virtio-backend",
                "property": property_name,
            },
        )

    def set(self, property_name: str, value: int) -> Any:
        return self.command(
            "qom-set",
            {
                "path": "/machine/peripheral/snd0/virtio-backend",
                "property": property_name,
                "value": value,
            },
        )

    def close(self) -> None:
        """Close the QMP stream and socket."""
        if not self._file.closed:
            self._file.close()
        self._socket.close()
