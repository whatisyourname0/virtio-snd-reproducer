#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Pack a direct-reset initrd from an explicit rootfs and installed modules."""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import stat
import tempfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument(
        "--modules",
        type=Path,
        required=True,
        help="contents of lib/modules/<release> from modules_install",
    )
    parser.add_argument("--release", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def write_newc_entry(
    output,
    name: str,
    mode: int,
    inode: int,
    nlink: int,
    data: bytes,
) -> None:
    encoded_name = name.encode("utf-8") + b"\0"
    fields = (
        inode,
        mode,
        0,
        0,
        nlink,
        0,
        len(data),
        0,
        0,
        0,
        0,
        len(encoded_name),
        0,
    )
    output.write(
        b"070701" + b"".join(f"{field:08x}".encode("ascii") for field in fields)
    )
    output.write(encoded_name)
    output.write(b"\0" * ((-output.tell()) % 4))
    output.write(data)
    output.write(b"\0" * ((-output.tell()) % 4))


def archive_tree(root: Path, output) -> None:
    entries = [
        root,
        *sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()),
    ]
    inode = 1
    for path in entries:
        name = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            mode |= stat.S_IFDIR
            data = b""
            nlink = 2
        elif stat.S_ISLNK(metadata.st_mode):
            mode |= stat.S_IFLNK
            data = os.readlink(path).encode("utf-8")
            nlink = 1
        elif stat.S_ISREG(metadata.st_mode):
            mode |= stat.S_IFREG
            data = path.read_bytes()
            nlink = 1
        else:
            raise SystemExit(f"unsupported special file in rootfs: {path}")
        write_newc_entry(output, name, mode, inode, nlink, data)
        inode += 1
    write_newc_entry(output, "TRAILER!!!", 0, inode, 1, b"")


def validate_inputs(rootfs: Path, modules: Path, release: str, output: Path) -> None:
    if release in ("", ".", "..") or Path(release).name != release:
        raise SystemExit("--release must be one non-empty path component")
    if output.exists() or output.is_symlink():
        raise SystemExit(f"refusing to overwrite existing output: {output}")
    if not (rootfs / "bin/busybox").is_file():
        raise SystemExit("rootfs must contain /bin/busybox")
    inittab = rootfs / "etc/inittab"
    if not inittab.is_file() or "/lab-init" not in inittab.read_text(errors="replace"):
        raise SystemExit("rootfs /etc/inittab must invoke /lab-init")
    for relative in (
        "modules.dep",
        "modules.dep.bin",
        "modules.alias",
        "kernel/sound/core/snd.ko",
        "kernel/sound/core/snd-timer.ko",
        "kernel/sound/core/snd-pcm.ko",
        "kernel/sound/virtio/virtio_snd.ko",
    ):
        if not (modules / relative).is_file():
            raise SystemExit(f"missing module input: {modules / relative}")
    dep_text = (modules / "modules.dep").read_text()
    virtio_line = next(
        (
            line
            for line in dep_text.splitlines()
            if line.startswith("kernel/sound/virtio/virtio_snd.ko:")
        ),
        None,
    )
    if virtio_line is None or any(
        required not in virtio_line
        for required in (
            "kernel/sound/core/snd-pcm.ko",
            "kernel/sound/core/snd.ko",
        )
    ):
        raise SystemExit("modules.dep does not include the ALSA core dependencies")
    pcm_line = next(
        (
            line
            for line in dep_text.splitlines()
            if line.startswith("kernel/sound/core/snd-pcm.ko:")
        ),
        None,
    )
    if pcm_line is None or "kernel/sound/core/snd-timer.ko" not in pcm_line:
        raise SystemExit("modules.dep does not include the snd-timer dependency")


def main() -> None:
    args = parse_args()
    rootfs = args.rootfs.resolve(strict=True)
    modules = args.modules.resolve(strict=True)
    if not rootfs.is_dir() or not modules.is_dir():
        raise SystemExit("rootfs and modules must be directories")
    validate_inputs(rootfs, modules, args.release, args.output)
    source = Path(__file__).resolve().parents[1]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="direct-reset-initrd-") as temporary:
        staging = Path(temporary) / "root"
        shutil.copytree(rootfs, staging, symlinks=True)
        module_destination = staging / "lib/modules" / args.release
        if module_destination.is_dir() and not module_destination.is_symlink():
            shutil.rmtree(module_destination)
        elif module_destination.exists() or module_destination.is_symlink():
            module_destination.unlink()
        module_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(modules, module_destination, symlinks=True)
        shutil.copy2(source / "guest/lab-init", staging / "lab-init")
        (staging / "lab-init").chmod(0o755)
        init = staging / "init"
        if init.exists() or init.is_symlink():
            init.unlink()
        init.symlink_to("/bin/busybox")
        with (
            args.output.open("wb") as raw,
            gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed,
        ):
            archive_tree(staging, compressed)
    print(f"INITRD={args.output.resolve()}")
    print(f"INITRD_BYTES={args.output.stat().st_size}")


if __name__ == "__main__":
    main()
