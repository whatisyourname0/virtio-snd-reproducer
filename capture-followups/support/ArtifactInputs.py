# SPDX-License-Identifier: GPL-2.0-only
"""Validate externally supplied regression-test artifacts and resource bounds."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path


class ArtifactInputs:
    """Optional artifact pins without depending on the author's build directory."""

    @staticmethod
    def validate_arguments(args: argparse.Namespace) -> None:
        """Reject invalid test budgets before starting a guest."""
        for name in ("memory_mib", "duration", "long_duration", "reopen_duration"):
            value = getattr(args, name, None)
            if value is not None and value <= 0:
                raise ValueError(f"--{name.replace('_', '-')} must be positive")
        stall = getattr(args, "stall_seconds", None)
        if stall is not None and (not math.isfinite(stall) or stall <= 0):
            raise ValueError("--stall-seconds must be finite and positive")
        if not 0 <= args.seed <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("--seed must fit in an unsigned 64-bit integer")
        if (
            not args.ready_marker
            or "\n" in args.ready_marker
            or "\r" in args.ready_marker
        ):
            raise ValueError("--ready-marker must be one nonempty line")

    @staticmethod
    def verify(manifest: Path | None, **paths: Path) -> None:
        """Check all three artifact hashes when a pin manifest is supplied."""
        if manifest is None:
            return
        pins = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(pins, dict) or pins.get("schema_version") != 1:
            raise ValueError("artifact manifest must have schema_version 1")
        records = pins.get("sha256")
        if not isinstance(records, dict) or set(records) != set(paths):
            raise ValueError(
                "manifest sha256 must contain exactly qemu, kernel and initrd"
            )
        for name, path in paths.items():
            expected = records[name]
            if not isinstance(expected, str) or not re.fullmatch(
                r"[0-9a-f]{64}", expected
            ):
                raise ValueError(f"invalid SHA256 pin for {name}")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise ValueError(f"artifact SHA256 mismatch: {name}")
