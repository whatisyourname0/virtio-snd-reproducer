#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run the actual PCM STOP or RELEASE timeout/reset regression."""

from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from qmp import QMP
from runtime_inputs import assert_kernel_diagnostics, validate_inputs
from serial_log import SerialLog

CONTROL_CODES = {"stop": 0x105, "release": 0x103}
PCM_READY_MARKER = "DIRECT_RESET_PCM_READY=/dev/snd/pcmC0D0p"
QMP_PROPERTIES = {
    "x-test-hold-control-ms",
    "x-test-hold-control-code",
    "x-test-control-held-code",
    "x-test-control-held",
    "x-test-control-completed",
    "x-test-control-pending",
}
TIMEOUT_WINDOW_SECONDS = (0.75, 5.0)
HOLD_MS = 15_000
HOLD_DEADLINE_MARGIN_SECONDS = 1.0
VIRTUAL_TIMER_HOST_GUARD_SECONDS = 60.0
HOST_TIMEOUT_GUARD_SECONDS = 20.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=(*CONTROL_CODES, "both"))
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--modules", type=Path, required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--logs-dir", type=Path, required=True)
    parser.add_argument("--accelerator", choices=("tcg", "hvf"), default="tcg")
    parser.add_argument("--boot-timeout", type=float, default=90)
    parser.add_argument("--module-option", action="append", default=[])
    parser.add_argument("--expect-module-parameter", action="append", default=[])
    return parser.parse_args()


def _validate_guest_options(args: argparse.Namespace) -> tuple[str, str]:
    option_pattern = re.compile(r"[A-Za-z0-9_]+=[A-Za-z0-9_.+-]+\Z")
    options = [*args.module_option]
    expected = [*args.expect_module_parameter]
    for value in options:
        if not option_pattern.fullmatch(value):
            raise ValueError(f"unsafe or unsupported module option: {value!r}")
    for value in expected:
        if not option_pattern.fullmatch(value):
            raise ValueError(
                f"unsafe or unsupported expected module parameter: {value!r}"
            )
    return ",".join(options), ",".join(expected)


def _qemu_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("QEMU_VIRTIO_SND_") and name != "FSM_QEMU_BINARY"
    }


