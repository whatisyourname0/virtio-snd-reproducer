#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify candidate virtio-snd reset cancels a pending delayed capture."""

from __future__ import annotations

import os
import subprocess
import argparse
import time

from run_pcm_lifetime import (
    GUEST_WAIT_SECONDS,
    QMP,
    SerialLog,
    assert_kernel_diagnostics,
    qmp_int_until,
    send_guest_command,
)
from runtime_common import add_runtime_arguments, log_path, qmp_socket_path, runtime_inputs


HOLD_MS = 30_000


def run(args: argparse.Namespace) -> None:
    inputs = runtime_inputs(args, "candidate")
    serial_log_path = log_path(args, "kasan-candidate-reset-runtime")
    qmp_path = str(qmp_socket_path(args, "reset-candidate"))

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

    print(f"[candidate/reset] booting KASAN guest; serial log: {serial_log_path}", flush=True)
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
        properties = qmp.command(
            "qom-list", {"path": "/machine/peripheral/snd0/virtio-backend"}
        )
        names = {item["name"] for item in properties}
        needed = {
            "x-test-hold-capture-ms", "x-test-capture-held",
            "x-test-capture-completed",
        }
        if not needed.issubset(names):
            raise RuntimeError(f"patched QEMU properties missing: {needed - names}")

        send_guest_command(
            process,
            "rmmod virtio_snd_lifetime_observer; echo RESET_OBSERVER_UNLOAD_RC:$?",
        )
        if serial.wait_for_status("RESET_OBSERVER_UNLOAD_RC", 5) != 0:
            raise AssertionError("could not unload observer before the reset case")

        qmp.set("x-test-hold-capture-ms", HOLD_MS)
        send_guest_command(
            process,
            "arecord -D hw:0,0 -f S16_LE -r 48000 -c 2 -d 1 "
            "/tmp/virtio-snd-reset-pending.wav; "
            "echo RESET_TIMEOUT_CAPTURE_RC:$?",
        )
        if qmp_int_until(qmp, "x-test-capture-held", 1, 10) != 1:
            raise AssertionError("QEMU did not hold one real capture descriptor")
        if int(qmp.get("x-test-capture-held")) != 1:
            raise AssertionError("QEMU held more than one capture descriptor")
        held_at = time.monotonic()
        if serial.wait_for_status("RESET_TIMEOUT_CAPTURE_RC", 22) != 0:
            raise AssertionError("capture did not return after the driver timeout path")
        if not serial.wait_for("failed to flush I/O queue", 5):
            raise AssertionError("capture hw_free did not report its pending-I/O flush timeout")
        if serial.text.count("SID 1: failed to flush I/O queue") != 1:
            raise AssertionError("expected exactly one capture pending-I/O flush timeout")
        if int(qmp.get("x-test-capture-completed")) != 0:
            raise AssertionError("the delayed DMA completed before device unbind")

        unbind = (
            "device=$(for path in /sys/bus/virtio/drivers/virtio_snd/virtio*; do "
            "[ -e \"$path\" ] && { echo \"${path##*/}\"; break; }; done); "
            "test -n \"$device\" || exit 1; "
            "echo \"$device\" > /sys/bus/virtio/drivers/virtio_snd/unbind; "
            "echo RESET_UNBIND_RC:$?"
        )
        send_guest_command(process, unbind)
        if serial.wait_for_status("RESET_UNBIND_RC", 15) != 0:
            raise AssertionError("virtio-snd sysfs unbind failed")
        if int(qmp.get("x-test-capture-completed")) != 0:
            raise AssertionError("the descriptor completed during device reset/unbind")

        bind = (
            "echo \"$device\" > /sys/bus/virtio/drivers/virtio_snd/bind; "
            "echo RESET_BIND_RC:$?; "
            "i=0; while [ ! -e /dev/snd/pcmC0D0c ] && [ $i -lt 100 ]; do "
            "sleep 0.1; i=$((i + 1)); done; "
            "test -e /dev/snd/pcmC0D0c; echo RESET_CARD_READY_RC:$?"
        )
        send_guest_command(process, bind)
        if serial.wait_for_status("RESET_BIND_RC", 10) != 0:
            raise AssertionError("virtio-snd sysfs bind failed")
        if serial.wait_for_status("RESET_CARD_READY_RC", 12) != 0:
            raise AssertionError("the virtio-snd card did not reappear after bind")

        recovery = (
            "speaker-test -D hw:0,0 -c 2 -t sine -f 440 -l 1 >/tmp/reset-playback.log 2>&1; "
            "echo RESET_RECOVERY_PLAYBACK_RC:$?; "
            "arecord -D hw:0,0 -f S16_LE -r 48000 -c 2 -d 1 "
            "/tmp/virtio-snd-reset-recovery.wav >/tmp/reset-capture.log 2>&1; "
            "echo RESET_RECOVERY_CAPTURE_RC:$?; "
            "aplay -D hw:0,0 /tmp/virtio-snd-reset-recovery.wav >/tmp/reset-aplay.log 2>&1; "
            "echo RESET_RECOVERY_APLAY_RC:$?; "
            "test $(wc -c </tmp/virtio-snd-reset-recovery.wav) -gt 180000; "
            "echo RESET_RECOVERY_SIZE_RC:$?"
        )
        send_guest_command(process, recovery)
        statuses = {
            marker: serial.wait_for_status(marker, 25)
            for marker in (
                "RESET_RECOVERY_PLAYBACK_RC", "RESET_RECOVERY_CAPTURE_RC",
                "RESET_RECOVERY_APLAY_RC", "RESET_RECOVERY_SIZE_RC",
            )
        }

        deadline = held_at + HOLD_MS / 1000 + 1
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        completed = int(qmp.get("x-test-capture-completed"))
        if completed != 0:
            raise AssertionError(
                f"QEMU completed delayed DMA after reset; completion count={completed}"
            )
        if int(qmp.get("x-test-capture-held")) != 1:
            raise AssertionError("reset lost the held-descriptor provenance counter")
        if serial.text.count("completed delayed capture descriptor") != 0:
            raise AssertionError("QEMU emitted a delayed DMA completion after reset")

        qmp.command("quit")
        if process.wait(timeout=10) != 0:
            raise RuntimeError("QEMU exited with a non-zero status")
        full_log = serial.finish()
        if full_log.count("completed delayed capture descriptor") != 0:
            raise AssertionError("QEMU emitted a delayed completion before serial shutdown")
        diagnostics = assert_kernel_diagnostics(full_log, None)
        if any(status != 0 for status in statuses.values()):
            raise AssertionError(f"normal PCM recovery after rebind failed: {statuses}")
        if diagnostics:
            raise AssertionError("candidate produced kernel diagnostics during reset/rebind recovery")
        print(
            "[candidate/reset] PASS; hw_free timed out with a held descriptor, unbind/rebind "
            "recovered audio, and no delayed completion or diagnostic appeared",
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
        except Exception:
            pass
        if os.path.exists(qmp_path):
            os.unlink(qmp_path)
        serial.logfile.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    add_runtime_arguments(parser, mode=False)
    arguments = parser.parse_args()
    run(arguments)
