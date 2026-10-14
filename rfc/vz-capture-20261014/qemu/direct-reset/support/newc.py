"""Read members from gzip-compressed newc initramfs archives."""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from pathlib import Path, PurePosixPath


def read_members(archive: Path) -> Iterator[tuple[str, bytes]]:
    """Yield normalized member paths and contents from a newc archive."""
    data = gzip.decompress(archive.read_bytes())
    offset = 0
    while offset + 110 <= len(data):
        magic = data[offset : offset + 6]
        if magic not in (b"070701", b"070702"):
            raise ValueError(f"invalid newc header at offset {offset}")
        fields = [
            int(data[offset + 6 + index * 8 : offset + 14 + index * 8], 16)
            for index in range(13)
        ]
        file_size, name_size = fields[6], fields[11]
        offset += 110
        if name_size < 1 or offset + name_size > len(data):
            raise ValueError("invalid newc filename length")
        name = data[offset : offset + name_size - 1].decode("utf-8")
        offset = (offset + name_size + 3) & ~3
        if offset + file_size > len(data):
            raise ValueError(f"truncated newc member {name!r}")
        content = data[offset : offset + file_size]
        offset = (offset + file_size + 3) & ~3
        if name == "TRAILER!!!":
            return
        yield PurePosixPath(name).as_posix().removeprefix("./"), content
    raise ValueError("newc archive has no TRAILER!!! entry")
