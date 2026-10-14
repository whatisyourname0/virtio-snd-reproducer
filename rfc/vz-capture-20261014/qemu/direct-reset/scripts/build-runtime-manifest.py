#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Create a path-independent SHA-256 manifest for direct-reset runtime inputs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from runtime_inputs import MODULE_PATHS, sha256


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--modules", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit(f"refusing to overwrite existing manifest: {args.output}")
    module_root = args.modules.resolve(strict=True)
    paths = {
        "qemu-system-aarch64": args.qemu,
        "Image": args.image,
        "initramfs.cpio.gz": args.initrd,
        **{name: module_root / relative for name, relative in MODULE_PATHS.items()},
    }
    rows = []
    for name, raw_path in paths.items():
        path = raw_path.resolve(strict=True)
        if not path.is_file():
            raise FileNotFoundError(path)
        rows.append((name, sha256(path)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(f"{digest}  {name}\n" for name, digest in rows))
    print(f"RUNTIME_MANIFEST={args.output.resolve()}")


if __name__ == "__main__":
    main()
