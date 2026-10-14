#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a deterministic gzip-compressed newc initrd from explicit inputs."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

MODULE_PATHS = {
    "snd_sha256": "kernel/sound/core/snd.ko",
    "snd_timer_sha256": "kernel/sound/core/snd-timer.ko",
    "snd_pcm_sha256": "kernel/sound/core/snd-pcm.ko",
    "virtio_snd_sha256": "kernel/sound/virtio/virtio_snd.ko",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rootfs",
        type=Path,
        required=True,
        help="complete ARM64 Linux root filesystem directory",
    )
    parser.add_argument(
        "--modules",
        type=Path,
        required=True,
        help="contents of lib/modules/<release> from modules_install",
    )
    parser.add_argument(
        "--release",
        required=True,
        help="exact kernel release string, e.g. 7.3.0-rc6-lab",
    )
    parser.add_argument(
        "--probe-dir",
        type=Path,
        required=True,
        help="directory containing guest executables selected by --suite",
    )
    parser.add_argument(
        "--suite",
        choices=("short-drain", "lifecycle"),
        default="short-drain",
        help="guest init and probe set to package",
    )
    parser.add_argument("--output", type=Path, required=True, help="output .cpio.gz")
    return parser.parse_args()


def write_newc_entry(
    out,
    name: str,
    mode: int,
    ino: int,
    nlink: int,
    mtime: int,
    data: bytes,
    devmajor: int = 0,
    devminor: int = 0,
    rdevmajor: int = 0,
    rdevminor: int = 0,
) -> None:
    encoded_name = name.encode("utf-8") + b"\0"
    fields = (
        ino,
        mode,
        0,
        0,
        nlink,
        mtime,
        len(data),
        devmajor,
        devminor,
        rdevmajor,
        rdevminor,
        len(encoded_name),
        0,
    )
    out.write(b"070701" + b"".join(f"{field:08x}".encode("ascii") for field in fields))
    out.write(encoded_name)
    out.write(b"\0" * ((-out.tell()) % 4))
    out.write(data)
    out.write(b"\0" * ((-out.tell()) % 4))


def archive_tree(root: Path, output) -> None:
    entries = [root]
    entries.extend(
        sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix())
    )
    ino = 1
    for path in entries:
        rel = "." if path == root else path.relative_to(root).as_posix()
        st = path.lstat()
        mode = stat.S_IMODE(st.st_mode)
        if stat.S_ISDIR(st.st_mode):
            mode |= stat.S_IFDIR
            data = b""
            nlink = 2
        elif stat.S_ISLNK(st.st_mode):
            mode |= stat.S_IFLNK
            data = os.readlink(path).encode("utf-8")
            nlink = 1
        elif stat.S_ISREG(st.st_mode):
            mode |= stat.S_IFREG
            data = path.read_bytes()
            nlink = 1
        else:
            raise SystemExit(f"unsupported special file in rootfs: {path}")
        write_newc_entry(output, rel, mode, ino, nlink, 0, data)
        ino += 1
    write_newc_entry(output, "TRAILER!!!", 0, ino, 1, 0, b"")


