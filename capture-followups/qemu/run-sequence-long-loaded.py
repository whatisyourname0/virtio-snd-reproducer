#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run a long loaded multichannel capture and verify its reopen."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SUPPORT = Path(__file__).resolve().parents[1] / "support"
VALIDATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUPPORT))
sys.path.insert(0, str(VALIDATION))
from ArtifactInputs import ArtifactInputs
from KernelDiagnostics import KernelDiagnostics
from qmp import QMP
from serial_log import SerialLog

from oracle.SequenceOracle import SequenceOracle

SEED = SequenceOracle.DEFAULT_SEED


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_sha256(
    *, channels: int, epoch: int, start_frame: int, frame_count: int, seed: int
) -> str:
    digest = hashlib.sha256()
    for chunk in SequenceOracle.iter_pcm_chunks(
        channels=channels,
        epoch=epoch,
        start_frame=start_frame,
        count=frame_count,
        seed=seed,
    ):
        digest.update(chunk)
    return digest.hexdigest()


def exact_segment(text: str, begin: str, end: str) -> str:
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == begin]
    ends = [index for index, line in enumerate(lines) if line.strip() == end]
    if not starts or not ends or ends[-1] <= starts[-1]:
        raise RuntimeError(f"missing anchored markers {begin!r}/{end!r}")
    return "\n".join(lines[starts[-1] + 1 : ends[-1]])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cpus", type=int, choices=(1, 2), default=1)
    parser.add_argument("--memory-mib", type=int, default=512)
    parser.add_argument("--channels", type=int, choices=(4, 8), default=8)
    parser.add_argument("--long-duration", type=int, default=70)
    parser.add_argument("--reopen-duration", type=int, default=10)
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=SEED)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Optional JSON SHA256 pins for qemu, kernel and initrd",
    )
    parser.add_argument("--ready-marker", default="VZ_HIGHLOAD_GUEST_READY=1")
    parser.add_argument("--append", default="console=hvc0")
    args = parser.parse_args()
    ArtifactInputs.validate_arguments(args)
    return args


def cpu_values(snapshot: str) -> dict[str, list[int]]:
    values: dict[str, list[int]] = {}
    for line in snapshot.splitlines():
        match = re.match(r"^(cpu\d*|cpu)\s+([0-9 ]+)$", line)
        if match:
            values[match.group(1)] = [int(part) for part in match.group(2).split()]
    return values


