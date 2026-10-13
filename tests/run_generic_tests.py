#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile fault-injection tests from the baseline and working-tree driver."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

LAB = Path(__file__).resolve().parent
HARNESS = LAB / "virtio_snd_lifetime_harness.c"
CTL_HARNESS = LAB / "virtio_snd_ctl_lifetime_harness.c"
REMOVE_HARNESS = LAB / "virtio_snd_remove_order_harness.c"
MARKER = "/* EXTRACTED_VIRTSND_FUNCTIONS */"
CTL_MARKER = "/* EXTRACTED_VIRTSND_CTL_FUNCTIONS */"
REMOVE_MARKER = "/* EXTRACTED_VIRTSND_REMOVE */"
BASELINE_DEFAULT = "HEAD~5"
EXPECTED_BASELINE_FAILURES = {
    "partial-send": (
        "vqueue->kicks == 1",
        "fixture.messages[1].length == 0",
        "vqueue->pending == 2",
    ),
    "first-add": (
        "fixture.messages[0].length == 0",
    ),
    "start-allocation": (
        "fixture.runtime.stop_operating",
        "fixture.vss.msg_count == 0",
        "fixture.snd.rx_queue.vqueue->pending == 0",
    ),
    "start-send": (
        "!fixture.vss.xfer_enabled",
        "fixture.runtime.stop_operating",
        "fixture.vss.elapsed_period.schedules == 0",
    ),
    "prepare-after-release-error": (
        "rc == -EBADFD",
        "prepare_commands == 0",
    ),
    "sync-stop-idle": (
        "!fixture.vss.xfer_enabled",
    ),
}

REQUIRED_FUNCTIONS = (
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_msg_send"),
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_msg_pending_num"),
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_msg_complete"),
    ("sound/virtio/virtio_pcm_ops.c", "virtsnd_pcm_trigger"),
    ("sound/virtio/virtio_pcm_ops.c", "virtsnd_pcm_sync_stop"),
    ("sound/virtio/virtio_pcm_ops.c", "virtsnd_pcm_prepare"),
    ("sound/virtio/virtio_pcm_ops.c", "virtsnd_pcm_open"),
    ("sound/virtio/virtio_pcm_ops.c", "virtsnd_pcm_hw_params"),
)

# Helpers can be introduced while repairing the START rollback path. Keep the
# test bound to their production definitions when they exist in the candidate.
OPTIONAL_FUNCTIONS = (
    ("sound/virtio/virtio_pcm_ops.c", "virtsnd_pcm_start_failed"),
    ("sound/virtio/virtio_pcm_ops.c", "virtsnd_pcm_send_cmd"),
    ("sound/virtio/virtio_pcm_ops.c", "virtsnd_pcm_start_rollback"),
    ("sound/virtio/virtio_pcm_ops.c", "virtsnd_pcm_release_drain"),
)

REQUIRED_CTL_FUNCTIONS = (
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_ref"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_unref"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_send"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_complete"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_cancel_all"),
)
OPTIONAL_CTL_FUNCTIONS = (
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_cleanup"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_enable"),
)
REQUIRED_REMOVE_FUNCTIONS = (
    ("sound/virtio/virtio_card.c", "virtsnd_remove"),
)


def extract_function(source: str, name: str) -> tuple[int, str]:
    signature = re.compile(
        r"(?m)^(?:static\s+)?(?:[\w]+\s+|struct\s+[\w]+\s+|"
        r"[\w]+\s*\*\s*)+"
        + re.escape(name)
        + r"\s*\([^;{}]*?\)\s*\{"
    )
    match = signature.search(source)
    if not match:
        raise RuntimeError(f"function definition not found: {name}")

    opening = source.find("{", match.start(), match.end())
    depth = 0
    state = "code"
    escaped = False
    index = opening
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "line_comment":
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            if char == "*" and following == "/":
                state = "code"
                index += 1
        elif state == "string":
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                state = "code"
        elif state == "char":
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "'":
                state = "code"
        elif char == "/" and following == "/":
            state = "line_comment"
            index += 1
        elif char == "/" and following == "*":
            state = "block_comment"
            index += 1
        elif char == '"':
            state = "string"
        elif char == "'":
            state = "char"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return match.start(), source[match.start() : index + 1]
        index += 1
    raise RuntimeError(f"unterminated function body: {name}")


