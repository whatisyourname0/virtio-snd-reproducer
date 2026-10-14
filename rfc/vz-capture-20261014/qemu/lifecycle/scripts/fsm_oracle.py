# SPDX-License-Identifier: GPL-2.0-only
"""Strict log checks for the lifecycle probe's timed-out reader."""

from __future__ import annotations

import re

_TIMEOUT_MARKERS = {
    "stop": "FSM_PAUSE_PUSH_RETURN",
    "release": "FSM_PAUSE_PUSH_RETURN",
    "prepare": "FSM_PAUSE_RELEASE_RETURN",
    "start": "FSM_PAUSE_RELEASE_RETURN",
}
_TIMEOUT_RE = re.compile(
    r"(?m)^FSM_PAUSE_(?:PUSH|RELEASE)_RETURN rc=(-?\d+) "
    r"elapsed_ms=(\d+) expected_timeout=(\d+)\r?$"
)
_READER_RE = re.compile(
    r"(?m)^FSM_READER_ASSERT first_rc=(-?\d+) final_rc=(-?\d+) "
    r"frames=(\d+) state=(\S+) first_ok=(\d+) final_ok=(\d+) "
    r"no_frames=(\d+) xrun=(\d+)\r?$"
)


def validate_timeout_result(text: str, mode: str) -> None:
    """Require the selected pause operation to time out within its bound."""
    if mode not in _TIMEOUT_MARKERS:
        raise AssertionError(f"unknown FSM mode: {mode}")
    matches = list(_TIMEOUT_RE.finditer(text))
    if len(matches) != 1:
        raise AssertionError("expected exactly one complete FSM pause result")
    match = matches[0]
    if not match.group(0).startswith(_TIMEOUT_MARKERS[mode]):
        raise AssertionError(f"wrong timeout operation for FSM mode {mode}")
    result, elapsed, expected = map(int, match.groups())
    if result != -110 or expected != 1 or not 800 <= elapsed <= 2500:
        raise AssertionError(
            "pause did not return the bounded -ETIMEDOUT result: "
            f"rc={result} elapsed_ms={elapsed} expected_timeout={expected}"
        )


def validate_reader_result(text: str, mode: str) -> None:
    """Require fail-closed reader results and an XRUN with no delivered frames."""
    if mode not in _TIMEOUT_MARKERS:
        raise AssertionError(f"unknown FSM mode: {mode}")
    matches = list(_READER_RE.finditer(text))
    if len(matches) != 1:
        raise AssertionError("expected exactly one complete FSM reader assertion")
    groups = matches[0].groups()
    first_rc, final_rc, frames = map(int, groups[:3])
    state = groups[3]
    first_ok, final_ok, no_frames, xrun = map(int, groups[4:])
    first_allowed = {-32}
    if mode in ("stop", "release"):
        first_allowed.add(-5)
    if first_rc not in first_allowed:
        raise AssertionError(f"unexpected first reader result: {first_rc}")
    if final_rc != -32:
        raise AssertionError(f"final reader result must be -EPIPE, got {final_rc}")
    if frames != 0:
        raise AssertionError(f"reader delivered frames after timeout: {frames}")
    if state != "XRUN":
        raise AssertionError(f"reader stream must be XRUN, got {state}")
    if (first_ok, final_ok, no_frames, xrun) != (1, 1, 1, 1):
        raise AssertionError("probe's reader assertions did not all pass")


def validate_reprobe_rejection(text: str) -> None:
    """Require prepare to fail specifically with the driver's -EIO contract."""
    marker = "FSM_PREPARE_WITHOUT_REPROBE rc=-5 rejected=1 expected_eio=1"
    if text.count(marker) != 1:
        raise AssertionError("prepare without reprobe must fail with -EIO")