def main() -> int:
    args = parse_args()
    qemu = args.qemu.resolve(strict=True)
    kernel = args.kernel.resolve(strict=True)
    initrd = args.initrd.resolve(strict=True)
    kernel_sha256 = sha256_file(kernel)
    initrd_sha256 = sha256_file(initrd)
    ArtifactInputs.verify(args.manifest, qemu=qemu, kernel=kernel, initrd=initrd)
    if args.out.exists() or args.out.is_symlink():
        raise ValueError(f"refusing to overwrite output directory: {args.out}")
    args.out.mkdir(parents=True)
    qmp_directory = Path(tempfile.mkdtemp(prefix="sndq-"))
    qmp_path = qmp_directory / "q.sock"
    argv = [
        str(qemu),
        "-machine",
        "virt",
        "-nic",
        "none",
        "-accel",
        "tcg",
        "-cpu",
        "cortex-a57",
        "-smp",
        str(args.cpus),
        "-m",
        f"{args.memory_mib}M",
        "-kernel",
        str(kernel),
        "-initrd",
        str(initrd),
        "-append",
        args.append,
        "-object",
        "rng-random,id=rng0,filename=/dev/urandom",
        "-device",
        "virtio-rng-device,rng=rng0",
        "-device",
        "virtio-serial-device",
        "-chardev",
        "stdio,id=console0,signal=off",
        "-device",
        "virtconsole,chardev=console0,name=console.0",
        "-audiodev",
        "none,id=snd",
        "-device",
        "virtio-sound-pci,id=snd0,streams=2,chmaps=0,audiodev=snd",
        "-display",
        "none",
        "-monitor",
        "none",
        "-qmp",
        f"unix:{qmp_path},server=on,wait=off",
        "-no-reboot",
    ]
    sequence_config = f"sequence,count=4096,epoch=0,frame=0,seed=0x{args.seed:016x}"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("QEMU_VIRTIO_SND_")
    }
    environment["QEMU_VIRTIO_SND_CAPTURE_TEST"] = sequence_config
    inputs = {
        "qemu": str(qemu),
        "qemu_sha256": sha256_file(qemu),
        "kernel": str(kernel),
        "kernel_sha256": kernel_sha256,
        "initrd": str(initrd),
        "initrd_sha256": initrd_sha256,
        "manifest": str(args.manifest.resolve()) if args.manifest else None,
        "accel": "tcg",
        "cpus": args.cpus,
        "memory_mib": args.memory_mib,
        "backend": "QEMU none timed backend; paced audio_be_read bytes replaced with S16 sequence",
        "sequence_config": sequence_config,
        "capture_schedule_seconds": [args.long_duration, args.reopen_duration],
        "seed": args.seed,
        "capture_formats": {
            "rate": 48000,
            "format": "S16_LE",
            "channels": [args.channels],
        },
        "expected_sequence_descriptors": 4096,
        "cells": [
            f"{args.channels}ch x one guest yes worker x {args.long_duration}s, then reopen"
        ],
    }
    (args.out / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    (args.out / "qemu-argv.json").write_text(json.dumps(argv, indent=2) + "\n")

    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        env=environment,
    )
    assert process.stdin is not None and process.stdout is not None
    guest_stdin = process.stdin
    serial = SerialLog(process.stdout, args.out / "serial.log")
    qmp: QMP | None = None
    wall_start = time.monotonic()
    result: dict[str, Any] = {"inputs": inputs, "cells": []}

    def guest_command(label: str, command: str, timeout: float = 60) -> str:
        begin, end = f"__HASH_BEGIN_{label}__", f"__HASH_END_{label}__"
        before = len(serial.text)
        guest_stdin.write(f"echo {begin}; {command}; echo {end}\n".encode())
        guest_stdin.flush()
        serial.wait_for_match(re.compile(rf"(?m)^{re.escape(end)}\r?$"), timeout)
        return exact_segment(serial.text[before:], begin, end)

    try:
        serial.wait_for_match(
            re.compile(rf"(?m)^{re.escape(args.ready_marker)}\r?$"), 120
        )
        qmp = QMP(qmp_path)
        result["guest_ready"] = True
        ready = re.search(r"release=([^\s]+)", serial.text)
        result["guest_release"] = ready.group(1) if ready else None
        result["guest_tools"] = guest_command(
            "tools",
            "command -v arecord; command -v sha256sum; cat /proc/meminfo | head -3",
        )

        channels = args.channels
        for mode, capture_duration in (
            ("cpu-load", args.long_duration),
            ("reopen", args.reopen_duration),
        ):
            label = f"{channels}ch_{mode}"
            path = f"/tmp/{label}.s16le"
            log = f"/tmp/{label}.log"
            load_start = "yes >/dev/null & load_pid=$!; " if mode == "cpu-load" else ""
            load_end = (
                "kill $load_pid 2>/dev/null; wait $load_pid 2>/dev/null; "
                if mode == "cpu-load"
                else ""
            )
            command = (
                f"echo CELL_PRE_BEGIN_{label}; cat /proc/stat; cat /proc/interrupts; "
                f"echo CELL_PRE_END_{label}; {load_start}"
                f"arecord -v -D hw:0,0 -t raw -f S16_LE -r 48000 -c {channels} "
                f"--buffer-size=4096 --period-size=1024 -d {capture_duration} {path} >{log} 2>&1; "
                f"rc=$?; {load_end}echo CAPTURE_RC_{label}:$rc; "
                f"echo CAPTURE_BYTES_{label}:$(wc -c <{path}); "
                f"echo CELL_POST_BEGIN_{label}; cat /proc/stat; cat /proc/interrupts; "
                f"echo CELL_POST_END_{label}; "
                f"echo CAPTURE_SHA256_{label}:$(sha256sum {path} | cut -d ' ' -f1); "
                f"cat {log}"
            )
            started = time.monotonic()
            transcript = guest_command(label, command, capture_duration * 4 + 60)
            elapsed = time.monotonic() - started
            rc_match = re.search(rf"CAPTURE_RC_{label}:(-?\d+)", transcript)
            byte_match = re.search(rf"CAPTURE_BYTES_{label}:(\d+)", transcript)
            hash_match = re.search(
                rf"CAPTURE_SHA256_{label}:([0-9a-fA-F]{{64}})", transcript
            )
            pre = exact_segment(
                transcript, f"CELL_PRE_BEGIN_{label}", f"CELL_PRE_END_{label}"
            )
            post = exact_segment(
                transcript, f"CELL_POST_BEGIN_{label}", f"CELL_POST_END_{label}"
            )
            guest_bytes = int(byte_match.group(1)) if byte_match else None
            frame_bytes = channels * 2
            frames_from_guest_size = (
                guest_bytes // frame_bytes if guest_bytes is not None else None
            )
            cell = {
                "label": label,
                "channels": channels,
                "mode": mode,
                "capture_duration_seconds": capture_duration,
                "repetition": 1,
                "host_command_wall_seconds_including_stats_and_log": elapsed,
                "returncode": int(rc_match.group(1)) if rc_match else None,
                "guest_bytes": guest_bytes,
                "frames_from_guest_file_length": frames_from_guest_size,
                "guest_sha256": hash_match.group(1).lower() if hash_match else None,
                "guest_cpu_before": cpu_values(pre),
                "guest_cpu_after": cpu_values(post),
                "guest_interrupts_before": [
                    line
                    for line in pre.splitlines()
                    if "virtio" in line.lower() or "snd" in line.lower()
                ],
                "guest_interrupts_after": [
                    line
                    for line in post.splitlines()
                    if "virtio" in line.lower() or "snd" in line.lower()
                ],
                "alsa_log": transcript[
                    transcript.find(f"CELL_POST_END_{label}")
                    + len(f"CELL_POST_END_{label}") :
                ].strip(),
            }
            cell["alsa_reports_xrun"] = bool(
                re.search(r"overrun|underrun|xrun", cell["alsa_log"], re.IGNORECASE)
            )
            result["cells"].append(cell)

        start_lines = [
            line
            for line in serial.text.splitlines()
            if "VIRTIO_SND_SEQUENCE_START" in line
        ]
        stop_lines = [
            line
            for line in serial.text.splitlines()
            if "VIRTIO_SND_SEQUENCE_STOP" in line
        ]
        errors = [
            line
            for line in serial.text.splitlines()
            if "VIRTIO_SND_SEQUENCE_INVALID" in line
            or "VIRTIO_SND_SEQUENCE_EXHAUSTED" in line
        ]
        result["sequence_start_lines"] = start_lines
        result["sequence_stop_lines"] = stop_lines
        result["sequence_errors"] = errors
        if len(start_lines) == len(result["cells"]) and len(stop_lines) == len(
            result["cells"]
        ):
            for cell, start_line, stop_line in zip(
                result["cells"], start_lines, stop_lines
            ):
                start_match = re.search(r"epoch=(\d+) first_frame=(\d+)", start_line)
                stop_match = re.search(
                    r"frames=(\d+) descriptors=(\d+) trailing_bytes=(\d+) remaining=(\d+) failed=(\d+)",
                    stop_line,
                )
                if not start_match or not stop_match:
                    cell["oracle_status"] = "FAIL"
                    cell["oracle_error"] = "missing QEMU sequence coordinates/counters"
                    continue
                epoch, first_frame = (
                    int(start_match.group(1)),
                    int(start_match.group(2)),
                )
                frames, descriptors, trailing, remaining, failed = map(
                    int, stop_match.groups()
                )
                frame_bytes = cell["channels"] * 2
                frame_count = cell["frames_from_guest_file_length"]
                cell.update(
                    {
                        "epoch": epoch,
                        "start_frame": first_frame,
                        "qemu_sequence_frames_generated": frames,
                        "qemu_sequence_descriptors_completed": descriptors,
                        "qemu_sequence_trailing_bytes": trailing,
                        "sequence_budget_remaining_after_stop": remaining,
                        "sequence_failed_flag": failed,
                        "oracle_expected_sha256": expected_sha256(
                            channels=cell["channels"],
                            epoch=epoch,
                            start_frame=first_frame,
                            frame_count=frame_count,
                            seed=args.seed,
                        )
                        if frame_count
                        else None,
                    }
                )
                nominal_frames = cell["capture_duration_seconds"] * 48000
                cell["nominal_expected_frames"] = nominal_frames
                cell["size_ok"] = (
                    cell["returncode"] == 0
                    and cell["guest_bytes"] == nominal_frames * cell["channels"] * 2
                )
                cell["oracle_status"] = (
                    "PASS"
                    if cell["guest_sha256"] == cell["oracle_expected_sha256"]
                    else "FAIL"
                )
                cell["state_ok"] = trailing == 0 and failed == 0 and remaining > 0
                cpu_before = cell["guest_cpu_before"].get("cpu", [])
                cpu_after = cell["guest_cpu_after"].get("cpu", [])
                if len(cpu_before) == len(cpu_after) and cpu_before:
                    cell["guest_total_jiffies_delta"] = sum(cpu_after) - sum(cpu_before)
                    cell["guest_user_jiffies_delta"] = cpu_after[0] - cpu_before[0]
                    cell["guest_system_jiffies_delta"] = cpu_after[2] - cpu_before[2]

        result["qemu_process_after"] = subprocess.run(
            ["ps", "-p", str(process.pid), "-o", "pid=,pcpu=,rss=,etime=,time="],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        ).stdout.strip()
        try:
            result["qmp_audio_device"] = qmp.command("query-audiodevs")
        except (OSError, RuntimeError, ValueError, TimeoutError) as error:
            result["qmp_audio_device_error"] = repr(error)
        result["wall_seconds_excluding_serial_pcm_export"] = (
            time.monotonic() - wall_start
        )
        try:
            qmp.command("quit")
        except (OSError, RuntimeError, ValueError, TimeoutError):
            process.terminate()
        qmp.close()
        qmp = None
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        serial.finish(timeout=10)
        serial.close_file()
        result["kernel_diagnostics"] = KernelDiagnostics.find(serial.text)

        result["passed"] = (
            len(result["cells"]) == 2
            and len(start_lines) == 2
            and len(stop_lines) == 2
            and not errors
            and not result["kernel_diagnostics"]
            and all(
                cell.get("size_ok")
                and cell.get("oracle_status") == "PASS"
                and cell.get("state_ok")
                and not cell.get("alsa_reports_xrun")
                for cell in result["cells"]
            )
        )
        (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "passed": result["passed"],
                    "cpus": args.cpus,
                    "capture_schedule_seconds": [
                        args.long_duration,
                        args.reopen_duration,
                    ],
                    "cell_results": [
                        {
                            key: cell.get(key)
                            for key in (
                                "label",
                                "returncode",
                                "guest_bytes",
                                "frames_from_guest_file_length",
                                "guest_sha256",
                                "oracle_expected_sha256",
                                "oracle_status",
                                "size_ok",
                                "state_ok",
                                "alsa_reports_xrun",
                                "epoch",
                                "start_frame",
                                "qemu_sequence_descriptors_completed",
                                "sequence_budget_remaining_after_stop",
                                "host_command_wall_seconds_including_stats_and_log",
                                "guest_total_jiffies_delta",
                                "guest_system_jiffies_delta",
                            )
                        }
                        for cell in result["cells"]
                    ],
                    "sequence_errors": errors,
                    "kernel_diagnostics": result["kernel_diagnostics"],
                },
                indent=2,
            )
        )
        return 0 if result["passed"] else 1
    finally:
        if qmp is not None:
            try:
                qmp.command("quit")
            except (OSError, RuntimeError, ValueError, TimeoutError):
                process.terminate()
            qmp.close()
        elif process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        serial.finish(timeout=10)
        serial.close_file()
        qmp_path.unlink(missing_ok=True)
        try:
            qmp_directory.rmdir()
        except OSError:
            pass
        (args.out / "runtime.json").write_text(
            json.dumps(
                {
                    "qemu_exit_code": process.returncode,
                    "wall_seconds": time.monotonic() - wall_start,
                },
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    raise SystemExit(main())
