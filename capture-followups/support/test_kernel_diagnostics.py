#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Focused checks for QEMU guest kernel diagnostic detection."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from KernelDiagnostics import KernelDiagnostics


class KernelDiagnosticsTest(unittest.TestCase):
    """Verify diagnostic detection and normal sanitizer boot markers."""

    def test_finds_ack_fixture_kernel_diagnostic_patterns(self) -> None:
        lines = [
            "[    1.000] KASAN: slab-out-of-bounds in virtio_snd_pcm",
            "[    1.500] BUG: kernel NULL pointer dereference at 00000000",
            "[    1.750] kernel BUG at drivers/virtio/virtio_snd.c:123!",
            "[    1.875] Internal error: Oops - undefined instruction: 0000 [#1] SMP",
            "[    2.000] WARNING: CPU: 0 PID: 1 at kernel/locking/lockdep.c:1",
            "[    3.000] possible circular locking dependency detected",
            "[    4.000] inconsistent lock state",
            "[    5.000] scheduling while atomic",
            "[    6.000] possible recursive locking",
            "[    7.000] DEBUG_LOCKS_WARN_ON(1)",
            "[    8.000] held lock freed",
            "[    9.000] Kernel panic - not syncing: test panic",
            "[   10.000] Oops: 0000 [#1] SMP",
        ]

        self.assertEqual(KernelDiagnostics.find("\n".join(lines)), lines)

    def test_ignores_clean_kasan_lockdep_and_panic_boot_parameters(self) -> None:
        log = "\n".join(
            (
                "Linux version 6.17.0 (builder@host)",
                "KernelAddressSanitizer initialized (generic)",
                "Lock dependency validator: Copyright (c) 2006 Red Hat, Inc.",
                "kernel bug tracker at https://example.invalid/BUG",
                "Internal error: Oops handled by the exception fixup path",
                "Kernel command line: console=ttyAMA0 panic_on_warn=1 oops=panic panic=-1",
                "VIRTIO_SND_SEQUENCE_START epoch=1 first_frame=0",
            )
        )

        self.assertEqual(KernelDiagnostics.find(log), [])


if __name__ == "__main__":
    unittest.main()
