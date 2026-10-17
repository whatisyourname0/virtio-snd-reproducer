#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Find kernel sanitizer, lockdep, and fatal diagnostics in guest output."""

from __future__ import annotations

import re


class KernelDiagnostics:
    """Extract kernel faults from a serial transcript."""

    _PATTERN = re.compile(
        r"BUG:|kernel BUG at\b|KASAN:|WARNING:|Kernel panic|Oops:|"
        r"Internal error:\s+Oops -|"
        r"possible circular locking dependency|inconsistent lock state|"
        r"scheduling while atomic|possible recursive locking|"
        r"DEBUG_LOCKS_WARN_ON|held lock",
        re.IGNORECASE,
    )

    @classmethod
    def find(cls, text: str) -> list[str]:
        """Return matching lines in transcript order."""
        return [line for line in text.splitlines() if cls._PATTERN.search(line)]