def main() -> None:
    args = parse_args()
    rootfs = args.rootfs.resolve(strict=True)
    if args.release in ("", ".", "..") or Path(args.release).name != args.release:
        raise SystemExit("--release must be one path component")
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    manifest = args.output.with_name(args.output.name + ".inputs.txt")
    if manifest.exists() or manifest.is_symlink():
        raise SystemExit(f"refusing to overwrite existing manifest: {manifest}")
    modules = args.modules.resolve(strict=True)
    probes = args.probe_dir.resolve(strict=True)
    if not rootfs.is_dir() or not modules.is_dir() or not probes.is_dir():
        raise SystemExit("rootfs, modules, and probe-dir must be directories")
    if not (rootfs / "bin/busybox").is_file():
        raise SystemExit("rootfs must contain /bin/busybox")
    if not (rootfs / "etc/inittab").is_file():
        raise SystemExit("rootfs must contain an /etc/inittab that runs /lab-init")
    if args.suite == "short-drain":
        probe_names = ("no-query-probe", "playback-guard")
        init_source = Path(__file__).resolve().parent.parent / "source/lab-init"
    else:
        probe_names = ("fsm_control_probe", "pause-rxflush-probe")
        init_source = (
            Path(__file__).resolve().parent.parent / "lifecycle/source/lab-init"
        )
    for filename in probe_names:
        if not (probes / filename).is_file():
            raise SystemExit(f"missing probe executable: {probes / filename}")
    if not init_source.is_file():
        raise SystemExit(f"missing init script for suite {args.suite}: {init_source}")
    for required in ("modules.dep", "modules.dep.bin", "modules.alias"):
        if not (modules / required).is_file():
            raise SystemExit(f"missing {required}; run depmod for this kernel release")
    for required in MODULE_PATHS.values():
        if not (modules / required).is_file():
            raise SystemExit(f"missing required loadable sound module: {required}")
    dep_lines = (modules / "modules.dep").read_text().splitlines()
    virtio_line = next(
        (
            line
            for line in dep_lines
            if line.startswith(f"{MODULE_PATHS['virtio_snd_sha256']}:")
        ),
        None,
    )
    if virtio_line is None or any(
        required not in virtio_line
        for required in (
            MODULE_PATHS["snd_pcm_sha256"],
            MODULE_PATHS["snd_sha256"],
        )
    ):
        raise SystemExit(
            "modules.dep does not map virtio_snd to the packaged ALSA core"
        )
    pcm_line = next(
        (
            line
            for line in dep_lines
            if line.startswith(f"{MODULE_PATHS['snd_pcm_sha256']}:")
        ),
        None,
    )
    if pcm_line is None or MODULE_PATHS["snd_timer_sha256"] not in pcm_line:
        raise SystemExit("modules.dep does not map snd-pcm to the packaged snd-timer")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qemu-audio-initrd-") as temp_name:
        staging = Path(temp_name) / "root"
        shutil.copytree(rootfs, staging, symlinks=True)
        module_dest = staging / "lib/modules" / args.release
        if module_dest.exists() or module_dest.is_symlink():
            if module_dest.is_dir() and not module_dest.is_symlink():
                shutil.rmtree(module_dest)
            else:
                module_dest.unlink()
        module_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(modules, module_dest, symlinks=True)
        for filename in probe_names:
            shutil.copy2(probes / filename, staging / filename)
        shutil.copy2(init_source, staging / "lab-init")
        (staging / "lab-init").chmod(0o755)
        init_link = staging / "init"
        if not init_link.exists() and not init_link.is_symlink():
            init_link.symlink_to("/bin/busybox")
        inittab = staging / "etc/inittab"
        contents = inittab.read_text(errors="replace")
        if "/lab-init" not in contents:
            raise SystemExit("/etc/inittab must invoke /lab-init as a sysinit action")

        # Fixed gzip metadata, zeroed archive mtimes, and stable traversal order
        # make the archive reproducible when rootfs contents are unchanged.
        with (
            args.output.open("wb") as raw,
            gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed,
        ):
            archive_tree(staging, compressed)

    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    module_hashes = "".join(
        f"{key}={sha256(modules / relative)}\n"
        for key, relative in MODULE_PATHS.items()
    )
    manifest.write_text(
        f"suite={args.suite}\n"
        f"rootfs={rootfs}\n"
        f"modules={modules}\n"
        f"release={args.release}\n"
        f"probe_dir={probes}\n"
        f"{module_hashes}"
        f"initrd_sha256={sha256(args.output)}\n"
    )
    print(f"SUITE={args.suite}")
    print(f"ROOTFS={rootfs}")
    print(f"MODULES={modules}")
    print(f"RELEASE={args.release}")
    print(f"INITRD={args.output.resolve()}")
    print(f"INITRD_BYTES={args.output.stat().st_size}")
    print(f"INITRD_SHA256={sha256(args.output)}")
    print(f"INPUT_MANIFEST={manifest.resolve()}")


if __name__ == "__main__":
    main()
