# SPDX-License-Identifier: GPL-2.0-only
"""Strictness checks for the timed-out FSM reader contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fsm_oracle import (
    validate_reader_result,
    validate_reprobe_rejection,
    validate_timeout_result,
)


@pytest.mark.parametrize(
    ("mode", "first_rc"),
    [("stop", -5), ("stop", -32), ("release", -5), ("release", -32)],
)
def test_stop_and_release_allow_the_source_supported_first_error(
    mode: str, first_rc: int
) -> None:
    """STOP and RELEASE can expose XRUN or an earlier wait timeout first."""
    text = (
        f"FSM_READER_ASSERT first_rc={first_rc} final_rc=-32 frames=0 "
        "state=XRUN first_ok=1 final_ok=1 no_frames=1 xrun=1\n"
    )
    validate_reader_result(text, mode)


@pytest.mark.parametrize("mode", ["prepare", "start"])
def test_prepare_and_start_keep_first_epipe_contract(mode: str) -> None:
    """PREPARE and START still require EPIPE as the first read result."""
    validate_reader_result(
        "FSM_READER_ASSERT first_rc=-32 final_rc=-32 frames=0 state=XRUN "
        "first_ok=1 final_ok=1 no_frames=1 xrun=1\n",
        mode,
    )


@pytest.mark.parametrize(
    "line",
    [
        (
            "FSM_READER_ASSERT first_rc=0 final_rc=-32 frames=1 state=XRUN "
            "first_ok=1 final_ok=1 no_frames=0 xrun=1\n"
        ),
        (
            "FSM_READER_ASSERT first_rc=-32 final_rc=0 frames=0 state=XRUN "
            "first_ok=1 final_ok=1 no_frames=1 xrun=1\n"
        ),
        (
            "FSM_READER_ASSERT first_rc=-32 final_rc=-32 frames=1 state=XRUN "
            "first_ok=1 final_ok=1 no_frames=0 xrun=1\n"
        ),
        (
            "FSM_READER_ASSERT first_rc=-32 final_rc=-32 frames=0 state=RUNNING "
            "first_ok=1 final_ok=1 no_frames=1 xrun=0\n"
        ),
        (
            "FSM_READER_ASSERT first_rc=-5 final_rc=-32 frames=0 state=XRUN "
            "first_ok=1 final_ok=1 no_frames=1 xrun=1\n"
        ),
    ],
)
def test_reader_oracle_rejects_successful_frames_or_wrong_final_state(
    line: str,
) -> None:
    """Positive frames, non-EPIPE finals, and unsupported first errors fail."""
    with pytest.raises(AssertionError):
        validate_reader_result(line, "prepare")


def test_timeout_oracle_rejects_a_successful_pause() -> None:
    """A successful pause cannot satisfy the injected timeout case."""
    with pytest.raises(AssertionError, match="-ETIMEDOUT"):
        validate_timeout_result(
            "FSM_PAUSE_PUSH_RETURN rc=0 elapsed_ms=1020 expected_timeout=0\n",
            "stop",
        )


@pytest.mark.parametrize("elapsed_ms", [799, 2501])
def test_timeout_oracle_keeps_the_historical_elapsed_bound(elapsed_ms: int) -> None:
    """Timeouts outside the probe's original 800–2500 ms bound fail."""
    with pytest.raises(AssertionError, match="bounded -ETIMEDOUT"):
        validate_timeout_result(
            f"FSM_PAUSE_PUSH_RETURN rc=-110 elapsed_ms={elapsed_ms} "
            "expected_timeout=1\n",
            "stop",
        )


def test_reprobe_oracle_requires_driver_eio() -> None:
    """An unrelated failure cannot stand in for the reset-required result."""
    validate_reprobe_rejection(
        "FSM_PREPARE_WITHOUT_REPROBE rc=-5 rejected=1 expected_eio=1\n"
    )
    with pytest.raises(AssertionError, match="must fail with -EIO"):
        validate_reprobe_rejection(
            "FSM_PREPARE_WITHOUT_REPROBE rc=-32 rejected=1 expected_eio=0\n"
        )
