"""Bound QMP request waits so test-level hang guards remain effective."""

from __future__ import annotations

import json
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from support.qmp import QMP


def test_qmp_command_times_out_when_server_does_not_reply() -> None:
    """A silent QMP peer cannot block the runner beyond its IO deadline."""
    temporary_directory = tempfile.TemporaryDirectory(dir="/tmp", prefix="qmp-")
    path = Path(temporary_directory.name) / "q.sock"
    listening = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listening.bind(str(path))
    listening.listen(1)
    peer_closed = threading.Event()

    def serve() -> None:
        connection, _ = listening.accept()
        with connection, connection.makefile("rb") as stream:
            connection.sendall(b'{"QMP":{}}\r\n')
            request = stream.readline()
            connection.sendall(
                json.dumps({"return": {}, "id": json.loads(request)["id"]}).encode()
                + b"\r\n"
            )
            json.loads(stream.readline())
            stream.readline()
            peer_closed.set()

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    client = QMP(path, timeout=0.1)
    try:
        with pytest.raises(TimeoutError, match="QMP response timed out"):
            client.command("query-status")
    finally:
        client.close()
        listening.close()
        server.join(timeout=1)
        temporary_directory.cleanup()
    assert peer_closed.is_set()
