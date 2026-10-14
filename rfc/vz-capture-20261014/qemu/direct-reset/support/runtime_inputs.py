"""Validate direct-reset runtime artifacts against their hash manifest."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from newc import read_members

MODULE_PATHS = {
    "snd.ko": "kernel/sound/core/snd.ko",
    "snd-timer.ko": "kernel/sound/core/snd-timer.ko",
    "snd-pcm.ko": "kernel/sound/core/snd-pcm.ko",
    "virtio_snd.ko": "kernel/sound/virtio/virtio_snd.ko",
}


def sha256(path: Path) -> str:
    """Return a file's SHA-256 digest."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(path: Path) -> dict[str, str]:
    """Read and validate `sha256  artifact-name` manifest entries."""
    entries: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise ValueError(f"invalid runtime manifest line {line_number}")
        if parts[1] in entries:
            raise ValueError(f"duplicate runtime manifest entry: {parts[1]}")
        entries[parts[1]] = parts[0]
    return entries


def validate_inputs(
    *,
    qemu: Path,
    image: Path,
    initrd: Path,
    modules: Path,
    release: str,
    manifest: Path,
) -> None:
    """Check required file hashes and the modules actually packaged in initrd."""
    if release in ("", ".", "..") or Path(release).name != release:
        raise ValueError("kernel release must be one non-empty path component")
    module_root = modules.resolve(strict=True)
    expected = read_manifest(manifest)
    paths = {
        "qemu-system-aarch64": qemu,
        "Image": image,
        "initramfs.cpio.gz": initrd,
        **{name: module_root / relative for name, relative in MODULE_PATHS.items()},
    }
    for name, raw_path in paths.items():
        artifact = raw_path.resolve(strict=True)
        if not artifact.is_file():
            raise FileNotFoundError(artifact)
        actual = sha256(artifact)
        if expected.get(name) != actual:
            raise AssertionError(
                f"runtime hash mismatch for {name}: expected {expected.get(name)}, "
                f"found {actual} at {artifact}"
            )

    version = subprocess.run(
        [str(qemu.resolve(strict=True)), "-version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.splitlines()[0]
    if "11.1.2" not in version:
        raise ValueError(f"expected QEMU 11.1.2, found {version}")

    members: dict[str, bytes] = {}
    for name, content in read_members(initrd):
        if name in members:
            raise AssertionError(f"initramfs contains duplicate member: {name}")
        members[name] = content
    for name, relative in MODULE_PATHS.items():
        archive_path = f"lib/modules/{release}/{relative}"
        embedded = members.get(archive_path)
        if embedded is None:
            raise AssertionError(f"initramfs is missing {archive_path}")
        embedded_sha = hashlib.sha256(embedded).hexdigest()
        if embedded_sha != expected[name]:
            raise AssertionError(f"initramfs module hash mismatch for {archive_path}")

    modules_prefix = f"lib/modules/{release}/"
    indexes = {
        name.removeprefix(modules_prefix): content
        for name, content in members.items()
        if name.startswith(modules_prefix)
        and name[len(modules_prefix) :].startswith("modules.")
    }
    dep_text = indexes.get("modules.dep")
    if dep_text is None or not indexes.get("modules.dep.bin"):
        raise AssertionError(
            "initramfs must contain non-empty modules.dep and modules.dep.bin"
        )
    dep_lines = dep_text.decode("utf-8").splitlines()
    virtio_line = next(
        (
            line
            for line in dep_lines
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
        raise AssertionError(
            "modules.dep does not map virtio_snd to the packaged ALSA core"
        )
    pcm_line = next(
        (
            line
            for line in dep_lines
            if line.startswith("kernel/sound/core/snd-pcm.ko:")
        ),
        None,
    )
    if pcm_line is None or "kernel/sound/core/snd-timer.ko" not in pcm_line:
        raise AssertionError(
            "modules.dep does not map snd-pcm to the packaged snd-timer"
        )
    print(
        "[inputs] manifest and initramfs module hashes verified: "
        + ", ".join(f"{name}={expected[name]}" for name in MODULE_PATHS),
        flush=True,
    )


def assert_kernel_diagnostics(text: str) -> list[str]:
    """Return kernel failure lines, including sanitizer and warning reports."""
    prefixes = (
        "BUG:",
        "WARNING:",
        "KASAN:",
        "Oops:",
        "Kernel panic - not syncing:",
        "Internal error:",
        "Unable to handle kernel",
    )
    return [
        line.strip()
        for line in text.splitlines()
        if any(prefix in line for prefix in prefixes)
    ]
