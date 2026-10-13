#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bind fresh QEMU and guest builds to the runtime validators by SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from runtime_common import (
    QEMU_PATCH_SHA256,
    QEMU_SOURCE_SHA256,
    read_newc_members,
    sha256_file,
)


QEMU_DIR = Path(__file__).resolve().parents[1]


def artifact_record(path: Path) -> dict[str, str]:
    resolved = path.resolve(strict=True)
    return {"path": str(resolved), "sha256": sha256_file(resolved)}


def load_kernel_build(path: Path, mode: str) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("mode") != mode:
        raise ValueError(f"invalid {mode} kernel build manifest: {path}")
    if manifest.get("arch") != "arm64":
        raise ValueError(f"{mode} build is not ARM64: {path}")
    artifacts = manifest["artifacts"]
    for key in ("Image", "virtio_snd.ko", "virtio_snd_lifetime_observer.ko"):
        record = artifacts[key]
        artifact = Path(record["path"]).resolve(strict=True)
        actual = sha256_file(artifact)
        if actual != record["sha256"]:
            raise AssertionError(f"{mode} build artifact hash mismatch for {key}")
    provenance = {
        "manifest": artifact_record(path),
        "artifact_origin": manifest.get("artifact_origin", "built_from_source"),
        "source_ref": manifest["source_ref"],
        "source_commit": manifest["source_commit"],
        "source_archive_sha256": manifest["source_archive_sha256"],
    }
    return provenance, artifacts


def packaged_module_hashes(initrd: Path) -> dict[str, str]:
    members = read_newc_members(initrd)
    required = {
        "virtio_snd.ko": "/kernel/sound/virtio/virtio_snd.ko",
        "virtio_snd_lifetime_observer.ko": "/extra/virtio_snd_lifetime_observer.ko",
    }
    packaged: dict[str, str] = {}
    for key, suffix in required.items():
        matches = [content for name, content in members if name.endswith(suffix)]
        if len(matches) != 1:
            raise AssertionError(f"{initrd}: expected one {key}, found {len(matches)}")
        packaged[key] = hashlib.sha256(matches[0]).hexdigest()
    return packaged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu-build", type=Path, required=True)
    parser.add_argument("--baseline-build", type=Path, required=True)
    parser.add_argument("--candidate-build", type=Path, required=True)
    parser.add_argument("--baseline-initrd", type=Path, required=True)
    parser.add_argument("--candidate-initrd", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    qemu_manifest = json.loads(args.qemu_build.read_text(encoding="utf-8"))
    if qemu_manifest.get("schema_version") != 1 or qemu_manifest.get("qemu_version") != "11.1.2":
        raise ValueError("expected a QEMU 11.1.2 build manifest")
    if qemu_manifest.get("source_sha256") != QEMU_SOURCE_SHA256:
        raise AssertionError("QEMU build manifest has an unexpected source archive hash")
    if qemu_manifest.get("test_patch_sha256") != QEMU_PATCH_SHA256:
        raise AssertionError("QEMU build manifest has an unexpected test patch hash")
    qemu_binary = Path(qemu_manifest["binary"]).resolve(strict=True)
    if sha256_file(qemu_binary) != qemu_manifest.get("binary_sha256"):
        raise AssertionError("QEMU binary does not match its build manifest")

    provenance: dict[str, Any] = {
        "qemu_artifact_origin": qemu_manifest.get("artifact_origin", "built_from_source"),
        "qemu_source_sha256": QEMU_SOURCE_SHA256,
        "qemu_test_patch_sha256": QEMU_PATCH_SHA256,
        "qemu_build_manifest": artifact_record(args.qemu_build),
    }
    inputs: dict[str, Any] = {"qemu": artifact_record(qemu_binary)}
    for mode in ("baseline", "candidate"):
        build_path: Path = getattr(args, f"{mode}_build")
        initrd: Path = getattr(args, f"{mode}_initrd")
        kernel_provenance, artifacts = load_kernel_build(build_path, mode)
        initrd_record = artifact_record(initrd)
        package_hashes = packaged_module_hashes(Path(initrd_record["path"]))
        for name in ("virtio_snd.ko", "virtio_snd_lifetime_observer.ko"):
            if package_hashes[name] != artifacts[name]["sha256"]:
                raise AssertionError(f"{mode} initramfs embeds a different {name}")
        provenance[mode] = kernel_provenance
        inputs[mode] = {
            "image": artifacts["Image"],
            "module": artifacts["virtio_snd.ko"],
            "observer": artifacts["virtio_snd_lifetime_observer.ko"],
            "initrd": initrd_record,
        }

    if provenance["baseline"]["source_commit"] == provenance["candidate"]["source_commit"]:
        raise AssertionError("baseline and candidate kernel source commits must differ")

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "provenance": provenance, "inputs": inputs}
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(f"runtime inputs recorded: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
