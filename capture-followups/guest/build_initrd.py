#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a sequence-test initramfs from explicit rootfs and installed modules."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from newc_pack import _archive_tree


class InitrdBuilder:
    """Stage caller inputs without editing the original rootfs or module tree."""

    @staticmethod
    def digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @classmethod
    def build(cls, args: argparse.Namespace) -> None:
        rootfs = args.rootfs.resolve(strict=True)
        modules = args.modules.resolve(strict=True)
        config = args.kernel_config.resolve(strict=True)
        release = args.release
        if not release or "/" in release or release in (".", ".."):
            raise ValueError("--release must be a kernel release, not a path")
        if not (rootfs / "bin/busybox").is_file():
            raise ValueError("rootfs must contain bin/busybox")
        if not (modules / "modules.dep").is_file():
            raise ValueError(
                "modules directory must contain modules.dep from modules_install/depmod"
            )
        config_text = config.read_text()
        for name in (
            "CONFIG_BLK_DEV_INITRD",
            "CONFIG_RD_GZIP",
            "CONFIG_VIRTIO_CONSOLE",
            "CONFIG_DEVTMPFS",
        ):
            if f"{name}=y\n" not in config_text:
                raise ValueError(f"kernel config requires {name}=y")
        if args.output_dir.exists():
            raise ValueError("output directory must not already exist")
        args.output_dir.mkdir(parents=True)
        with tempfile.TemporaryDirectory(prefix="snd-initrd-") as temporary:
            staging = Path(temporary) / "rootfs"
            shutil.copytree(rootfs, staging, symlinks=True)
            for relative in ("lib/modules", "etc", "dev", "proc", "sys", "tmp", "run"):
                if not (staging / relative).resolve().is_relative_to(staging.resolve()):
                    raise ValueError(
                        f"rootfs writable path escapes staging: {relative}"
                    )
            destination = staging / "lib/modules" / release
            if destination.exists():
                raise ValueError("rootfs already contains the selected module release")
            shutil.copytree(modules, destination, symlinks=True)
            for name in ("build", "source"):
                (destination / name).unlink(missing_ok=True)
            for name in ("dev", "proc", "sys", "tmp", "run", "etc"):
                (staging / name).mkdir(exist_ok=True)
            init = staging / "init"
            if init.exists() or init.is_symlink():
                init.unlink()
            init.symlink_to("bin/busybox")
            guest_init = Path(__file__).with_name("init-sequence")
            shutil.copy2(guest_init, staging / "etc/init-sequence")
            (staging / "etc/init-sequence").chmod(0o755)
            (staging / "etc/inittab").write_text(
                "::sysinit:/bin/sh /etc/init-sequence\n"
                "hvc0::respawn:/bin/sh\n"
                "::shutdown:/bin/sync\n"
            )
            # Module indexes are copied from the matching modules_install tree.
            # Do not run ARM64 rootfs tools on the host.
            archive = args.output_dir / "initramfs.cpio.gz"
            with tempfile.TemporaryFile() as raw:
                _archive_tree(staging, raw)
                raw.seek(0)
                with (
                    archive.open("wb") as output,
                    gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed,
                ):
                    shutil.copyfileobj(raw, compressed)
            records = {
                "schema_version": 1,
                "release": release,
                "kernel_config_sha256": cls.digest(config),
                "guest_init_sha256": cls.digest(guest_init),
                "initrd_sha256": cls.digest(archive),
                "modules": {
                    str(path.relative_to(modules)): cls.digest(path)
                    for path in sorted(modules.rglob("*"))
                    if path.is_file() and not path.is_symlink()
                },
                "rootfs_source": str(rootfs),
                "modules_source": str(modules),
                "scope": "synthetic sequence fixture; no host microphone",
            }
            (args.output_dir / "build-manifest.json").write_text(
                json.dumps(records, indent=2) + "\n"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument(
        "--modules",
        type=Path,
        required=True,
        help="Installed lib/modules/RELEASE directory",
    )
    parser.add_argument("--release", required=True)
    parser.add_argument("--kernel-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    InitrdBuilder.build(parser.parse_args())


if __name__ == "__main__":
    main()
