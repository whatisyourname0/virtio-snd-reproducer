#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run the synthetic short-packed capture + no-query drain QEMU case."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

CASES = {
    "after0": ("0", "1023", "20d61b4edef6d535"),
    "after1": ("1", "2047", "af2de207e4d27535"),
    "drop": ("0", "0", "cbf29ce484222325"),
}
BAD = ("KASAN:", "BUG:", "WARNING:", "Kernel panic", "Oops:")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, default="after1")
    parser.add_argument("--qemu", type=Path, required=True, help="qemu-system-aarch64")
    parser.add_argument("--kernel", type=Path, required=True, help="ARM64 Linux Image")
    parser.add_argument(
        "--initrd", type=Path, required=True, help="packed guest initrd"
    )
    parser.add_argument(
        "--bios-dir", type=Path, help="QEMU firmware directory (optional)"
    )
    parser.add_argument("--accel", choices=("tcg", "hvf"), default="tcg")
    parser.add_argument(
        "--icount-shift",
        type=int,
        choices=range(11),
        help="optional TCG instruction clock: fixed shift, sleep=off (not wall-time performance)",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("qemu-evidence"))
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    if args.icount_shift is not None and args.accel != "tcg":
        parser.error("--icount-shift requires --accel tcg")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    qemu, kernel, initrd = (
        p.resolve(strict=True) for p in (args.qemu, args.kernel, args.initrd)
    )
    bios = args.bios_dir.resolve(strict=True) if args.bios_dir else None
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    serial = output / f"short-packed-{args.case}.serial.log"
    host = output / f"short-packed-{args.case}.host.log"
    if serial.exists() or host.exists():
        raise SystemExit(f"refusing to overwrite existing evidence under {output}")

    after, frames, fnv = CASES[args.case]
    cmdline = (
        "console=ttyAMA0 panic_on_warn=1 oops=panic panic=-1 "
        f"probe.frames={frames} probe.hash={fnv}"
        + (" probe.action=drop" if args.case == "drop" else "")
    )
    cpu = "host" if args.accel == "hvf" else "max"
    command = [
        str(qemu),
        "-machine",
        "virt",
        "-accel",
        args.accel,
        "-cpu",
        cpu,
        "-smp",
        "2",
        "-m",
        "2048",
        "-kernel",
        str(kernel),
        "-initrd",
        str(initrd),
        "-append",
        cmdline,
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
    if bios:
        command[command.index("-append") : command.index("-append")] = ["-L", str(bios)]

    if args.icount_shift is not None:
        command.extend(["-icount", f"shift={args.icount_shift},sleep=off"])

    env = os.environ.copy()
    for key in list(env):
        if key.startswith("QEMU_VIRTIO_SND_") or key == "FSM_QEMU_BINARY":
            env.pop(key, None)
    env.update(
        {
            "QEMU_VIRTIO_SND_CAPTURE_TEST": f"short-packed,after={after},count=1",
            "QEMU_VIRTIO_SND_CAPTURE_TEST_DELAY_MS": "40",
            "QEMU_VIRTIO_SND_CAPTURE_TEST_PAUSE_AFTER": "1",
        }
    )
    with host.open("x", encoding="utf-8") as log:
        print(f"CASE={args.case}")
        for label, path in (("QEMU", qemu), ("KERNEL", kernel), ("INITRD", initrd)):
            print(f"{label}={path}")
            print(f"{label}_SHA256={sha256(path)}")
        print("COMMAND=" + " ".join(command))
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=args.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(f"QEMU_TIMEOUT={args.timeout}s; logs preserved")
            return 1
        finally:
            print(f"HOST_WALL_SECONDS={time.monotonic() - started:.3f}")

    guest_text = serial.read_text(errors="replace") if serial.exists() else ""
    host_text = host.read_text(errors="replace")
    expected = {
        "after0": "DRAIN_NO_QUERY_VERDICT=PASS action=drain frames=1023 fnv1a64=20d61b4edef6d535",
        "after1": "DRAIN_NO_QUERY_VERDICT=PASS action=drain frames=2047 fnv1a64=af2de207e4d27535",
        "drop": "DRAIN_NO_QUERY_VERDICT=PASS action=drop frames=0 fnv1a64=cbf29ce484222325",
    }[args.case]
    guest_markers = (
        "NO_QUERY_PROBE_RC=0",
        "NO_QUERY_RECOVERY_PASS",
        "NO_QUERY_PLAYBACK_PASS",
        "NO_QUERY_COMPLETE",
        expected,
    )
    host_markers = (
        "capture-test delay enabled: mode=short-packed delay_ms=40",
        f"capture-test pause-after enabled: mode=short-packed after={after} count=1 delay_ms=40",
        "capture-test reply: mode=short-packed",
        "capture-test RX paused after delayed reply:",
    )
    missing = [marker for marker in guest_markers if marker not in guest_text]
    missing.extend(marker for marker in host_markers if marker not in host_text)
    diagnostics = [
        marker for marker in BAD if marker in guest_text or marker in host_text
    ]
    print(f"QEMU_RC={result.returncode}")
    print(f"GUEST_LOG={serial}")
    print(f"HOST_LOG={host}")
    if result.returncode or missing or diagnostics:
        print(f"FAIL_MISSING={missing}")
        print(f"FAIL_DIAGNOSTIC={diagnostics}")
        return 1
    print(f"SHORT_PACKED_NO_QUERY_DRAIN_PASS case={args.case}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
