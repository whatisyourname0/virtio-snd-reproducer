#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Reconstruct source trees from published patches without creating commits."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], env=env, text=True
    ).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        required=True,
        help="Git URL or local repository containing the pinned base",
    )
    parser.add_argument(
        "--destination",
        required=True,
        type=Path,
        help="New isolated repository; must not exist",
    )
    args = parser.parse_args()
    package = Path(__file__).resolve().parents[1]
    manifest = json.loads((package / "provenance/kernel-stack.json").read_text())
    destination = args.destination.resolve()
    if destination.exists():
        parser.error("destination already exists; choose a new isolated directory")
    for entry in manifest["ordered_patches"]:
        patch = package / entry["path"]
        if hashlib.sha256(patch.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError(f"patch hash mismatch: {entry['path']}")
    subprocess.run(
        ["git", "clone", "--no-checkout", "--", args.source, str(destination)],
        check=True,
    )
    base = manifest["base"]["commit"]
    git(destination, "cat-file", "-e", f"{base}^{{commit}}")
    trees: dict[str, str] = {}
    names = {7: "pre_ack", 8: "ack_only", 9: "ack_plus_highpri"}
    with tempfile.TemporaryDirectory(prefix="kernel-stack-") as temporary:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / "index"))
        git(destination, "read-tree", base, env=env)
        for entry in manifest["ordered_patches"]:
            patch = package / entry["path"]
            git(destination, "apply", "--cached", "--check", str(patch), env=env)
            git(destination, "apply", "--cached", str(patch), env=env)
            if entry["order"] in names:
                name = names[entry["order"]]
                tree = git(destination, "write-tree", env=env)
                trees[name] = tree
                # Keep tree objects reachable across later garbage collection.
                git(destination, "update-ref", f"refs/reproducer/{name}", tree)
    result = {
        "repository": str(destination),
        "base": base,
        "trees": trees,
        "refs": {name: f"refs/reproducer/{name}" for name in trees},
        "commits_created": False,
        "source_files": {
            name: {
                path: hashlib.sha256(
                    subprocess.check_output(
                        ["git", "-C", str(destination), "show", f"{tree}:{path}"]
                    )
                ).hexdigest()
                for path in (
                    "sound/virtio/virtio_pcm_ops.c",
                    "sound/virtio/virtio_pcm_msg.c",
                )
            }
            for name, tree in trees.items()
        },
    }
    output = destination / "reproducer-trees.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
