#!/usr/bin/env python3
"""Run the isolated linked-drain and recovery regression on a fresh kernel."""

import hashlib
import os
import pathlib
import subprocess
import sys


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: run-linked-regression.py QEMU KERNEL_ARTIFACTS FIXTURE_OUTPUT BIOS_DIR"
        )
    qemu = pathlib.Path(sys.argv[1]).resolve()
    artifacts = pathlib.Path(sys.argv[2]).resolve()
    fixture = pathlib.Path(sys.argv[3]).resolve()
    romdir = pathlib.Path(sys.argv[4]).resolve()
    accel = "tcg"
    kernel = artifacts / "Image"
    image = fixture / "initrd.cpio.gz"
    manifest = fixture / "initrd.sha256"
    serial = fixture / "linked-drain.serial.log"
    host = fixture / "linked-drain.host.log"
    release = (artifacts / "kernel-release.txt").read_text().strip()
    pcm_module = (
        fixture / "rootfs/lib/modules" / release / "kernel/sound/core/snd-pcm.ko"
    )
    virtio_module = (
        fixture / "rootfs/lib/modules" / release / "kernel/sound/virtio/virtio_snd.ko"
    )
    for label, path in (
        ("QEMU", qemu),
        ("ROMDIR", romdir),
        ("KERNEL", kernel),
        ("INITRD", image),
        ("INITRD_MANIFEST", manifest),
        ("SND_PCM", pcm_module),
        ("VIRTIO_SND", virtio_module),
    ):
        if not path.exists():
            raise SystemExit(f"missing {label}: {path}")
    manifest_fields = manifest.read_text().split()
    if len(manifest_fields) != 2 or manifest_fields[1] != image.name:
        raise SystemExit("invalid initrd.sha256 manifest")
    if manifest_fields[0] != sha256(image):
        raise SystemExit("initrd SHA-256 does not match initrd.sha256")
    release_module_tree = artifacts / "modules/lib/modules" / release
    for relative in (
        pathlib.Path("kernel/sound/core/snd-pcm.ko"),
        pathlib.Path("kernel/sound/virtio/virtio_snd.ko"),
    ):
        packaged = fixture / "rootfs/lib/modules" / release / relative
        built = release_module_tree / relative
        if sha256(packaged) != sha256(built):
            raise SystemExit(f"initrd module does not match kernel artifacts: {relative}")
    if serial.exists() or host.exists():
        raise SystemExit("refuse to overwrite prior QEMU evidence")

    args = [
        str(qemu),
        "-machine",
        "virt",
        "-accel",
        accel,
        "-cpu",
        "max",
        "-smp",
        "2",
        "-m",
        "2048",
        "-kernel",
        str(kernel),
        "-initrd",
        str(image),
        "-L",
        str(romdir),
        "-append",
        "console=ttyAMA0 panic_on_warn=1 oops=panic panic=-1",
        "-audiodev",
        "none,id=snd",
        "-device",
        "virtio-sound-pci,streams=2,audiodev=snd",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        f"file:{serial}",
        "-no-reboot",
    ]
    env = os.environ.copy()
    for name in tuple(env):
        if name.startswith("QEMU_VIRTIO_SND_") or name == "FSM_QEMU_BINARY":
            env.pop(name, None)
    print("CASE=linked-drain-kasan-lockdep-later-member")
    print(f"QEMU_ACCEL={accel}")
    print("QEMU_CPU=max")
    for label, path in (
        ("QEMU", qemu),
        ("KERNEL", kernel),
        ("INITRD", image),
        ("INITRD_MANIFEST", manifest),
        ("SND_PCM", pcm_module),
        ("VIRTIO_SND", virtio_module),
    ):
        print(f"{label}={path}")
        print(f"{label}_SHA256={sha256(path)}")
    print(f"INITRD_MANIFEST_SHA256={manifest_fields[0]}")
    try:
        with host.open("xb") as host_log:
            result = subprocess.run(
                args,
                stdout=host_log,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=90,
                check=False,
            )
    except subprocess.TimeoutExpired:
        print("QEMU_TIMEOUT=90s; raw logs preserved")
        raise SystemExit(1)

    serial_text = serial.read_text(errors="replace")
    host_text = host.read_text(errors="replace")
    print(f"QEMU_RC={result.returncode}")
    markers = (
        "ZERO_DRAIN",
        "LINKED_XRUN",
        "POINTER",
        "SYNTHETIC",
        "CONFIG rate=",
        "ARMED_POINTER_COUNTS",
        "KASAN:",
        "BUG:",
        "WARNING:",
        "Kernel panic",
        "Oops:",
    )
    for line in serial_text.splitlines():
        if any(marker in line for marker in markers):
            print(line)
    for line in host_text.splitlines():
        if any(
            marker in line
            for marker in ("KASAN:", "BUG:", "WARNING:", "Kernel panic", "Oops:")
        ):
            print(f"HOST: {line}")

    required = (
        "ZERO_DRAIN_VERDICT=PASS linkage=unlinked",
        "ZERO_DRAIN_VERDICT=PASS linkage=linked",
        "LINKED_XRUN_DRAIN_VERDICT=PASS direct_error=EPIPE pointers=1,1",
        "LINKED_XRUN_PASS",
        "SYNTHETIC_CAPTURE_RECOVERY_PASS",
        "SYNTHETIC_PLAYBACK_PASS",
        "SYNTHETIC_COMPLETE",
    )
    missing = [marker for marker in required if marker not in serial_text]
    errors = (
        "KASAN:",
        "BUG:",
        "WARNING:",
        "Kernel panic",
        "Oops:",
        "possible circular locking dependency",
        "inconsistent lock state",
        "scheduling while atomic",
        "possible recursive locking",
        "DEBUG_LOCKS_WARN_ON",
        "turning off the locking correctness validator",
    )
    found = [
        marker for marker in errors if marker in serial_text or marker in host_text
    ]
    if result.returncode != 0 or missing or found:
        print(f"FAIL_MISSING={missing}")
        print(f"FAIL_DIAGNOSTIC={found}")
        raise SystemExit(1)
    print("LINKED_QEMU_REGRESSION_PASS")


if __name__ == "__main__":
    main()
