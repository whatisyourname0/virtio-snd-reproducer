#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Hold one capture RX descriptor across RELEASE, then prove live recovery."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import (
    QMP,
    ProcessDeadline,
    SerialLog,
    assert_no_kernel_diagnostics,
    qmp_int_until,
    qmp_integer,
    send_guest_command,
    sha256,
    validate_packaged_modules,
)

HOLD_MS = 5000
HOLD_MIN_GUEST_SECONDS = 4.0
HOLD_MAX_GUEST_SECONDS = 10.0
LINUX_ETIMEDOUT = 110
MODULE_PARAMETERS = (
    "capture_staging",
    "capture_packed_status",
    "capture_allow_short",
    "capture_allow_empty",
    "capture_pause_restart",
)
TRACE_EVENTS = Path(__file__).resolve().parents[1] / "source/trace.events"
CONTROL_CODE_RE = re.compile(
    r"virtio_snd_handle_code.*?==\s+(VIRTIO_SND_R_PCM_(?:PREPARE|START|STOP|RELEASE))"
)
RESULT_RE = re.compile(
    r"(?m)^.*PAUSE_RXFLUSH_RESULT rc=(-?\d+) error=.*? "
    r"elapsed_ms=(\d+) state=(\S+)\r?\n"
)
UPTIME_RE = re.compile(r"(?m)^.*PENDING_RX_HELD_UPTIME:(\d+\.\d+)\r?\n")
COMPLETED_UPTIME_RE = re.compile(r"(?m)^.*PENDING_RX_COMPLETED_UPTIME:(\d+\.\d+)\r?\n")
DMESG_END_RE = re.compile(r"(?m)^PAUSE_RXFLUSH_DMESG_END\r?\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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
        "--timeout", type=int, default=240, help="QEMU wall timeout in seconds"
    )
    return parser.parse_args()


def command_sequence(text: str) -> list[str]:
    return CONTROL_CODE_RE.findall(text)


def require_control_sequence(text: str, expected: list[str], stage: str) -> None:
    actual = command_sequence(text)
    if actual != expected:
        raise AssertionError(
            f"{stage} control trace sequence {actual}, expected {expected}"
        )
    for code, marker in (
        ("VIRTIO_SND_R_PCM_STOP", "VIRTIO_SND_R_PCM_STOP called for stream 1"),
        ("VIRTIO_SND_R_PCM_RELEASE", "VIRTIO_SND_PCM_RELEASE called for stream 1"),
        ("VIRTIO_SND_R_PCM_START", "VIRTIO_SND_R_PCM_START called for stream 1"),
    ):
        if text.count(marker) != expected.count(code):
            raise AssertionError(f"{stage} {code} handler trace count mismatch")


