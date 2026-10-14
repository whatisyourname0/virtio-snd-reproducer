#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run one delayed PCM-control timeout and reprobe-recovery case."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fsm_oracle import (
    validate_reader_result,
    validate_reprobe_rejection,
    validate_timeout_result,
)
from runtime import read_newc_members, sha256, validate_packaged_modules

FAULTS = {
    "stop": (
        "stop",
        1,
        ("set_params", "prepare", "prepare", "start", "stop"),
        "FSM_PAUSE_PUSH_RETURN",
    ),
    "release": (
        "release",
        1,
        ("set_params", "prepare", "prepare", "start", "stop", "release"),
        "FSM_PAUSE_PUSH_RETURN",
    ),
    "prepare": (
        "prepare",
        3,
        ("set_params", "prepare", "prepare", "start", "stop", "release", "prepare"),
        "FSM_PAUSE_RELEASE_RETURN",
    ),
    "start": (
        "start",
        2,
        (
            "set_params",
            "prepare",
            "prepare",
            "start",
            "stop",
            "release",
            "prepare",
            "start",
        ),
        "FSM_PAUSE_RELEASE_RETURN",
    ),
}
DELAY_MS = 1500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=tuple(FAULTS))
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument(
        "--kernel", type=Path, required=True, help="matching ARM64 Image"
    )
    parser.add_argument(
        "--initrd", type=Path, required=True, help="lifecycle initramfs"
    )
    parser.add_argument(
        "--modules",
        type=Path,
        required=True,
        help="matching lib/modules/<release> directory",
    )
    parser.add_argument("--release", required=True, help="exact kernel release string")
    parser.add_argument(
        "--bios-dir", type=Path, required=True, help="QEMU pc-bios directory"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--accel", choices=("tcg", "hvf"), default="tcg")
    parser.add_argument(
        "--timeout", type=int, default=180, help="QEMU wall timeout in seconds"
    )
    return parser.parse_args()


def require_files(args: argparse.Namespace) -> dict[str, str]:
    paths = {
        "qemu": args.qemu,
        "kernel": args.kernel,
        "initrd": args.initrd,
        "modules": args.modules,
        "bios_dir": args.bios_dir,
    }
    for label, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"missing {label}: {path}")
    if (
        not args.qemu.is_file()
        or not args.kernel.is_file()
        or not args.initrd.is_file()
    ):
        raise ValueError("QEMU, kernel, and initrd inputs must be files")
    if not args.modules.is_dir() or not args.bios_dir.is_dir():
        raise ValueError("modules and bios-dir inputs must be directories")
    if args.timeout < 1:
        raise ValueError("--timeout must be positive")

    version = subprocess.run(
        [str(args.qemu), "-version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.splitlines()[0]
    if "11.1.2" not in version:
        raise ValueError(f"expected QEMU 11.1.2, got: {version}")
    module_hashes = validate_packaged_modules(args.modules, args.release, args.initrd)
    contents = {name: [] for name in ("fsm_control_probe", "pause-rxflush-probe")}
    for name, data in read_newc_members(args.initrd):
        if name in contents:
            contents[name].append(data)
    if any(len(items) != 1 for items in contents.values()):
        raise AssertionError(
            f"initrd lifecycle probe members are incomplete: {contents.keys()}"
        )
    return {
        "qemu_version": version,
        "qemu_sha256": sha256(args.qemu),
        "kernel_sha256": sha256(args.kernel),
        "initrd_sha256": sha256(args.initrd),
        "virtio_snd_sha256": module_hashes[
            f"lib/modules/{args.release}/kernel/sound/virtio/virtio_snd.ko"
        ],
        "snd_pcm_sha256": module_hashes[
            f"lib/modules/{args.release}/kernel/sound/core/snd-pcm.ko"
        ],
        "snd_sha256": module_hashes[
            f"lib/modules/{args.release}/kernel/sound/core/snd.ko"
        ],
        "snd_timer_sha256": module_hashes[
            f"lib/modules/{args.release}/kernel/sound/core/snd-timer.ko"
        ],
    }


def run() -> None:
    args = parse_args()
    target, ordinal, expected_prefix, _timeout_marker = FAULTS[args.mode]
    hashes = require_files(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    serial_path = args.output_dir / f"{args.mode}.serial.log"
    host_path = args.output_dir / f"{args.mode}.host.log"
    if serial_path.exists() or host_path.exists():
        raise FileExistsError(f"refusing to overwrite evidence for {args.mode}")

    cpu = "host" if args.accel == "hvf" else "max"
    argv = [
        str(args.qemu),
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
        str(args.kernel),
        "-initrd",
        str(args.initrd),
        "-L",
        str(args.bios_dir),
        "-append",
        (
            f"console=ttyAMA0 earlycon=pl011,0x09000000 loglevel=7 "
            f"panic_on_warn=1 oops=panic panic=-1 fsm.mode={args.mode}"
        ),
        "-audiodev",
        "none,id=snd",
        "-device",
        "virtio-sound-pci,id=snd0,streams=2,audiodev=snd",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        f"file:{serial_path}",
        "-no-reboot",
    ]
    env = os.environ.copy()
    for name in tuple(env):
        if name.startswith("QEMU_VIRTIO_SND_") or name == "FSM_QEMU_BINARY":
            env.pop(name, None)
    env["QEMU_VIRTIO_SND_CTRL_TEST"] = f"{target},{ordinal},{DELAY_MS}"

    print(f"MODE={args.mode}")
    print(f"CONTROL_INJECTION={target},{ordinal},{DELAY_MS}ms")
    for label, path in (
        ("QEMU", args.qemu),
        ("KERNEL", args.kernel),
        ("INITRD", args.initrd),
        ("MODULES", args.modules),
        ("BIOS_DIR", args.bios_dir),
    ):
        print(f"{label}={path.resolve()}")
    for label, digest in hashes.items():
        print(f"{label.upper()}={digest}")

    timed_out = False
    with host_path.open("xb") as host_log:
        try:
            result = subprocess.run(
                argv,
                stdout=host_log,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=args.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            result = None
    serial = serial_path.read_text(errors="replace") if serial_path.exists() else ""
    host = host_path.read_text(errors="replace")
    print(f"QEMU_RC={None if result is None else result.returncode}")
    for line in serial.splitlines():
        if any(
            token in line
            for token in (
                "FSM_",
                "device reprobe required",
                "BUG:",
                "KASAN:",
                "WARNING:",
                "Kernel panic",
                "Oops:",
            )
        ):
            print(line)
    for line in host.splitlines():
        if any(
            token in line
            for token in (
                "VIRTIO_SND_CTRL",
                "control-test enabled",
                "BUG:",
                "KASAN:",
                "WARNING:",
                "Kernel panic",
                "Oops:",
            )
        ):
            print(f"HOST: {line}")

    required = (
        f"FSM_BOOT mode={args.mode} release={args.release}",
        f"FSM_TEST_BEGIN mode={args.mode}",
        "FSM_READER_BLOCKED ",
        "FSM_READER_ASSERT ",
        "FSM_PREPARE_WITHOUT_REPROBE rc=",
        f"FSM_CONTROL_CASE_PASS mode={args.mode}",
        f"FSM_LATE_RESPONSE_GRACE_BEGIN mode={args.mode} seconds=1",
        f"FSM_LATE_RESPONSE_GRACE_END mode={args.mode}",
        f"FSM_REPROBE_RECOVERY_PASS mode={args.mode}",
        f"FSM_CASE_COMPLETE mode={args.mode}",
    )
    missing = [token for token in required if token not in serial]
    for validate in (
        validate_timeout_result,
        validate_reader_result,
    ):
        try:
            validate(serial, args.mode)
        except AssertionError as error:
            missing.append(str(error))
    try:
        validate_reprobe_rejection(serial)
    except AssertionError as error:
        missing.append(str(error))
    if serial.count("device reprobe required") < 1:
        missing.append("device reprobe required")
    if args.mode in ("prepare", "start") and "FSM_READER_BLOCKED_PAUSED " not in serial:
        missing.append("reader blocked while PAUSED")

    delay_begin = "VIRTIO_SND_CTRL_DELAY_BEGIN sequence="
    if (
        host.count(delay_begin) != 1
        or f"command={target} delay_ms={DELAY_MS}" not in host
    ):
        missing.append("exactly one delayed target control")
    if (
        host.count("VIRTIO_SND_CTRL_DELAY_END sequence=") != 1
        or f"command={target}" not in host
    ):
        missing.append("delayed control completion")
    commands = [
        match.group(1)
        for match in re.finditer(
            r"VIRTIO_SND_CTRL sequence=\d+ command=([a-z_]+) ordinal=\d+", host
        )
    ]
    if tuple(commands[: len(expected_prefix)]) != expected_prefix:
        missing.append(f"control prefix {expected_prefix}, got {commands}")
    elif (
        len(commands) <= len(expected_prefix)
        or commands[len(expected_prefix)] != "set_params"
    ):
        missing.append(f"no control command between failure and reprobe: {commands}")

    errors = (
        "BUG:",
        "KASAN:",
        "WARNING:",
        "Kernel panic",
        "Oops:",
        "FSM_FAIL ",
        "LIFECYCLE_INIT_FAIL ",
    )
    found = [token for token in errors if token in serial or token in host]
    if timed_out or result is None or result.returncode != 0 or missing or found:
        if timed_out:
            print(f"QEMU_TIMEOUT={args.timeout}s raw logs preserved")
        print(f"FAIL_MISSING={missing}")
        print(f"FAIL_DIAGNOSTIC={found}")
        raise SystemExit(1)
    print(f"SNAPSHOT3_FSM_CONTROL_PASS mode={args.mode}")
    print(f"SERIAL_LOG={serial_path.resolve()}")
    print(f"HOST_LOG={host_path.resolve()}")


if __name__ == "__main__":
    run()