def _qemu_argv(
    args: argparse.Namespace,
    mode: str,
    qmp_path: Path,
    module_options: str,
    expected_parameters: str,
) -> list[str]:
    if args.accelerator == "hvf":
        if platform.system() != "Darwin":
            raise ValueError(
                "HVF is available only on macOS; use the default TCG accelerator"
            )
        cpu = "host"
    else:
        cpu = "cortex-a57"
    command_line = (
        "console=ttyAMA0 earlycon=pl011,0x09000000 loglevel=7 "
        "panic_on_warn=0 kasan_multi_shot=1"
    )
    if module_options:
        command_line += f" direct_reset.module_options={module_options}"
    if expected_parameters:
        command_line += f" direct_reset.expected_parameters={expected_parameters}"
    return [
        str(args.qemu.resolve(strict=True)),
        "-machine",
        "virt",
        "-accel",
        args.accelerator,
        "-cpu",
        cpu,
        "-smp",
        "2",
        "-m",
        "2048",
        "-kernel",
        str(args.image.resolve(strict=True)),
        "-initrd",
        str(args.initrd.resolve(strict=True)),
        "-append",
        command_line,
        "-audiodev",
        "none,id=snd",
        "-device",
        "virtio-sound-pci,id=snd0,streams=2,audiodev=snd",
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


def _send_guest_command(process: subprocess.Popen[bytes], command: str) -> None:
    if process.stdin is None:
        raise RuntimeError("QEMU serial input is unavailable")
    process.stdin.write(command.encode("utf-8") + b"\n")
    process.stdin.flush()


def _wait_qmp_int(qmp: QMP, name: str, expected: int, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    last_value = -1
    while time.monotonic() < deadline:
        last_value = int(qmp.get(name))
        if last_value >= expected:
            return last_value
        time.sleep(0.1)
    raise TimeoutError(f"QMP {name} stayed at {last_value}; expected >= {expected}")


def _require_qmp_properties(qmp: QMP) -> None:
    properties = qmp.command(
        "qom-list", {"path": "/machine/peripheral/snd0/virtio-backend"}
    )
    names = {item["name"] for item in properties}
    missing = QMP_PROPERTIES - names
    if missing:
        raise RuntimeError(f"direct-reset QEMU properties missing: {sorted(missing)}")


def _assert_control_state(
    qmp: QMP,
    *,
    mode: str,
    pending: int,
    completed: int,
    held: int,
) -> None:
    code = CONTROL_CODES[mode]
    actual_code = int(qmp.get("x-test-control-held-code"))
    actual_pending = int(qmp.get("x-test-control-pending"))
    actual_completed = int(qmp.get("x-test-control-completed"))
    actual_held = int(qmp.get("x-test-control-held"))
    if actual_code != code:
        raise AssertionError(f"QEMU held 0x{actual_code:04x}; expected 0x{code:04x}")
    if actual_pending != pending:
        raise AssertionError(
            f"QEMU pending descriptor={actual_pending}; expected {pending}"
        )
    if actual_completed != completed:
        raise AssertionError(
            f"QEMU completion count={actual_completed}; expected {completed}"
        )
    if actual_held != held:
        raise AssertionError(
            f"QEMU held descriptor count={actual_held}; expected {held}"
        )


def _status(serial: SerialLog, mode: str, label: str, timeout: float = 12) -> int:
    return serial.wait_for_status(f"DIRECT_RESET_{mode.upper()}_{label}_RC", timeout)


def _guest_time(serial: SerialLog, mode: str, label: str) -> float:
    marker = f"DIRECT_RESET_{mode.upper()}_{label}_UPTIME"
    match = serial.wait_for_match(
        re.compile(rf"(?m)^{re.escape(marker)}:(\d+(?:\.\d+)?)\r?\n"),
        10,
    )
    return float(match.group(1))


def _timeout_command(mode: str) -> str:
    marker = mode.upper()
    return (
        f"pid=$(cat /tmp/direct-reset-{mode}.pid); "
        "read -r started idle </proc/uptime; "
        f"echo DIRECT_RESET_{marker}_START_UPTIME:$started; "
        f'kill -KILL "$pid"; kill_rc=$?; '
        f"echo DIRECT_RESET_{marker}_KILL_RC:$kill_rc; "
        f'wait "$pid" 2>/dev/null; child_rc=$?; '
        "read -r ended idle </proc/uptime; "
        f"echo DIRECT_RESET_{marker}_CHILD_EXIT_RC:$child_rc; "
        f"echo DIRECT_RESET_{marker}_END_UPTIME:$ended"
    )


def _start_pcm_caller(
    process: subprocess.Popen[bytes], serial: SerialLog, mode: str
) -> None:
    marker = mode.upper()
    _send_guest_command(
        process,
        "speaker-test -D hw:0,0 -c 2 -t sine -f 440 -l 100000 "
        f">/tmp/direct-reset-{mode}.log 2>&1 & "
        f"echo $! >/tmp/direct-reset-{mode}.pid; "
        f"echo DIRECT_RESET_{marker}_PID:$!",
    )
    if not serial.wait_for(f"DIRECT_RESET_{marker}_PID:", 10):
        raise TimeoutError("guest did not start the long-running PCM caller")
    _send_guest_command(
        process,
        f"pid=$(cat /tmp/direct-reset-{mode}.pid); "
        f'kill -0 "$pid"; echo DIRECT_RESET_{marker}_ALIVE_RC:$?',
    )
    if _status(serial, mode, "ALIVE") != 0:
        raise AssertionError("PCM playback caller exited before signal injection")
    _send_guest_command(
        process,
        "i=0; while ! grep -q 'Time per period' "
        f"/tmp/direct-reset-{mode}.log && [ $i -lt 100 ]; do "
        "sleep 0.1; i=$((i + 1)); done; "
        f"grep -q 'Time per period' /tmp/direct-reset-{mode}.log; "
        f"echo DIRECT_RESET_{marker}_PLAYING_RC:$?",
    )
    if _status(serial, mode, "PLAYING") != 0:
        raise AssertionError("speaker-test did not reach active playback")


def _unbind_and_rebind(
    process: subprocess.Popen[bytes], serial: SerialLog, mode: str
) -> None:
    marker = mode.upper()
    unbind = (
        "device=$(for path in /sys/bus/virtio/drivers/virtio_snd/virtio*; do "
        '[ -e "$path" ] && { echo "${path##*/}"; break; }; done); '
        'test -n "$device" || exit 1; '
        'echo "$device" > /sys/bus/virtio/drivers/virtio_snd/unbind; '
        f"echo DIRECT_RESET_{marker}_UNBIND_RC:$?"
    )
    _send_guest_command(process, unbind)
    if _status(serial, mode, "UNBIND", 15) != 0:
        raise AssertionError("virtio-snd sysfs unbind failed")

    bind = (
        'echo "$device" > /sys/bus/virtio/drivers/virtio_snd/bind; '
        f"echo DIRECT_RESET_{marker}_BIND_RC:$?; "
        "i=0; while [ ! -e /dev/snd/pcmC0D0p ] && [ $i -lt 100 ]; do "
        "sleep 0.1; i=$((i + 1)); done; "
        "test -e /dev/snd/pcmC0D0p; "
        f"echo DIRECT_RESET_{marker}_CARD_READY_RC:$?"
    )
    _send_guest_command(process, bind)
    if _status(serial, mode, "BIND", 10) != 0:
        raise AssertionError("virtio-snd sysfs bind failed")
    if _status(serial, mode, "CARD_READY", 12) != 0:
        raise AssertionError("PCM device did not reappear after reset")


def _recover_pcm(
    process: subprocess.Popen[bytes], serial: SerialLog, mode: str
) -> None:
    marker = mode.upper()
    command = (
        "speaker-test -D hw:0,0 -c 2 -t sine -f 440 -l 1 "
        f">/tmp/direct-reset-{mode}-play.log 2>&1; "
        f"echo DIRECT_RESET_{marker}_PLAY_RC:$?; "
        "arecord -D hw:0,0 -f S16_LE -r 48000 -c 2 -d 1 "
        f"/tmp/direct-reset-{mode}.wav >/tmp/direct-reset-{mode}-capture.log 2>&1; "
        f"echo DIRECT_RESET_{marker}_CAPTURE_RC:$?; "
        f"aplay -D hw:0,0 /tmp/direct-reset-{mode}.wav "
        f">/tmp/direct-reset-{mode}-aplay.log 2>&1; "
        f"echo DIRECT_RESET_{marker}_APLAY_RC:$?; "
        f"test $(wc -c </tmp/direct-reset-{mode}.wav) -gt 180000; "
        f"echo DIRECT_RESET_{marker}_SIZE_RC:$?"
    )
    _send_guest_command(process, command)
    statuses = {
        name: _status(serial, mode, name, 30)
        for name in ("PLAY", "CAPTURE", "APLAY", "SIZE")
    }
    if any(status != 0 for status in statuses.values()):
        _send_guest_command(
            process,
            f"cat /tmp/direct-reset-{mode}-play.log "
            f"/tmp/direct-reset-{mode}-capture.log "
            f"/tmp/direct-reset-{mode}-aplay.log; "
            f"echo DIRECT_RESET_{marker}_RECOVERY_LOGS_END",
        )
        serial.wait_for(f"DIRECT_RESET_{marker}_RECOVERY_LOGS_END", 5)
        raise AssertionError(
            f"normal PCM recovery failed: {statuses}\n{serial.text[-4000:]}"
        )


def run_one(args: argparse.Namespace, mode: str) -> None:
    validate_inputs(
        qemu=args.qemu,
        image=args.image,
        initrd=args.initrd,
        modules=args.modules,
        release=args.release,
        manifest=args.manifest,
    )
    module_options, expected_parameters = _validate_guest_options(args)
    args.logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = args.logs_dir / f"direct-reset-{mode}-{stamp}-{os.getpid()}.serial.log"
    qmp_path = Path("/tmp") / f"dr-{mode}-{os.getpid()}.qmp"
    if qmp_path.exists():
        qmp_path.unlink()
    argv = _qemu_argv(args, mode, qmp_path, module_options, expected_parameters)
    print(f"[{mode}] booting explicit runtime inputs; serial={log_path}", flush=True)
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        env=_qemu_environment(),
    )
    if process.stdout is None:
        raise RuntimeError("QEMU serial output is unavailable")
    serial = SerialLog(process.stdout, log_path)
    qmp: QMP | None = None
    try:
        ready_match = serial.wait_for_match(
            re.compile(r"(?m)^VIRTIO_SND_GUEST_READY release=([^\r\n]+)\r?\n"),
            args.boot_timeout,
        )
        guest_release = ready_match.group(1)
        if guest_release != args.release:
            raise AssertionError(
                f"guest uname -r is {guest_release!r}; expected {args.release!r}"
            )
        if not serial.wait_for(PCM_READY_MARKER, 5):
            raise AssertionError("guest PCM playback device did not become ready")
        for expected in args.expect_module_parameter:
            parameter = expected.split("=", maxsplit=1)[0]
            marker = f"VIRTSND_MODULE_PARAM_{parameter}={expected.split('=', 1)[1]}"
            if not serial.wait_for(marker, 5):
                raise AssertionError(f"module parameter did not match: {expected}")

        qmp = QMP(qmp_path)
        _require_qmp_properties(qmp)
        code = CONTROL_CODES[mode]
        qmp.set("x-test-hold-control-code", code)
        qmp.set("x-test-hold-control-ms", HOLD_MS)
        _start_pcm_caller(process, serial, mode)

        signal_at = time.monotonic()
        _send_guest_command(process, _timeout_command(mode))
        if not serial.wait_for(f"DIRECT_RESET_{mode.upper()}_START_UPTIME:", 5):
            raise TimeoutError("guest did not print a timestamp before SIGKILL")
        _wait_qmp_int(qmp, "x-test-control-held", 1, 15)
        _wait_qmp_int(qmp, "x-test-control-pending", 1, 5)
        _assert_control_state(qmp, mode=mode, pending=1, completed=0, held=1)
        signal_guest_uptime = _guest_time(serial, mode, "START")
        if int(qmp.get("x-test-control-held-code")) != code:
            raise AssertionError(
                "QEMU held a different virtio-snd request than requested"
            )
        print(f"[{mode}] QEMU holds the actual request 0x{code:04x}", flush=True)

        # The first wait command already initiated SIGKILL; retain its start time.
        timeout_end_uptime = _wait_for_timeout_after_kill(serial, qmp, mode, signal_at)
        _assert_control_state(qmp, mode=mode, pending=1, completed=0, held=1)
        print(
            f"[{mode}] caller timed out while the descriptor remains pending",
            flush=True,
        )

        _unbind_and_rebind(process, serial, mode)
        _assert_control_state(qmp, mode=mode, pending=0, completed=0, held=1)
        _recover_pcm(process, serial, mode)

        hold_anchor = max(signal_guest_uptime, timeout_end_uptime)
        _wait_past_virtual_hold_deadline(process, serial, qmp, mode, hold_anchor)
        _assert_control_state(qmp, mode=mode, pending=0, completed=0, held=1)
        if f"completed delayed control request code=0x{code:04x}" in serial.text:
            raise AssertionError("QEMU logged a delayed request completion after reset")

        qmp.command("quit")
        if process.wait(timeout=10) != 0:
            raise RuntimeError("QEMU exited with a non-zero status")
        transcript = serial.finish()
        diagnostics = assert_kernel_diagnostics(transcript)
        if diagnostics:
            raise AssertionError(
                "kernel diagnostics detected:\n" + "\n".join(diagnostics)
            )
        print(
            f"[{mode}] PASS: actual request held, SIGKILL timeout bounded, reset canceled "
            "completion, playback/capture/replay recovered, no kernel diagnostics",
            flush=True,
        )
    finally:
        if qmp is not None:
            qmp.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        try:
            serial.finish(timeout=5)
        except (OSError, RuntimeError, TimeoutError) as error:
            print(
                f"[cleanup] serial reader did not finish cleanly: {error}",
                file=sys.stderr,
            )
        serial.close_file()
        if qmp_path.exists():
            qmp_path.unlink()


def _wait_for_timeout_after_kill(
    serial: SerialLog,
    qmp: QMP,
    mode: str,
    signal_at: float,
) -> float:
    timeout_line = f"control message (0x{CONTROL_CODES[mode]:08x}) timeout"
    if _status(serial, mode, "KILL", 5) != 0:
        raise AssertionError("guest could not send SIGKILL to the waiting caller")
    if not serial.wait_for(timeout_line, HOST_TIMEOUT_GUARD_SECONDS):
        raise TimeoutError(
            f"driver timeout did not appear within {HOST_TIMEOUT_GUARD_SECONDS:.0f}s "
            f"of host time; serial tail:\n{serial.text[-3000:]}"
        )
    child_status = _status(serial, mode, "CHILD_EXIT", 5)
    if child_status != 137:
        raise AssertionError(
            f"SIGKILLed PCM caller exited with {child_status}, expected 137"
        )
    start = _guest_time(serial, mode, "START")
    end = _guest_time(serial, mode, "END")
    elapsed = end - start
    lower, upper = TIMEOUT_WINDOW_SECONDS
    if not lower <= elapsed < upper:
        raise AssertionError(
            f"guest timeout interval {elapsed:.2f}s outside preserved "
            f"{lower:.2f}..{upper:.2f}s window"
        )
    host_elapsed = time.monotonic() - signal_at
    if int(qmp.get("x-test-control-pending")) != 1:
        raise AssertionError("held descriptor disappeared before device reset")
    if int(qmp.get("x-test-control-completed")) != 0:
        raise AssertionError("QEMU completed the held request before reset")
    print(
        f"[{mode}] timeout guest interval={elapsed:.2f}s; host interval="
        f"{host_elapsed:.2f}s (diagnostic only)",
        flush=True,
    )
    return end


def _wait_past_virtual_hold_deadline(
    process: subprocess.Popen[bytes],
    serial: SerialLog,
    qmp: QMP,
    mode: str,
    hold_anchor_guest_uptime: float,
) -> None:
    marker = mode.upper()
    deadline = hold_anchor_guest_uptime + HOLD_MS / 1000 + HOLD_DEADLINE_MARGIN_SECONDS
    host_started = time.monotonic()
    sample = 0
    current = hold_anchor_guest_uptime
    while current <= deadline:
        if time.monotonic() - host_started >= VIRTUAL_TIMER_HOST_GUARD_SECONDS:
            raise TimeoutError(
                "guest uptime did not pass the QEMU virtual hold deadline within "
                f"the {VIRTUAL_TIMER_HOST_GUARD_SECONDS:.0f}s host hang guard"
            )
        if int(qmp.get("x-test-control-completed")) != 0:
            raise AssertionError("delayed control completed after reset")
        if int(qmp.get("x-test-control-pending")) != 0:
            raise AssertionError("reset did not clear the held control descriptor")
        sample += 1
        probe = f"DIRECT_RESET_{marker}_HOLD_UPTIME_{sample}"
        _send_guest_command(
            process,
            f"uptime=$(cut -d' ' -f1 /proc/uptime); echo {probe}:$uptime",
        )
        match = serial.wait_for_match(
            re.compile(rf"(?m)^{re.escape(probe)}:(\d+(?:\.\d+)?)\r?\n"),
            5,
        )
        current = float(match.group(1))
        if current <= deadline:
            time.sleep(0.25)
    host_elapsed = time.monotonic() - host_started
    print(
        f"[{mode}] guest uptime passed QEMU hold deadline: "
        f"{current:.2f}s > {deadline:.2f}s; host wait={host_elapsed:.2f}s",
        flush=True,
    )


def main() -> None:
    args = parse_args()
    for mode in ("stop", "release") if args.mode == "both" else (args.mode,):
        run_one(args, mode)


if __name__ == "__main__":
    main()
