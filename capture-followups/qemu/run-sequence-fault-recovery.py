#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Inject an arecord reader stall, then verify a clean paced reopen by hash."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
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


def expected_sha256(
    channels: int, epoch: int, start_frame: int, frames: int, seed: int
) -> str:
    digest = hashlib.sha256()
    for chunk in SequenceOracle.iter_pcm_chunks(
        channels=channels,
        epoch=epoch,
        start_frame=start_frame,
        count=frames,
        seed=seed,
    ):
        digest.update(chunk)
    return digest.hexdigest()


def segment(text: str, begin: str, end: str) -> str:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == begin]
    ends = [i for i, line in enumerate(lines) if line.strip() == end]
    if not starts or not ends or ends[-1] <= starts[-1]:
        raise RuntimeError(f"missing anchored markers {begin!r}/{end!r}")
    return "\n".join(lines[starts[-1] + 1 : ends[-1]])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cpus", type=int, choices=(1, 2), default=1)
    parser.add_argument("--memory-mib", type=int, default=512)
    parser.add_argument("--channels", type=int, choices=(4, 8), required=True)
    parser.add_argument("--stall-seconds", type=float, default=0.25)
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument(
        "--seed", type=lambda value: int(value, 0), default=SequenceOracle.DEFAULT_SEED
    )
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
    qmp_directory = Path(tempfile.mkdtemp(prefix="sndq-fault-"))
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
        "capture_channels": args.channels,
        "format": "S16_LE",
        "rate": 48000,
        "stall_seconds": args.stall_seconds,
        "buffer_frames": 4096,
        "period_frames": 1024,
        "seed": args.seed,
        "stall_exceeds_buffer_seconds": args.stall_seconds > 4096 / 48000,
        "expected_recovery_frames": args.duration * 48000,
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
    result: dict[str, Any] = {"inputs": inputs, "captures": []}

    def guest_command(label: str, command: str, timeout: float = 90) -> str:
        begin, end = f"__FAULT_BEGIN_{label}__", f"__FAULT_END_{label}__"
        before = len(serial.text)
        # A fresh noninteractive shell keeps SIGSTOP job notifications from
        # satisfying the interactive console shell's wait before process exit.
        script = shlex.quote(command)
        guest_stdin.write(f"echo {begin}; /bin/sh -c {script}; echo {end}\n".encode())
        guest_stdin.flush()
        serial.wait_for_match(re.compile(rf"(?m)^{re.escape(end)}\r?$"), timeout)
        return segment(serial.text[before:], begin, end)

    try:
        serial.wait_for_match(
            re.compile(rf"(?m)^{re.escape(args.ready_marker)}\r?$"), 120
        )
        qmp = QMP(qmp_path)
        result["guest_ready"] = True
        result["guest_tools"] = guest_command(
            "tools",
            "command -v arecord; command -v sha256sum; command -v sleep; cat /proc/meminfo | head -3",
        )

        ch = args.channels
        fault_log = "/tmp/stall.log"
        fault_file = "/tmp/stall.s16le"
        fault_command = (
            f"arecord -v -D hw:0,0 -t raw -f S16_LE -r 48000 -c {ch} "
            f"--buffer-size=4096 --period-size=1024 -d 5 {fault_file} >{fault_log} 2>&1 & "
            f"capture_pid=$!; sleep 1; kill -STOP $capture_pid; stop_rc=$?; "
            f"sleep {args.stall_seconds}; kill -CONT $capture_pid; continue_rc=$?; "
            f"wait $capture_pid; rc=$?; echo SIGNAL_STOP_RC:$stop_rc; "
            f"echo SIGNAL_CONT_RC:$continue_rc; echo FAULT_CAPTURE_RC:$rc; "
            f"if kill -0 $capture_pid 2>/dev/null; then echo FAULT_PROCESS_ALIVE:1; "
            f"else echo FAULT_PROCESS_ALIVE:0; fi; "
            f"echo FAULT_CAPTURE_BYTES:$(wc -c <{fault_file}); "
            f"echo FAULT_CAPTURE_SHA256:$(sha256sum {fault_file} | cut -d ' ' -f1); cat {fault_log}"
        )
        started = time.monotonic()
        fault_text = guest_command("stall", fault_command, 90)
        result["fault_injection"] = {
            "command": fault_command,
            "host_wall_seconds": time.monotonic() - started,
            "transcript": fault_text,
            "stop_signal_sent": "SIGNAL_STOP_RC:0" in fault_text,
            "continue_signal_sent": "SIGNAL_CONT_RC:0" in fault_text,
            "process_exited": bool(
                re.search(r"(?m)^FAULT_PROCESS_ALIVE:0\r?$", fault_text)
            ),
            "returncode": (
                int(m.group(1))
                if (m := re.search(r"FAULT_CAPTURE_RC:(-?\d+)", fault_text))
                else None
            ),
            "guest_bytes": (
                int(m.group(1))
                if (m := re.search(r"FAULT_CAPTURE_BYTES:(\d+)", fault_text))
                else None
            ),
            "guest_sha256": (
                m.group(1).lower()
                if (
                    m := re.search(
                        r"FAULT_CAPTURE_SHA256:([0-9a-fA-F]{64})", fault_text
                    )
                )
                else None
            ),
            "alsa_reports_xrun": bool(
                re.search(r"overrun|xrun|underrun", fault_text, re.IGNORECASE)
            ),
        }
        if not result["fault_injection"]["process_exited"]:
            raise RuntimeError(
                "fault capture is still alive after wait; refusing reopen"
            )

        recovery_file = "/tmp/recovery.s16le"
        recovery_log = "/tmp/recovery.log"
        recovery_command = (
            f"echo RECOVERY_PRE_BEGIN; cat /proc/stat; cat /proc/interrupts; echo RECOVERY_PRE_END; "
            f"arecord -v -D hw:0,0 -t raw -f S16_LE -r 48000 -c {ch} "
            f"--buffer-size=4096 --period-size=1024 -d {args.duration} {recovery_file} >{recovery_log} 2>&1; "
            f"rc=$?; echo RECOVERY_RC:$rc; echo RECOVERY_BYTES:$(wc -c <{recovery_file}); "
            f"echo RECOVERY_POST_BEGIN; cat /proc/stat; cat /proc/interrupts; echo RECOVERY_POST_END; "
            f"echo RECOVERY_SHA256:$(sha256sum {recovery_file} | cut -d ' ' -f1); cat {recovery_log}"
        )
        started = time.monotonic()
        recovery_text = guest_command("recovery", recovery_command, 60)
        recovery_bytes_m = re.search(r"RECOVERY_BYTES:(\d+)", recovery_text)
        recovery_hash_m = re.search(r"RECOVERY_SHA256:([0-9a-fA-F]{64})", recovery_text)
        recovery_rc_m = re.search(r"RECOVERY_RC:(-?\d+)", recovery_text)
        guest_bytes = int(recovery_bytes_m.group(1)) if recovery_bytes_m else None
        frames = guest_bytes // (ch * 2) if guest_bytes is not None else None
        result["captures"].append(
            {
                "label": "reopen_after_stall",
                "channels": ch,
                "host_wall_seconds_including_stats_and_log": time.monotonic() - started,
                "returncode": int(recovery_rc_m.group(1)) if recovery_rc_m else None,
                "guest_bytes": guest_bytes,
                "frames_from_guest_file_length": frames,
                "guest_sha256": recovery_hash_m.group(1).lower()
                if recovery_hash_m
                else None,
                "transcript": recovery_text,
            }
        )

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
        fault = result["fault_injection"]
        fault_start_lines = [
            line
            for line in fault_text.splitlines()
            if "VIRTIO_SND_SEQUENCE_START" in line
        ]
        fault_stop_lines = [
            line
            for line in fault_text.splitlines()
            if "VIRTIO_SND_SEQUENCE_STOP" in line
        ]
        recovery_start_lines = [
            line
            for line in recovery_text.splitlines()
            if "VIRTIO_SND_SEQUENCE_START" in line
        ]
        recovery_stop_lines = [
            line
            for line in recovery_text.splitlines()
            if "VIRTIO_SND_SEQUENCE_STOP" in line
        ]
        result["fault_pcm_sequence_starts"] = fault_start_lines
        result["fault_pcm_sequence_stops"] = fault_stop_lines
        result["recovery_pcm_sequence_starts"] = recovery_start_lines
        result["recovery_pcm_sequence_stops"] = recovery_stop_lines
        result["fault_epochs"] = [
            int(m.group(1))
            for line in fault_start_lines
            if (m := re.search(r"epoch=(\d+) first_frame=(\d+)", line))
        ]
        result["fault_epoch"] = (
            result["fault_epochs"][0] if result["fault_epochs"] else None
        )
        result["fault_stop_states"] = []
        for line in fault_stop_lines:
            state_match = re.search(
                r"epoch=(\d+).*frames=(\d+) descriptors=(\d+) trailing_bytes=(\d+) remaining=(\d+) failed=(\d+)",
                line,
            )
            if state_match:
                epoch, frames_seen, descriptors, trailing, remaining, failed = map(
                    int, state_match.groups()
                )
                result["fault_stop_states"].append(
                    {
                        "epoch": epoch,
                        "frames": frames_seen,
                        "descriptors": descriptors,
                        "trailing_bytes": trailing,
                        "remaining": remaining,
                        "failed": failed,
                    }
                )

        result["recovery_epoch"] = None
        result["recovery_stop_state"] = None
        if len(recovery_start_lines) == 1 and len(recovery_stop_lines) == 1:
            start_match = re.search(
                r"epoch=(\d+) first_frame=(\d+)", recovery_start_lines[0]
            )
            stop_match = re.search(
                r"epoch=(\d+).*frames=(\d+) descriptors=(\d+) trailing_bytes=(\d+) remaining=(\d+) failed=(\d+)",
                recovery_stop_lines[0],
            )
            if start_match and stop_match:
                epoch, first_frame = map(int, start_match.groups())
                stop_epoch, frames_seen, descriptors, trailing, remaining, failed = map(
                    int, stop_match.groups()
                )
                stop_state = {
                    "epoch": stop_epoch,
                    "frames": frames_seen,
                    "descriptors": descriptors,
                    "trailing_bytes": trailing,
                    "remaining": remaining,
                    "failed": failed,
                }
                result["recovery_epoch"] = epoch
                result["recovery_stop_state"] = stop_state
                if epoch == stop_epoch and frames:
                    capture = result["captures"][0]
                    oracle_hash = expected_sha256(
                        ch, epoch, first_frame, frames, args.seed
                    )
                    capture.update(
                        {
                            "epoch": epoch,
                            "start_frame": first_frame,
                            "qemu_sequence_frames_generated": frames_seen,
                            "qemu_sequence_descriptors_completed": descriptors,
                            "qemu_sequence_trailing_bytes": trailing,
                            "sequence_budget_remaining_after_stop": remaining,
                            "sequence_failed_flag": failed,
                            "oracle_expected_sha256": oracle_hash,
                            "size_ok": capture["returncode"] == 0
                            and guest_bytes == args.duration * 48000 * ch * 2,
                            "oracle_status": "PASS"
                            if capture["guest_sha256"] == oracle_hash
                            else "FAIL",
                            "state_ok": trailing == 0 and failed == 0 and remaining > 0,
                        }
                    )

        result["qmp_audio_device"] = None
        try:
            result["qmp_audio_device"] = qmp.command("query-audiodevs")
        except (OSError, RuntimeError, ValueError, TimeoutError) as error:
            result["qmp_audio_device_error"] = repr(error)
        result["wall_seconds"] = time.monotonic() - wall_start
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
            len(fault_start_lines) >= 1
            and len(fault_stop_lines) >= 1
            and len(recovery_start_lines) == 1
            and len(recovery_stop_lines) == 1
            and not errors
            and not result["kernel_diagnostics"]
            and fault["stop_signal_sent"]
            and fault["continue_signal_sent"]
            and fault["process_exited"]
            and fault["alsa_reports_xrun"]
            and result["recovery_epoch"] is not None
            and result["fault_epochs"]
            and result["recovery_epoch"] > max(result["fault_epochs"])
            and bool(result["captures"])
            and result["captures"][0].get("size_ok")
            and result["captures"][0].get("oracle_status") == "PASS"
            and result["captures"][0].get("state_ok")
        )
        (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "passed": result["passed"],
                    "fault": {
                        key: fault.get(key)
                        for key in (
                            "stop_signal_sent",
                            "continue_signal_sent",
                            "alsa_reports_xrun",
                            "returncode",
                            "guest_bytes",
                            "host_wall_seconds",
                        )
                    },
                    "fault_epochs": result["fault_epochs"],
                    "fault_stop_states": result["fault_stop_states"],
                    "recovery": {
                        key: result["captures"][0].get(key)
                        for key in (
                            "returncode",
                            "guest_bytes",
                            "guest_sha256",
                            "oracle_expected_sha256",
                            "oracle_status",
                            "size_ok",
                            "state_ok",
                            "epoch",
                            "start_frame",
                            "qemu_sequence_descriptors_completed",
                        )
                    },
                    "recovery_stop_state": result["recovery_stop_state"],
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
