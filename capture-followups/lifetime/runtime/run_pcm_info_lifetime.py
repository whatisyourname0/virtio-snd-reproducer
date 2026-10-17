#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run the delayed PCM_INFO caller-buffer lifetime case in a KASAN guest."""

from __future__ import annotations

import os
import subprocess
import argparse

from run_pcm_lifetime import GUEST_WAIT_SECONDS, QMP, SerialLog
from run_pcm_lifetime import qmp_int_until, send_guest_command, assert_kernel_diagnostics
from runtime_common import add_runtime_arguments, log_path, qmp_socket_path, runtime_inputs


HOLD_MS = 10_000


def run(mode: str, args: argparse.Namespace) -> None:
    inputs = runtime_inputs(args, mode)
    serial_log_path = log_path(args, f"kasan-{mode}-control-runtime")
    qmp_path = str(qmp_socket_path(args, f"control-{mode}"))

    argv = [
        str(inputs["qemu"]), "-machine", "virt", "-accel", args.accel, "-cpu", "host",
        "-smp", "2", "-m", "2048", "-kernel", str(inputs["image"]),
        "-initrd", str(inputs["initrd"]),
        "-append", "console=ttyAMA0 earlycon=pl011,0x09000000 loglevel=7 "
                   "panic_on_warn=0 kasan_multi_shot=1",
        "-audiodev", "none,id=snd",
        "-device", "virtio-sound-pci,id=snd0,streams=2,audiodev=snd",
        "-display", "none", "-monitor", "none", "-serial", "stdio",
        "-qmp", f"unix:{qmp_path},server=on,wait=off", "-no-reboot",
    ]

    print(f"[{mode}/control] booting KASAN guest; serial log: {serial_log_path}", flush=True)
    process = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, bufsize=0,
    )
    serial = SerialLog(process.stdout, serial_log_path)
    qmp = None
    try:
        if not serial.wait_for("VIRTIO_SND_GUEST_READY", GUEST_WAIT_SECONDS):
            raise TimeoutError("guest did not reach virtio-snd ready state")
        if not serial.wait_for("VIRTSND_LIFETIME OBSERVER_READY", 5):
            raise RuntimeError("guest observer did not load")
        qmp = QMP(qmp_path)
        props = qmp.command(
            "qom-list", {"path": "/machine/peripheral/snd0/virtio-backend"}
        )
        names = {item["name"] for item in props}
        needed = {
            "x-test-hold-pcm-info-ms", "x-test-control-held",
            "x-test-control-completed",
        }
        if not needed.issubset(names):
            raise RuntimeError(f"patched QEMU control properties missing: {needed - names}")

        qmp.set("x-test-hold-pcm-info-ms", HOLD_MS)
        send_guest_command(
            process,
            "echo 1 > /sys/module/virtio_snd_lifetime_observer/parameters/run_control_query; "
            "echo CONTROL_TRIGGER_RC:$?",
        )
        if not serial.wait_for("VIRTSND_LIFETIME CTL_SG_SAVED", 10):
            raise TimeoutError("guest did not capture the real PCM_INFO response SG")
        qmp_int_until(qmp, "x-test-control-held", 1, 10)
        if not serial.wait_for("VIRTSND_LIFETIME CTL_QUERY_TIMEOUT rc=-110 bytes=32", 5):
            raise TimeoutError("PCM_INFO timeout marker did not finish printing its response size")
        if not serial.wait_for("VIRTSND_LIFETIME CTL_QUERY_RESULT rc=-110", 5):
            raise TimeoutError("the timed-out query caller did not free its output buffer")
        if serial.wait_for_status("CONTROL_TRIGGER_RC", 3) != 0:
            raise RuntimeError("the observer query trigger did not complete")
        if serial.text.count("VIRTSND_LIFETIME CTL_QUERY_RESULT rc=-110") != 1:
            raise AssertionError("expected exactly one timed-out PCM_INFO caller result")
        if serial.text.count("VIRTSND_LIFETIME CTL_QUERY_TIMEOUT rc=-110 bytes=32") != 1:
            raise AssertionError("expected exactly one PCM_INFO caller timeout marker")
        if int(qmp.get("x-test-control-held")) != 1:
            raise AssertionError("QEMU did not hold exactly one PCM_INFO descriptor at caller return")
        if int(qmp.get("x-test-control-completed")) != 0:
            raise AssertionError("QEMU completed PCM_INFO before the timed-out caller returned")
        qmp_int_until(qmp, "x-test-control-completed", 1, 20)
        if qmp_int_until(qmp, "x-test-control-held", 1, 2) != 1:
            raise AssertionError("QEMU did not hold exactly one PCM_INFO descriptor")
        if qmp_int_until(qmp, "x-test-control-completed", 1, 2) != 1:
            raise AssertionError("QEMU did not complete exactly one delayed PCM_INFO descriptor")
        if not serial.wait_for(
            "VIRTSND_LIFETIME CTL_NOTIFY_ENTER count=1 pending=1", 5
        ):
            raise AssertionError("guest did not run the control completion notification")
        if not serial.wait_for("VIRTSND_LIFETIME CTL_LATE_NOTIFY_READ value=a5", 5):
            raise AssertionError("guest did not finish reading the delayed PCM_INFO response")
        if serial.text.count("VIRTSND_LIFETIME CTL_NOTIFY_ENTER count=1 pending=1") != 1:
            raise AssertionError("expected exactly one notification for the pending PCM_INFO descriptor")
        if serial.text.count("VIRTSND_LIFETIME CTL_LATE_NOTIFY_READ value=a5") != 1:
            raise AssertionError("expected exactly one late read of the completed PCM_INFO response")
        completion_marker = (
            "qemu-system-aarch64: virtio-snd lifetime test: "
            "completed delayed PCM_INFO control descriptor"
        )
        if serial.text.count(completion_marker) != 1:
            raise AssertionError("QEMU did not emit exactly one delayed PCM_INFO completion marker")
        positions = [
            serial.text.find("VIRTSND_LIFETIME CTL_NOTIFY_ENTER count=1 pending=1"),
            serial.text.find("VIRTSND_LIFETIME CTL_LATE_NOTIFY_READ value=a5"),
        ]
        if any(position < 0 for position in positions) or positions != sorted(positions):
            raise AssertionError("control notification and late observer read markers were out of order")

        if mode == "baseline":
            if not serial.wait_for("BUG: KASAN:", 5):
                raise AssertionError("baseline did not report the freed PCM_INFO output buffer")
            if "use-after-free" not in serial.text or "ctl_notify_pre_handler" not in serial.text:
                raise AssertionError("baseline KASAN report was not from the late control completion observer")
        else:
            if not serial.wait_for("VIRTSND_LIFETIME CTL_LATE_NOTIFY_READ value=a5", 5):
                raise AssertionError("candidate did not retain and receive the delayed PCM_INFO payload")
            if "BUG: KASAN:" in serial.text:
                raise AssertionError("candidate emitted a KASAN report during delayed control completion")

        print(f"[{mode}/control] delayed PCM_INFO completed; testing normal PCM recovery", flush=True)
        send_guest_command(
            process,
            "echo 0 > /sys/module/virtio_snd_lifetime_observer/parameters/watch_capture; "
            "echo CONTROL_OBSERVER_CAPTURE_DISABLED_RC:$?; "
            "speaker-test -D hw:0,0 -c 2 -t sine -f 440 -l 1 >/tmp/ctl-playback.log 2>&1; "
            "echo CONTROL_RECOVERY_PLAYBACK_RC:$?; "
            f"arecord -D hw:0,0 -f S16_LE -r 48000 -c 2 -d 1 /tmp/ctl-recovery-{mode}.wav >/tmp/ctl-capture.log 2>&1; "
            "echo CONTROL_RECOVERY_CAPTURE_RC:$?; "
            f"aplay -D hw:0,0 /tmp/ctl-recovery-{mode}.wav >/tmp/ctl-aplay.log 2>&1; "
            "echo CONTROL_RECOVERY_APLAY_RC:$?; "
            f"test $(wc -c </tmp/ctl-recovery-{mode}.wav) -gt 180000; "
            "echo CONTROL_RECOVERY_SIZE_RC:$?",
        )
        if serial.wait_for_status("CONTROL_OBSERVER_CAPTURE_DISABLED_RC", 5) != 0:
            raise AssertionError("capture observer could not be disabled for control recovery")
        statuses = {
            marker: serial.wait_for_status(marker, 25)
            for marker in (
                "CONTROL_RECOVERY_PLAYBACK_RC", "CONTROL_RECOVERY_CAPTURE_RC",
                "CONTROL_RECOVERY_APLAY_RC", "CONTROL_RECOVERY_SIZE_RC",
            )
        }
        print(f"[{mode}/control] normal recovery statuses={statuses}; stopping QEMU", flush=True)
        qmp.command("quit")
        if process.wait(timeout=10) != 0:
            raise RuntimeError("QEMU exited with a non-zero status")
        full_log = serial.finish()
        expected = (
            r"slab-use-after-free in ctl_notify_pre_handler"
            if mode == "baseline"
            else None
        )
        diagnostics = assert_kernel_diagnostics(full_log, expected)
        if any(status != 0 for status in statuses.values()):
            raise AssertionError(f"normal PCM recovery failed: {statuses}")
        if mode == "candidate" and diagnostics:
            raise AssertionError("candidate produced kernel diagnostics during fault or recovery")
        if mode == "baseline":
            print(f"[{mode}/control] expected baseline KASAN reports={len(diagnostics)}", flush=True)
            if len(diagnostics) != 1:
                raise AssertionError(
                    f"baseline expected one freed PCM_INFO buffer diagnostic, found {len(diagnostics)}"
                )
        print(f"[{mode}/control] PASS; QEMU exited cleanly and complete serial log was checked", flush=True)
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
        except Exception:
            pass
        if os.path.exists(qmp_path):
            os.unlink(qmp_path)
        serial.logfile.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    add_runtime_arguments(parser, mode=True)
    arguments = parser.parse_args()
    run(arguments.mode, arguments)