def read_baseline(source_tree: Path, baseline: str, relative: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_tree), "show", f"{baseline}:{relative}"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout


def resolve_commit(source_tree: Path, reference: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_tree), "rev-parse", "--verify", f"{reference}^{{commit}}"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError(f"baseline ref does not resolve to a commit: {reference}")
    return result.stdout.strip()


def extract_set(
    sources: dict[str, str], *, require_optional: bool,
    required: tuple[tuple[str, str], ...] = REQUIRED_FUNCTIONS,
    optional: tuple[tuple[str, str], ...] = OPTIONAL_FUNCTIONS,
) -> tuple[list[str], list[str]]:
    file_order = {relative: rank for rank, relative in enumerate(sources)}
    found: list[tuple[int, int, str]] = []
    extracted_names: list[str] = []
    for relative, name in (*required, *optional):
        source = sources[relative]
        try:
            offset, body = extract_function(source, name)
        except RuntimeError:
            if (relative, name) in required or require_optional:
                raise
            continue
        # Keep the definitions in their original file order so extracted
        # helpers are available before callers when the kernel source does so.
        found.append((file_order[relative], offset, body))
        extracted_names.append(name)
    return [body for _, _, body in sorted(found)], extracted_names


def compile_and_run(
    cc: list[str], harness_path: Path, marker: str, source_blocks: list[str],
    label: str, temp: Path, *, candidate_ctl: bool = False
) -> subprocess.CompletedProcess[str]:
    harness = harness_path.read_text()
    if harness.count(marker) != 1:
        raise RuntimeError("expected exactly one production-code marker")
    generated = temp / f"{label}-lifetime-tests.c"
    binary = temp / f"{label}-lifetime-tests"
    extracted = "\n\n".join(source_blocks)
    prefix = ""
    if candidate_ctl:
        prefix = (
            "#define VIRTSND_CTL_TEARDOWN_AVAILABLE 1\n"
            "#define VIRTSND_CTL_NEW_SEND_API 1\n"
        )
    generated.write_text(prefix + harness.replace(marker, extracted))
    command = [
        *cc,
        "-std=gnu11",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-unused-function",
        "-O1",
        "-g",
        "-fno-omit-frame-pointer",
        "-fsanitize=address,undefined",
        str(generated),
        "-o",
        str(binary),
    ]
    print(f"COMPILE {label}: {' '.join(command)}")
    subprocess.run(command, check=True)
    environment = os.environ.copy()
    environment.setdefault("ASAN_OPTIONS", "detect_leaks=0:halt_on_error=1")
    environment.setdefault("UBSAN_OPTIONS", "halt_on_error=1:print_stacktrace=1")
    return subprocess.run(
        [str(binary)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environment,
    )


def assertion_total(result: subprocess.CompletedProcess[str]) -> int:
    match = re.search(r"RESULT PASS assertions=(\d+)", result.stdout)
    if not match:
        raise RuntimeError("candidate output is missing its passing assertion count")
    return int(match.group(1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel-tree", "--source-tree", dest="kernel_tree", type=Path,
                        required=True, help="patched Linux source Git worktree")
    parser.add_argument("--baseline-ref", "--baseline", dest="baseline_ref",
                        default=BASELINE_DEFAULT,
                        help="Git commit/ref containing the pre-series kernel source")
    parser.add_argument("--manifest-out", type=Path,
                        help="optional JSON file for extracted-source hashes and results")
    args = parser.parse_args()

    source_tree = args.kernel_tree.resolve()
    cc = shlex.split(os.environ.get("CC", "cc"))
    if not cc or not shutil.which(cc[0]):
        raise RuntimeError("a C compiler is required (set CC if needed)")
    if not source_tree.is_dir():
        raise RuntimeError(f"kernel source tree not found: {source_tree}")
    baseline_commit = resolve_commit(source_tree, args.baseline_ref)

    source_names = sorted({
        relative
        for relative, _ in (
            REQUIRED_FUNCTIONS + OPTIONAL_FUNCTIONS +
            REQUIRED_CTL_FUNCTIONS + OPTIONAL_CTL_FUNCTIONS
            + REQUIRED_REMOVE_FUNCTIONS
        )
    })
    candidate_bytes = {
        relative: (source_tree / relative).read_bytes() for relative in source_names
    }
    candidate_sources = {
        relative: source.decode() for relative, source in candidate_bytes.items()
    }
    baseline_sources = {
        relative: read_baseline(source_tree, args.baseline_ref, relative)
        for relative in source_names
    }
    baseline_blocks, baseline_functions = extract_set(
        baseline_sources, require_optional=False
    )
    candidate_blocks, candidate_functions = extract_set(
        candidate_sources, require_optional=False
    )
    baseline_ctl_blocks, baseline_ctl_functions = extract_set(
        baseline_sources,
        require_optional=False,
        required=REQUIRED_CTL_FUNCTIONS,
        optional=OPTIONAL_CTL_FUNCTIONS,
    )
    candidate_ctl_blocks, candidate_ctl_functions = extract_set(
        candidate_sources,
        require_optional=False,
        required=REQUIRED_CTL_FUNCTIONS,
        optional=OPTIONAL_CTL_FUNCTIONS,
    )
    baseline_remove_blocks, baseline_remove_functions = extract_set(
        baseline_sources,
        require_optional=False,
        required=REQUIRED_REMOVE_FUNCTIONS,
        optional=(),
    )
    candidate_remove_blocks, candidate_remove_functions = extract_set(
        candidate_sources,
        require_optional=False,
        required=REQUIRED_REMOVE_FUNCTIONS,
        optional=(),
    )

    candidate_hashes = {
        relative: hashlib.sha256(source).hexdigest()
        for relative, source in candidate_bytes.items()
    }
    baseline_hashes = {
        relative: hashlib.sha256(source.encode()).hexdigest()
        for relative, source in baseline_sources.items()
    }

    with tempfile.TemporaryDirectory(prefix="virtio-snd-lifetime-") as temporary:
        temp = Path(temporary)
        baseline_result = compile_and_run(
            cc, HARNESS, MARKER, baseline_blocks, "baseline", temp
        )
        candidate_result = compile_and_run(
            cc, HARNESS, MARKER, candidate_blocks, "candidate", temp
        )
        baseline_ctl_result = compile_and_run(
            cc, CTL_HARNESS, CTL_MARKER, baseline_ctl_blocks,
            "baseline-ctl", temp
        )
        candidate_ctl_result = compile_and_run(
            cc, CTL_HARNESS, CTL_MARKER, candidate_ctl_blocks,
            "candidate-ctl", temp, candidate_ctl=True
        )
        baseline_remove_result = compile_and_run(
            cc, REMOVE_HARNESS, REMOVE_MARKER, baseline_remove_blocks,
            "baseline-remove", temp
        )
        candidate_remove_result = compile_and_run(
            cc, REMOVE_HARNESS, REMOVE_MARKER, candidate_remove_blocks,
            "candidate-remove", temp
        )

    print("--- BASELINE OUTPUT ---")
    print(f"returncode={baseline_result.returncode}")
    print(baseline_result.stdout, end="")
    print("--- CANDIDATE OUTPUT ---")
    print(f"returncode={candidate_result.returncode}")
    print(candidate_result.stdout, end="")
    print("--- BASELINE CONTROL OUTPUT ---")
    print(f"returncode={baseline_ctl_result.returncode}")
    print(baseline_ctl_result.stdout, end="")
    print("--- CANDIDATE CONTROL OUTPUT ---")
    print(f"returncode={candidate_ctl_result.returncode}")
    print(candidate_ctl_result.stdout, end="")
    print("--- BASELINE REMOVE OUTPUT ---")
    print(f"returncode={baseline_remove_result.returncode}")
    print(baseline_remove_result.stdout, end="")
    print("--- CANDIDATE REMOVE OUTPUT ---")
    print(f"returncode={candidate_remove_result.returncode}")
    print(candidate_remove_result.stdout, end="")

    if baseline_result.returncode == 0:
        raise RuntimeError("fault regression cases unexpectedly pass on baseline")
    for case, assertions in EXPECTED_BASELINE_FAILURES.items():
        case_failures = [
            line for line in baseline_result.stdout.splitlines()
            if line.startswith(f"FAIL [{case}]")
        ]
        if not case_failures:
            raise RuntimeError(f"baseline did not reproduce expected case: {case}")
        for assertion in assertions:
            if not any(assertion in line for line in case_failures):
                raise RuntimeError(
                    f"baseline did not fail expected assertion for {case}: {assertion}"
                )
    for marker in ("AddressSanitizer", "runtime error:"):
        if marker in baseline_result.stdout:
            raise RuntimeError(f"baseline failed by sanitizer instead of assertion: {marker}")
    if candidate_result.returncode != 0 or "RESULT PASS" not in candidate_result.stdout:
        raise RuntimeError("working-tree candidate failed the lifetime regression suite")
    if baseline_ctl_result.returncode == 0 or not any(
        marker in baseline_ctl_result.stdout
        for marker in ("heap-use-after-free", "heap-buffer-overflow")
    ):
        raise RuntimeError("baseline did not reproduce the control-message lifetime fault")
    if candidate_ctl_result.returncode != 0 or "RESULT PASS" not in candidate_ctl_result.stdout:
        raise RuntimeError("working-tree candidate failed the control-message lifetime suite")
    if baseline_remove_result.returncode == 0 or (
        "FAIL [remove-control-order] card_free_control_rc == 0"
        not in baseline_remove_result.stdout
    ):
        raise RuntimeError("baseline did not reproduce the remove ordering contract failure")
    if candidate_remove_result.returncode != 0 or "RESULT PASS" not in candidate_remove_result.stdout:
        raise RuntimeError("working-tree candidate failed the removal order contract")

    after_hashes = {
        relative: hashlib.sha256((source_tree / relative).read_bytes()).hexdigest()
        for relative in source_names
    }
    if after_hashes != candidate_hashes:
        raise RuntimeError("kernel candidate source changed during the test run")

    manifest = {
        "schema": "virtio-snd-source-extracted-lifetime-fault-tests-v2",
        "candidate": str(source_tree),
        "baseline_ref": args.baseline_ref,
        "baseline_commit": baseline_commit,
        "candidate_sha256": candidate_hashes,
        "baseline_sha256": baseline_hashes,
        "candidate_extracted_functions": candidate_functions,
        "baseline_extracted_functions": baseline_functions,
        "candidate_control_extracted_functions": candidate_ctl_functions,
        "baseline_control_extracted_functions": baseline_ctl_functions,
        "candidate_remove_extracted_functions": candidate_remove_functions,
        "baseline_remove_extracted_functions": baseline_remove_functions,
        "expected_baseline_failures": {
            case: list(assertions)
            for case, assertions in EXPECTED_BASELINE_FAILURES.items()
        },
        "expected_baseline_control_failure": "AddressSanitizer: heap-use-after-free",
        "expected_baseline_remove_failure":
            "FAIL [remove-control-order] card_free_control_rc == 0",
        "candidate_assertions": {
            "pcm": assertion_total(candidate_result),
            "control": assertion_total(candidate_ctl_result),
            "remove_order": assertion_total(candidate_remove_result),
        },
        "baseline_returncode": baseline_result.returncode,
        "candidate_returncode": candidate_result.returncode,
        "baseline_control_returncode": baseline_ctl_result.returncode,
        "candidate_control_returncode": candidate_ctl_result.returncode,
        "baseline_remove_returncode": baseline_remove_result.returncode,
        "candidate_remove_returncode": candidate_remove_result.returncode,
        "sanitizers": ["address", "undefined"],
        "kernel_built_or_booted": False,
        "source_unchanged_during_run": True,
    }
    if args.manifest_out:
        output = args.manifest_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        print(f"SOURCE_MANIFEST {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
