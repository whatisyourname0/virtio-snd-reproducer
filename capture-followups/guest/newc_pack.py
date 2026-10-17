#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Pack an initrd with the trace-capturing guest init and explicit modules."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def _write_entry(
    output, name: str, mode: int, inode: int, nlink: int, data: bytes
) -> None:
    encoded_name = name.encode("utf-8") + b"\0"
    fields = (inode, mode, 0, 0, nlink, 0, len(data), 0, 0, 0, 0, len(encoded_name), 0)
    output.write(b"070701" + b"".join(f"{field:08x}".encode() for field in fields))
    output.write(encoded_name)
    output.write(b"\0" * ((-output.tell()) % 4))
    output.write(data)
    output.write(b"\0" * ((-output.tell()) % 4))


def _archive_tree(root: Path, output) -> None:
    paths = [
        root,
        *sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix()),
    ]
    for inode, path in enumerate(paths, start=1):
        name = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            mode |= stat.S_IFDIR
            nlink = 2
            data = b""
        elif stat.S_ISLNK(metadata.st_mode):
            mode |= stat.S_IFLNK
            nlink = 1
            data = os.readlink(path).encode()
        elif stat.S_ISREG(metadata.st_mode):
            mode |= stat.S_IFREG
            nlink = 1
            data = path.read_bytes()
        else:
            raise SystemExit(f"unsupported special file in rootfs: {path}")
        _write_entry(output, name, mode, inode, nlink, data)
    _write_entry(output, "TRAILER!!!", 0, len(paths) + 1, 1, b"")
