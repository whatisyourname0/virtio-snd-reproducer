#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a deterministic initrd from explicit fresh kernel and guest inputs."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import pathlib
import shutil
import stat
from typing import BinaryIO


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_newc_entry(
    output: BinaryIO,
    name: str,
    mode: int,
    inode: int,
    nlink: int,
    data: bytes,
) -> None:
    encoded_name = name.encode("utf-8") + b"\0"
    fields = (inode, mode, 0, 0, nlink, 0, len(data), 0, 0, 0, 0, len(encoded_name), 0)
    output.write(b"070701" + b"".join(f"{field:08x}".encode("ascii") for field in fields))
    output.write(encoded_name)
    output.write(b"\0" * ((-output.tell()) % 4))
    output.write(data)
    output.write(b"\0" * ((-output.tell()) % 4))


def archive_tree(root: pathlib.Path, output: BinaryIO) -> None:
    entries = [root]
    entries.extend(sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()))
    inode = 1
    for path in entries:
        name = "." if path == root else path.relative_to(root).as_posix()
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            mode |= stat.S_IFDIR
            data = b""
            nlink = 2
        elif stat.S_ISLNK(info.st_mode):
            mode |= stat.S_IFLNK
            data = os.readlink(path).encode("utf-8")
            nlink = 1
        elif stat.S_ISREG(info.st_mode):
            mode |= stat.S_IFREG
            data = path.read_bytes()
            nlink = 1
        else:
            raise SystemExit(f"unsupported special file in rootfs: {path}")
        write_newc_entry(output, name, mode, inode, nlink, data)
        inode += 1
    write_newc_entry(output, "TRAILER!!!", 0, inode, 1, b"")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-artifacts", required=True, type=pathlib.Path)
    parser.add_argument("--module-artifacts", required=True, type=pathlib.Path)
    parser.add_argument("--base-rootfs", required=True, type=pathlib.Path)
    parser.add_argument("--probe-dir", required=True, type=pathlib.Path)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    args = parser.parse_args()

    artifacts = args.kernel_artifacts.resolve(strict=True)
    module_artifacts = args.module_artifacts.resolve(strict=True)
    base_rootfs = args.base_rootfs.resolve(strict=True)
    probes = args.probe_dir.resolve(strict=True)
    output = args.output_dir.resolve()
    release = (artifacts / "kernel-release.txt").read_text(encoding="utf-8").strip()
    module_release = (module_artifacts / "kernel.release").read_text(encoding="utf-8").strip()
    if not release or release != module_release:
        raise SystemExit(f"module release mismatch: kernel={release!r}, module={module_release!r}")
    if not (artifacts / "build-artifacts.sha256").is_file():
        raise SystemExit("kernel build completion marker is missing")
    if output.exists() or output.is_symlink():
        raise SystemExit(f"refuse to overwrite existing initrd output {output}")
    if not base_rootfs.is_dir():
        raise SystemExit(f"base rootfs is missing: {base_rootfs}")
    if not (base_rootfs / "bin/busybox").is_file():
        raise SystemExit("base rootfs must contain /bin/busybox")
    inittab = base_rootfs / "etc/inittab"
    if not inittab.is_file() or "/lab-init" not in inittab.read_text(errors="replace"):
        raise SystemExit("base rootfs /etc/inittab must invoke /lab-init")
    init_link = base_rootfs / "init"
    if not init_link.is_symlink() or os.readlink(init_link) != "/bin/busybox":
        raise SystemExit("base rootfs must have /init -> /bin/busybox")
    for path in (base_rootfs / "dev", base_rootfs / "proc", base_rootfs / "sys"):
        if not path.is_dir():
            raise SystemExit(f"base rootfs is missing required mount point: {path.name}")

    module_tree = artifacts / "modules" / "lib" / "modules" / release
    if not module_tree.is_dir():
        raise SystemExit(f"fresh installed module tree is missing: {module_tree}")
    for name in ("snd_pcm_drain_test.ko", "kernel.release", "module.vermagic"):
        if not (module_artifacts / name).is_file():
            raise SystemExit(f"fresh synthetic module artifact is missing: {module_artifacts / name}")
    for name in ("lab-init", "playback-guard", "linked_capture_drain_later_member_r1"):
        if not (probes / name).is_file():
            raise SystemExit(f"fresh fixture executable is missing: {probes / name}")

    output.mkdir(parents=True)
    rootfs = output / "rootfs"
    shutil.copytree(base_rootfs, rootfs, symlinks=True)
    installed_modules = rootfs / "lib/modules"
    if installed_modules.exists() or installed_modules.is_symlink():
        if installed_modules.is_symlink():
            installed_modules.unlink()
        else:
            shutil.rmtree(installed_modules)
    installed_modules.mkdir(parents=True)
    shutil.copytree(module_tree, installed_modules / release, symlinks=True)
    shutil.copy2(module_artifacts / "snd_pcm_drain_test.ko", rootfs / "drain-test.ko")
    shutil.copy2(probes / "lab-init", rootfs / "lab-init")
    shutil.copy2(probes / "playback-guard", rootfs / "playback-guard")
    shutil.copy2(
        probes / "linked_capture_drain_later_member_r1",
        rootfs / "linked-drain-probe",
    )
    for name in ("lab-init", "playback-guard", "linked-drain-probe"):
        (rootfs / name).chmod(0o755)

    image = output / "initrd.cpio.gz"
    with image.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0, compresslevel=9) as compressed:
            archive_tree(rootfs, compressed)

    required_modules = (
        rootfs / "lib/modules" / release / "kernel/sound/core/snd-pcm.ko",
        rootfs / "lib/modules" / release / "kernel/sound/virtio/virtio_snd.ko",
    )
    for path in required_modules:
        if not path.is_file():
            raise SystemExit(f"required kernel module is missing from initrd: {path}")
    image_hash = sha256(image)
    (output / "initrd.sha256").write_text(f"{image_hash}  initrd.cpio.gz\n", encoding="utf-8")
    print(f"KERNEL_RELEASE={release}")
    print(f"KERNEL_CONFIG_SHA256={sha256(artifacts / 'kernel.config')}")
    print(f"DRAIN_TEST_MODULE_SHA256={sha256(module_artifacts / 'snd_pcm_drain_test.ko')}")
    print(f"SND_PCM_SHA256={sha256(required_modules[0])}")
    print(f"VIRTIO_SND_SHA256={sha256(required_modules[1])}")
    print(f"INITRD={image}")
    print(f"INITRD_BYTES={image.stat().st_size}")
    print(f"INITRD_SHA256={image_hash}")


if __name__ == "__main__":
    main()
