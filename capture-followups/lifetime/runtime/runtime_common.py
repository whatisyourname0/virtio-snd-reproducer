# SPDX-License-Identifier: GPL-2.0-only
"""Shared artifact provenance checks and command-line options for QEMU tests."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


QEMU_SOURCE_SHA256 = "731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016"
QEMU_PATCH_SHA256 = "c35bfd4abf39e15cffdb42874fd6a036912f1a34924ef8f574c334a3362e7023"


def sha256_file(path: Path) -> str:
    """Hash a file without loading the full artifact into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_newc_members(initrd: Path) -> list[tuple[str, bytes]]:
    """Read file members from a gzip-compressed newc initramfs."""
    data = gzip.decompress(initrd.read_bytes())
    members: list[tuple[str, bytes]] = []
    offset = 0
    while offset + 110 <= len(data):
        magic = data[offset:offset + 6]
        if magic not in (b"070701", b"070702"):
            raise ValueError(f"invalid cpio newc header at offset {offset}")
        fields = [
            int(data[offset + 6 + index * 8:offset + 14 + index * 8], 16)
            for index in range(13)
        ]
        file_size, name_size = fields[6], fields[11]
        offset += 110
        if name_size < 1 or offset + name_size > len(data):
            raise ValueError("invalid cpio newc filename length")
        name = data[offset:offset + name_size - 1].decode("utf-8")
        offset = (offset + name_size + 3) & ~3
        if offset + file_size > len(data):
            raise ValueError(f"truncated cpio member {name!r}")
        content = data[offset:offset + file_size]
        offset = (offset + file_size + 3) & ~3
        if name == "TRAILER!!!":
            return members
        members.append((name, content))
    raise ValueError("initramfs has no cpio TRAILER!!! entry")


def add_runtime_arguments(parser: argparse.ArgumentParser, *, mode: bool) -> None:
    """Add explicit artifact and host-acceleration options to a runtime test."""
    if mode:
        parser.add_argument("mode", choices=("baseline", "candidate"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--qemu", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--module", type=Path)
    parser.add_argument("--observer", type=Path)
    parser.add_argument("--initrd", type=Path)
    parser.add_argument("--accel", choices=("hvf",), default="hvf")
    parser.add_argument("--log-dir", type=Path, default=Path("qemu-runtime-logs"))
    parser.add_argument("--qmp-dir", type=Path, default=Path(tempfile.gettempdir()))


def runtime_inputs(args: argparse.Namespace, mode: str) -> dict[str, Path]:
    """Resolve and hash-check runtime inputs against a freshly generated manifest."""
    manifest_path: Path = args.manifest.resolve(strict=True)
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError(f"unsupported runtime manifest schema: {manifest.get('schema_version')}")
    provenance = manifest.get("provenance", {})
    if provenance.get("qemu_source_sha256") != QEMU_SOURCE_SHA256:
        raise AssertionError("runtime manifest does not identify the verified QEMU 11.1.2 source")
    if provenance.get("qemu_test_patch_sha256") != QEMU_PATCH_SHA256:
        raise AssertionError("runtime manifest does not identify the packaged QEMU test patch")
    qemu_build = provenance["qemu_build_manifest"]
    qemu_build_path = Path(qemu_build["path"]).resolve(strict=True)
    if sha256_file(qemu_build_path) != qemu_build["sha256"]:
        raise AssertionError("QEMU build provenance manifest changed after input generation")
    qemu_build_data = json.loads(qemu_build_path.read_text(encoding="utf-8"))
    if (
        qemu_build_data.get("source_sha256") != QEMU_SOURCE_SHA256
        or qemu_build_data.get("test_patch_sha256") != QEMU_PATCH_SHA256
    ):
        raise AssertionError("QEMU build provenance does not match the source and patch")

    records: dict[str, dict[str, str]] = manifest["inputs"]
    qemu_record = records["qemu"]
    if qemu_build_data.get("binary_sha256") != qemu_record.get("sha256"):
        raise AssertionError("runtime manifest QEMU hash differs from its build manifest")
    mode_records = records[mode]
    overrides = {
        "qemu": args.qemu,
        "image": args.image,
        "module": args.module,
        "observer": args.observer,
        "initrd": args.initrd,
    }
    resolved: dict[str, Path] = {}
    for key, override in overrides.items():
        record = records["qemu"] if key == "qemu" else mode_records[key]
        path = (override or Path(record["path"])).resolve(strict=True)
        actual = sha256_file(path)
        if actual != record["sha256"]:
            raise AssertionError(
                f"runtime input hash mismatch for {key}: {actual} != {record['sha256']}"
            )
        resolved[key] = path

    kernel_build = provenance[mode]["manifest"]
    kernel_build_path = Path(kernel_build["path"]).resolve(strict=True)
    if sha256_file(kernel_build_path) != kernel_build["sha256"]:
        raise AssertionError(f"{mode} kernel build provenance manifest changed")
    kernel_build_data = json.loads(kernel_build_path.read_text(encoding="utf-8"))
    if kernel_build_data.get("source_commit") != provenance[mode]["source_commit"]:
        raise AssertionError(f"{mode} kernel source commit does not match its build manifest")
    for key, artifact_name in (
        ("image", "Image"),
        ("module", "virtio_snd.ko"),
        ("observer", "virtio_snd_lifetime_observer.ko"),
    ):
        expected_hash = kernel_build_data["artifacts"][artifact_name]["sha256"]
        if mode_records[key]["sha256"] != expected_hash:
            raise AssertionError(f"runtime manifest {mode} {key} hash differs from kernel build")

    members = read_newc_members(resolved["initrd"])
    for key, suffix in (
        ("module", "/kernel/sound/virtio/virtio_snd.ko"),
        ("observer", "/extra/virtio_snd_lifetime_observer.ko"),
    ):
        matches = [content for name, content in members if name.endswith(suffix)]
        if len(matches) != 1:
            raise AssertionError(f"expected exactly one packaged {key}, found {len(matches)}")
        embedded_hash = hashlib.sha256(matches[0]).hexdigest()
        if embedded_hash != sha256_file(resolved[key]):
            raise AssertionError(f"initramfs packaged {key} does not match the selected module")

    return resolved


def log_path(args: argparse.Namespace, name: str) -> Path:
    args.log_dir.mkdir(parents=True, exist_ok=True)
    return args.log_dir / f"{name}.log"


def qmp_socket_path(args: argparse.Namespace, name: str) -> Path:
    args.qmp_dir.mkdir(parents=True, exist_ok=True)
    path = args.qmp_dir / f"vsnd-{name}-{os.getpid()}.qmp"
    if len(str(path).encode()) >= 100:
        raise ValueError("QMP socket path is too long; choose a short --qmp-dir such as /tmp")
    return path
