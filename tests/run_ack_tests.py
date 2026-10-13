#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run source-extracted playback and capture PCM ACK fault tests."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

HARNESS = Path(__file__).with_name("virtio_snd_ack_harness.c")
OPS = "sound/virtio/virtio_pcm_ops.c"
INDIRECT = "include/sound/pcm-indirect.h"
BASELINE_DEFAULT = "9aac47803de024cd66c56cc594560c69b5a8a526"
TYPE_MARKER = "/* EXTRACTED_VIRTSND_ACK_TYPES */"
FUNCTION_MARKER = "/* EXTRACTED_VIRTSND_ACK_FUNCTIONS */"


def extract_function(source: str, name: str) -> str:
    pattern = re.compile(
        r"(?m)^(?:static\s+)?(?:[\w]+\s+|struct\s+[\w]+\s+|"
        r"[\w]+\s*\*\s*)+" + re.escape(name) + r"\s*\([^;{}]*?\)\s*\{"
    )
    match = pattern.search(source)
    if not match:
        raise RuntimeError(f"source function not found: {name}")

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
                return source[match.start() : index + 1]
        index += 1
    raise RuntimeError(f"unterminated function body: {name}")


def extract_block(source: str, marker: str) -> str:
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : source.index(";", index) + 1]
    raise RuntimeError(f"unterminated source block: {marker}")


def extract_typedef(source: str, marker: str) -> str:
    start = source.index(marker)
    return source[start : source.index(";", start) + 1]


def read_source(tree: Path, relative: str, baseline_ref: str | None) -> str:
    if baseline_ref:
        result = subprocess.run(
            ["git", "-C", str(tree), "show", f"{baseline_ref}:{relative}"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        return result.stdout
    return (tree / relative).read_text()


def resolve_commit(tree: Path, reference: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "--verify", f"{reference}^{{commit}}"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError(f"baseline ref does not resolve to a commit: {reference}")
    return result.stdout.strip()


def extracted_types(indirect: str, ops: str) -> str:
    blocks = [
        extract_block(indirect, "struct snd_pcm_indirect {"),
        extract_typedef(indirect, "typedef void (*snd_pcm_indirect_copy_t)"),
    ]
    try:
        blocks.append(extract_block(ops, "struct virtsnd_pcm_transfer {"))
    except ValueError:
        pass
    return "\n\n".join(blocks)


def extracted_functions(ops: str, indirect: str) -> str:
    functions = [
        extract_function(indirect, "snd_pcm_indirect_playback_transfer"),
        extract_function(indirect, "snd_pcm_indirect_capture_transfer"),
        extract_function(ops, "virtsnd_pcm_trans_copy"),
    ]
    try:
        functions.append(extract_function(ops, "virtsnd_pcm_transfer_failed"))
    except RuntimeError:
        pass
    functions.extend(
        [
            extract_function(ops, "virtsnd_pcm_pb_ack"),
            extract_function(ops, "virtsnd_pcm_cp_ack"),
        ]
    )
    return "\n\n".join(functions)


def compile_test(cc: list[str], harness: str, label: str, directory: Path,
         baseline: bool) -> Path:
    generated = directory / f"{label}-ack-tests.c"
    binary = directory / f"{label}-ack-tests"
    command = [
        *cc,
        "-std=gnu11",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-unused-function",
        "-Wno-sign-compare",
        "-O1",
        "-g",
        "-fno-omit-frame-pointer",
        "-fsanitize=address,undefined",
    ]
    if baseline:
        command.append("-DBASELINE")
    command.extend([str(generated), "-o", str(binary)])
    generated.write_text(harness)
    print(f"COMPILE {label}: {' '.join(command)}")
    subprocess.run(command, check=True)
    return binary


def run_test(binary: Path, label: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.setdefault("ASAN_OPTIONS", "detect_leaks=0:halt_on_error=1")
    environment.setdefault("UBSAN_OPTIONS", "halt_on_error=1:print_stacktrace=1")
    result = subprocess.run(
        [str(binary)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environment,
    )
    print(f"RUN {label}: rc={result.returncode}")
    print(result.stdout, end="")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kernel-source",
        "--kernel-tree",
        dest="kernel_source",
        type=Path,
        required=True,
        help="Linux kernel Git worktree containing the candidate source",
    )
    parser.add_argument(
        "--baseline-ref",
        default=BASELINE_DEFAULT,
        help="commit containing the pre-ACK-fix source",
    )
    args = parser.parse_args()
    tree = args.kernel_source.resolve()
    if not tree.is_dir():
        raise RuntimeError(f"kernel source tree not found: {tree}")
    cc = shlex.split(os.environ.get("CC", "cc"))
    if not cc or not shutil.which(cc[0]):
        raise RuntimeError("C compiler not found; set CC to select one")

    baseline_commit = resolve_commit(tree, args.baseline_ref)
    baseline_ops = read_source(tree, OPS, args.baseline_ref)
    baseline_indirect = read_source(tree, INDIRECT, args.baseline_ref)
    candidate_ops = read_source(tree, OPS, None)
    candidate_indirect = read_source(tree, INDIRECT, None)
    source_hashes = {
        OPS: hashlib.sha256(candidate_ops.encode()).hexdigest(),
        INDIRECT: hashlib.sha256(candidate_indirect.encode()).hexdigest(),
    }
    harness = HARNESS.read_text()
    if harness.count(TYPE_MARKER) != 1 or harness.count(FUNCTION_MARKER) != 1:
        raise RuntimeError("ACK harness must contain each extraction marker once")
    print(f"BASELINE {baseline_commit}")
    print(
        "SOURCE_SHA256 "
        f"ops={source_hashes[OPS]} indirect={source_hashes[INDIRECT]}"
    )

    with tempfile.TemporaryDirectory(prefix="virtio-snd-ack-") as temporary:
        directory = Path(temporary)
        baseline_harness = harness.replace(
            TYPE_MARKER,
            extracted_types(baseline_indirect, baseline_ops),
        ).replace(
            FUNCTION_MARKER,
            extracted_functions(baseline_ops, baseline_indirect),
        )
        candidate_harness = harness.replace(
            TYPE_MARKER,
            extracted_types(candidate_indirect, candidate_ops),
        ).replace(
            FUNCTION_MARKER,
            extracted_functions(candidate_ops, candidate_indirect),
        )
        baseline_binary = compile_test(
            cc, baseline_harness, "baseline", directory, baseline=True
        )
        candidate_binary = compile_test(
            cc, candidate_harness, "candidate", directory, baseline=False
        )

        baseline_result = run_test(baseline_binary, "baseline")
        if (
            baseline_result.returncode != 10
            or "BASELINE_EXPECTED_FAILURES=4" not in baseline_result.stdout
        ):
            raise RuntimeError("baseline did not reproduce both ACK desynchronizations")

        candidate_result = run_test(candidate_binary, "candidate")
        if (
            candidate_result.returncode
            or "ACK transfer, fail-stop, and recovery checks passed"
            not in candidate_result.stdout
        ):
            raise RuntimeError("candidate ACK transfer checks failed")

    for relative, expected_hash in source_hashes.items():
        actual_hash = hashlib.sha256((tree / relative).read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError(f"candidate source changed during test: {relative}")

    print("ACK baseline regression and candidate recovery checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=__import__("sys").stderr)
        raise SystemExit(1)
