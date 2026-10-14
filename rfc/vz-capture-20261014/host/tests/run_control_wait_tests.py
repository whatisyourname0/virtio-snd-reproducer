#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run the isolated STOP/RELEASE completion-wait contract fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "virtio_snd_ctl_wait_harness.c"
SOURCE_FILES = (
    "sound/virtio/virtio_ctl_msg.c",
    "sound/virtio/virtio_ctl_msg.h",
)
FUNCTIONS = (
    "virtsnd_ctl_msg_ref",
    "virtsnd_ctl_msg_unref",
    "virtsnd_ctl_msg_send",
    "virtsnd_ctl_msg_complete",
    "virtsnd_ctl_msg_cancel_all",
    "virtsnd_ctl_msg_cleanup",
    "virtsnd_ctl_msg_enable",
)
MARKER = "/* EXTRACTED_VIRTSND_CTL_FUNCTIONS */"


def extract_function(source: str, name: str) -> tuple[int, str]:
    signature = re.compile(
        r"(?m)^(?:static\s+)?(?:[\w]+\s+|struct\s+[\w]+\s+|"
        r"[\w]+\s*\*\s*)+" + re.escape(name) + r"\s*\([^;{}]*?\)\s*\{"
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


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kernel-tree", type=Path, required=True, help="Linux candidate source tree"
    )
    parser.add_argument(
        "--candidate-only",
        action="store_true",
        help="run candidate wait checks without the negative control",
    )
    parser.add_argument(
        "--baseline-control-msg",
        type=Path,
        help="optional pre-fix virtio_ctl_msg.c negative control",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="directory for generated source, binary, log and manifest",
    )
    args = parser.parse_args()

    if args.candidate_only and args.baseline_control_msg is not None:
        raise RuntimeError("omit --baseline-control-msg with --candidate-only")
    if not args.candidate_only and args.baseline_control_msg is None:
        raise RuntimeError("provide --baseline-control-msg or select --candidate-only")

    tree = args.kernel_tree.resolve()
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_paths = {relative: tree / relative for relative in SOURCE_FILES}
    for relative, path in source_paths.items():
        if not path.is_file():
            raise RuntimeError(f"source file not found: {relative} under {tree}")
    before = {relative: digest(path) for relative, path in source_paths.items()}
    source = source_paths["sound/virtio/virtio_ctl_msg.c"].read_text()
    harness = HARNESS.read_text()
    if harness.count(MARKER) != 1:
        raise RuntimeError(f"expected exactly one marker in {HARNESS}")
    baseline_path = (
        args.baseline_control_msg.resolve() if args.baseline_control_msg else None
    )
    baseline_source = baseline_path.read_text() if baseline_path else None

    def make_generated(source_text: str, label: str) -> Path:
        extracted = [extract_function(source_text, name) for name in FUNCTIONS]
        blocks = [body for _, body in sorted(extracted)]
        generated_path = output / f"{label}-control-wait-tests.c"
        generated_path.write_text(
            "#define VIRTSND_CTL_TEARDOWN_AVAILABLE 1\n"
            "#define VIRTSND_CTL_NEW_SEND_API 1\n"
            + harness.replace(MARKER, "\n\n".join(blocks))
        )
        return generated_path

    generated = make_generated(source, "candidate")
    baseline_generated = (
        make_generated(baseline_source, "baseline") if baseline_source else None
    )

    cc = shlex.split(os.environ.get("CC", "cc"))
    if not cc or not shutil.which(cc[0]):
        raise RuntimeError("a C compiler is required (set CC if needed)")

    def compile_one(
        source_path: Path, label: str
    ) -> tuple[list[str], Path, subprocess.CompletedProcess[str]]:
        binary_path = output / f"{label}-control-wait-tests"
        command_line = [
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
            str(source_path),
            "-o",
            str(binary_path),
        ]
        result = subprocess.run(
            command_line,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return command_line, binary_path, result

    candidate_command, candidate_binary, compile_result = compile_one(
        generated, "candidate"
    )
    baseline_command = None
    baseline_binary = None
    baseline_compile_result = None
    if baseline_generated is not None:
        baseline_command, baseline_binary, baseline_compile_result = compile_one(
            baseline_generated, "baseline"
        )
    environment = os.environ.copy()
    environment.setdefault("ASAN_OPTIONS", "detect_leaks=0:halt_on_error=1")
    environment.setdefault("UBSAN_OPTIONS", "halt_on_error=1:print_stacktrace=1")
    candidate_result = None
    baseline_result = None
    if compile_result.returncode == 0:
        candidate_result = subprocess.run(
            [str(candidate_binary)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
        )
    if (
        baseline_compile_result is not None
        and baseline_binary is not None
        and baseline_compile_result.returncode == 0
    ):
        baseline_result = subprocess.run(
            [str(baseline_binary)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
        )
    after = {relative: digest(path) for relative, path in source_paths.items()}
    if after != before:
        raise RuntimeError("source snapshot changed while the fixture was running")

    log = [f"COMPILE CANDIDATE {' '.join(candidate_command)}", compile_result.stdout]
    if baseline_command is not None and baseline_compile_result is not None:
        log.extend(
            (
                f"COMPILE BASELINE {' '.join(baseline_command)}",
                baseline_compile_result.stdout,
            )
        )
    if candidate_result is not None:
        log.extend(
            (f"RUN CANDIDATE rc={candidate_result.returncode}", candidate_result.stdout)
        )
    if baseline_result is not None:
        log.extend(
            (f"RUN BASELINE rc={baseline_result.returncode}", baseline_result.stdout)
        )
    log_path = output / "run.log"
    log_path.write_text("\n".join(log))
    manifest = {
        "schema": "virtio-snd-control-wait-source-extracted-v1",
        "source_tree": str(tree),
        "source_sha256": before,
        "source_unchanged_during_run": after == before,
        "candidate_only": args.candidate_only,
        "baseline_source": str(baseline_path) if baseline_path else None,
        "baseline_source_sha256": digest(baseline_path) if baseline_path else None,
        "extracted_functions": FUNCTIONS,
        "fixture_sha256": digest(HARNESS),
        "generated_source_sha256": digest(generated),
        "baseline_generated_source_sha256": (
            digest(baseline_generated) if baseline_generated else None
        ),
        "compile_command": candidate_command,
        "compile_returncode": compile_result.returncode,
        "baseline_compile_command": baseline_command,
        "baseline_compile_returncode": (
            baseline_compile_result.returncode if baseline_compile_result else None
        ),
        "run_returncode": None
        if candidate_result is None
        else candidate_result.returncode,
        "baseline_run_returncode": None
        if baseline_result is None
        else baseline_result.returncode,
        "assertions": None,
        "negative_control_expected_failures": [],
        "sanitizers": ["address", "undefined"],
        "kernel_built_or_booted": False,
    }
    if candidate_result is not None:
        match = re.search(r"RESULT PASS assertions=(\d+)", candidate_result.stdout)
        if candidate_result.returncode == 0 and match:
            manifest["assertions"] = int(match.group(1))
        if candidate_result.returncode != 0 or not match:
            raise RuntimeError(f"control wait fixture failed; see {log_path}")
    baseline_markers = (
        "FAIL [control-reset-stop-wakes-sender] noninterruptible_wait_calls == 1",
        "FAIL [control-reset-release-wakes-sender] noninterruptible_wait_calls == 1",
        "FAIL [control-stop-signal-timeout] rc == -ETIMEDOUT",
        "FAIL [control-release-signal-timeout] rc == -ETIMEDOUT",
        "FAIL [control-stop-signal-success] noninterruptible_wait_calls == 1",
        "FAIL [control-release-signal-success] noninterruptible_wait_calls == 1",
    )
    if baseline_result is not None:
        if baseline_result.returncode == 0:
            raise RuntimeError("v3 baseline unexpectedly passed wait-selection checks")
        if (
            "AddressSanitizer" in baseline_result.stdout
            or "runtime error:" in baseline_result.stdout
        ):
            raise RuntimeError(
                "baseline failed by sanitizer instead of wait assertions"
            )
        missing = [
            marker
            for marker in baseline_markers
            if marker not in baseline_result.stdout
        ]
        if missing:
            raise RuntimeError(
                "baseline failed to show all expected wait-selection gaps: "
                + ", ".join(missing)
            )
        manifest["negative_control_expected_failures"] = list(baseline_markers)
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(log_path.read_text(), end="")
    print(f"MANIFEST {manifest_path}")
    baseline_compile_rc = (
        baseline_compile_result.returncode if baseline_compile_result else 0
    )
    print(
        "CONTROL_WAIT_CANDIDATE_PASS"
        if args.candidate_only
        else "CONTROL_WAIT_COMPARISON_PASS"
    )
    return compile_result.returncode or baseline_compile_rc


if __name__ == "__main__":
    raise SystemExit(main())