def validate_inputs(args: argparse.Namespace) -> dict[str, str]:
    paths = {
        "qemu": args.qemu,
        "kernel": args.kernel,
        "initrd": args.initrd,
        "modules": args.modules,
        "bios_dir": args.bios_dir,
        "trace_events": TRACE_EVENTS,
    }
    for label, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"missing {label}: {path}")
    if any(
        not path.is_file()
        for path in (args.qemu, args.kernel, args.initrd, TRACE_EVENTS)
    ):
        raise ValueError("QEMU, kernel, initrd, and trace-events inputs must be files")
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
    trace_names = set(
        subprocess.run(
            [str(args.qemu), "-trace", "help"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.splitlines()
    )
    required = {
        "virtio_snd_handle_code",
        "virtio_snd_handle_pcm_start_stop",
        "virtio_snd_handle_pcm_release",
    }
    if not required.issubset(trace_names):
        raise AssertionError(f"QEMU trace events missing: {required - trace_names}")
    module_hashes = validate_packaged_modules(args.modules, args.release, args.initrd)
    return {
        "qemu_version": version,
        "qemu_sha256": sha256(args.qemu),
        "kernel_sha256": sha256(args.kernel),
        "initrd_sha256": sha256(args.initrd),
        "trace_events_sha256": sha256(TRACE_EVENTS),
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
    if args.timeout < 1:
        raise ValueError("--timeout must be positive")
    hashes = validate_inputs(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.output_dir / "pending-rx.qemu.log"
    if log_path.exists():
        raise FileExistsError(f"refusing to overwrite evidence: {log_path}")
    cpu = "host" if args.accel == "hvf" else "max"
    qmp_directory = Path(tempfile.mkdtemp(prefix="vsnd-qmp-"))
    qmp_path = qmp_directory / "qmp.sock"
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
            "console=ttyAMA0 earlycon=pl011,0x09000000 loglevel=7 "
            "panic_on_warn=1 oops=panic panic=-1 lifecycle.mode=pending-rx"
        ),
        "-audiodev",
        "none,id=snd",
        "-device",
        "virtio-sound-pci,id=snd0,streams=2,audiodev=snd",
        "-trace",
        f"events={TRACE_EVENTS}",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        "stdio",
        "-qmp",
        f"unix:{qmp_path},server=on,wait=off",
        "-no-reboot",
    ]

    process: subprocess.Popen[bytes] | None = None
    deadline: ProcessDeadline | None = None
    serial: SerialLog | None = None
    qmp: QMP | None = None
    completed_log = ""
    try:
        env = os.environ.copy()
        for name in tuple(env):
            if name.startswith("QEMU_VIRTIO_SND_") or name == "FSM_QEMU_BINARY":
                env.pop(name, None)
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            bufsize=0,
        )
        deadline = ProcessDeadline(process, args.timeout)
        deadline.start()
        if process.stdout is None:
            raise RuntimeError("QEMU did not expose serial output")
        serial = SerialLog(process.stdout, log_path)
        print(f"QEMU={args.qemu.resolve()}")
        print(f"KERNEL={args.kernel.resolve()}")
        print(f"INITRD={args.initrd.resolve()}")
        print(f"MODULES={args.modules.resolve()}")
        print(f"BIOS_DIR={args.bios_dir.resolve()}")
        for label, digest in hashes.items():
            print(f"{label.upper()}={digest}")

        ready_marker = f"VIRTIO_SND_GUEST_READY release={args.release}"
        if not serial.wait_for(ready_marker, 90):
            raise TimeoutError("guest did not reach the virtio-snd ready state")
        for parameter in MODULE_PARAMETERS:
            marker = f"VIRTSND_MODULE_PARAM_{parameter}=Y"
            if not serial.wait_for(marker, 5):
                raise AssertionError(
                    f"guest module parameter is not enabled: {parameter}"
                )

        qmp = QMP(qmp_path, timeout=deadline.remaining())
        properties = qmp.command(
            "qom-list", {"path": "/machine/peripheral/snd0/virtio-backend"}
        )
        if not isinstance(properties, list):
            raise TypeError("QMP did not return the virtio-snd backend properties")
        property_names = {item["name"] for item in properties if isinstance(item, dict)}
        required_properties = {
            "x-test-hold-capture-ms",
            "x-test-capture-held",
            "x-test-capture-completed",
        }
        if not required_properties.issubset(property_names):
            raise AssertionError(
                f"QEMU delayed-RX properties missing: {required_properties - property_names}"
            )
        qmp.set("x-test-hold-capture-ms", HOLD_MS)
        print(f"RX_HOLD_ARMED_MS={HOLD_MS}", flush=True)

        send_guest_command(
            process,
            "/pause-rxflush-probe hw:0,0 & "
            "echo $! >/tmp/pause-rxflush.pid; "
            "echo PAUSE_RXFLUSH_PROBE_PID:$!",
        )
        if not serial.wait_for("PAUSE_RXFLUSH_READY", 30):
            raise TimeoutError("probe did not reach its capture-pause gate")
        if not serial.wait_for(
            "PAUSE_RXFLUSH_CONFIG rate=48000 format=S16_LE channels=2", 2
        ):
            raise AssertionError("probe did not confirm the requested capture geometry")
        if not serial.wait_for("can_pause=1", 2):
            raise AssertionError("capture PCM did not advertise pause capability")
        held = qmp_int_until(qmp, "x-test-capture-held", 1, 10)
        capture_completed = qmp_integer(qmp, "x-test-capture-completed")
        if held != 1 or capture_completed != 0:
            raise AssertionError(
                "expected exactly one still-pending RX descriptor, "
                f"held={held} completed={capture_completed}"
            )
        send_guest_command(
            process,
            "echo PENDING_RX_HELD_UPTIME:$(cut -d' ' -f1 /proc/uptime)",
        )
        held_uptime_match = serial.wait_for_match(UPTIME_RE, 5)
        held_uptime = float(held_uptime_match.group(1))

        send_guest_command(
            process,
            'kill -USR1 "$(cat /tmp/pause-rxflush.pid)"; '
            "echo PAUSE_RXFLUSH_SIGNAL_RC:$?",
        )
        if serial.wait_for_status("PAUSE_RXFLUSH_SIGNAL_RC", 5) != 0:
            raise AssertionError("guest could not signal the probe to pause")
        result = serial.wait_for_match(RESULT_RE, 8)
        rc, elapsed_ms, state = (
            int(result.group(1)),
            int(result.group(2)),
            result.group(3),
        )
        if rc != -LINUX_ETIMEDOUT:
            raise AssertionError(f"pause returned {rc}, expected -ETIMEDOUT")
        if not 750 <= elapsed_ms < 3000:
            raise AssertionError(
                f"pause timeout outside the 1 s bound: {elapsed_ms} ms"
            )

        send_guest_command(
            process,
            "echo PAUSE_RXFLUSH_DMESG_BEGIN; "
            "count=$(dmesg | grep -F -c 'SID 1: pause RELEASE did not flush I/O queue' || true); "
            "echo PAUSE_RXFLUSH_WARNING_COUNT:$count; "
            "echo PAUSE_RXFLUSH_DMESG_END",
        )
        warning_count = serial.wait_for_status("PAUSE_RXFLUSH_WARNING_COUNT", 5)
        if warning_count != 1:
            raise AssertionError(
                "expected exactly one pending-RX RELEASE timeout warning, "
                f"got {warning_count}"
            )
        try:
            serial.wait_for_match(DMESG_END_RE, 5)
        except TimeoutError as error:
            raise TimeoutError(
                "guest did not return the dmesg timeout evidence"
            ) from error
        for control_timeout in (
            "control message (0x00000105) timeout",
            "control message (0x00000103) timeout",
        ):
            if control_timeout in serial.text:
                raise AssertionError(
                    f"PCM control response was not acknowledged: {control_timeout}"
                )
        capture_held = qmp_integer(qmp, "x-test-capture-held")
        capture_completed = qmp_integer(qmp, "x-test-capture-completed")
        if capture_held != 1 or capture_completed != 0:
            raise AssertionError(
                "held RX completed before or during the RELEASE timeout"
            )
        expected_controls = [
            "VIRTIO_SND_R_PCM_PREPARE",
            "VIRTIO_SND_R_PCM_PREPARE",
            "VIRTIO_SND_R_PCM_START",
            "VIRTIO_SND_R_PCM_STOP",
            "VIRTIO_SND_R_PCM_RELEASE",
        ]
        require_control_sequence(serial.text, expected_controls, "after_pause_timeout")
        print(
            f"RX_PENDING_AFTER_RELEASE_PASS elapsed_ms={elapsed_ms} state={state}",
            flush=True,
        )

        completed = qmp_int_until(
            qmp, "x-test-capture-completed", 1, HOLD_MS / 1000 + 15
        )
        capture_held = qmp_integer(qmp, "x-test-capture-held")
        if completed != 1 or capture_held != 1:
            raise AssertionError(
                "expected one delayed RX completion, "
                f"held={qmp.get('x-test-capture-held')} completed={completed}"
            )
        if serial.text.count("completed delayed capture descriptor") != 1:
            raise AssertionError("QEMU did not complete exactly one held RX descriptor")
        send_guest_command(
            process,
            "echo PENDING_RX_COMPLETED_UPTIME:$(cut -d' ' -f1 /proc/uptime)",
        )
        completed_uptime_match = serial.wait_for_match(COMPLETED_UPTIME_RE, 5)
        elapsed_guest_seconds = float(completed_uptime_match.group(1)) - held_uptime
        if (
            not HOLD_MIN_GUEST_SECONDS
            <= elapsed_guest_seconds
            <= HOLD_MAX_GUEST_SECONDS
        ):
            raise AssertionError(
                "delayed RX completion violated the 5 s guest-time contract: "
                f"{elapsed_guest_seconds:.2f}s"
            )

        send_guest_command(
            process,
            'kill -USR2 "$(cat /tmp/pause-rxflush.pid)"; '
            "echo PAUSE_RXFLUSH_RECOVERY_SIGNAL_RC:$?",
        )
        if serial.wait_for_status("PAUSE_RXFLUSH_RECOVERY_SIGNAL_RC", 5) != 0:
            raise AssertionError("guest could not signal the recovery path")
        if not serial.wait_for(
            "PAUSE_RXFLUSH_RECOVERY_PASS frames=1024 state=RUNNING", 30
        ):
            raise TimeoutError("next capture did not return a full 1024-frame period")
        if qmp_int_until(qmp, "x-test-capture-completed", 1, 2) != 1:
            raise AssertionError("delayed RX completion count changed during recovery")
        expected_after_recovery = expected_controls + [
            "VIRTIO_SND_R_PCM_PREPARE",
            "VIRTIO_SND_R_PCM_START",
        ]
        require_control_sequence(serial.text, expected_after_recovery, "after_recovery")
        if "PAUSE_RXFLUSH_RECOVERY_PASS" not in serial.text:
            raise AssertionError("next-capture recovery marker is missing")
        capture_held = qmp_integer(qmp, "x-test-capture-held")
        capture_completed = qmp_integer(qmp, "x-test-capture-completed")
        if capture_held != 1 or capture_completed != 1:
            raise AssertionError("final QMP counters are not held=1 completed=1")

        qmp.command("quit")
        if process.wait(timeout=15) != 0:
            raise RuntimeError("QEMU exited with a non-zero status")
        deadline.check()
        completed_log = serial.finish(timeout=15)
        if len(DMESG_END_RE.findall(completed_log)) != 1:
            raise AssertionError("log lacks the single dmesg timeout evidence block")
        if completed_log.count("completed delayed capture descriptor") != 1:
            raise AssertionError("log lacks exactly one delayed RX completion")
        assert_no_kernel_diagnostics(completed_log)
        print(
            "PAUSE_RELEASE_DELAYED_RX_PASS: acknowledged STOP/RELEASE retained one "
            "pending RX through the timeout; delayed completion was followed by a "
            "full-period capture without a second RELEASE",
            flush=True,
        )
        print(
            f"RX_DELAYED_COMPLETION_GUEST_SECONDS={elapsed_guest_seconds:.2f}",
            flush=True,
        )
        print(f"QEMU_LOG={log_path.resolve()}")
    except Exception:
        if deadline is not None:
            deadline.check()
        raise
    finally:
        if deadline is not None:
            deadline.close()
        if qmp is not None:
            qmp.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if serial is not None:
            with suppress(Exception):  # cleanup must preserve the original failure
                serial.finish(timeout=5)
            serial.close()
        shutil.rmtree(qmp_directory, ignore_errors=True)


if __name__ == "__main__":
    run()
