#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare baseline NOMMU PCM rejection with the owned-buffer candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import run_generic_tests as generic

TESTS = Path(__file__).resolve().parent
HARNESS = TESTS / "virtio_snd_nommu_lifetime_harness.c"
MARKER = "/* EXTRACTED_NOMMU_FUNCTIONS */"
SOURCE = "sound/virtio/virtio_pcm_msg.c"
FUNCTIONS = (
    "virtsnd_pcm_sg_num",
    "virtsnd_pcm_dma_pages_put",
    "virtsnd_pcm_dma_pages_get",
    "virtsnd_pcm_sg_from",
    "virtsnd_pcm_msg_alloc",
    "virtsnd_pcm_msg_free",
    "virtsnd_pcm_msg_reset",
    "virtsnd_pcm_msg_send",
    "virtsnd_pcm_msg_pending_num",
    "virtsnd_pcm_msg_cancel",
    "virtsnd_pcm_msg_complete",
)


def extract_functions(
    source: str, *, allow_missing: bool
) -> tuple[list[str], list[str]]:
    found: list[tuple[int, str, str]] = []
    for name in FUNCTIONS:
        try:
            offset, body = generic.extract_function(source, name)
        except RuntimeError:
            if allow_missing and name == "virtsnd_pcm_msg_cancel":
                continue
            raise
        found.append((offset, name, body))
    found.sort()
    return [body for _, _, body in found], [name for _, name, _ in found]


def compile_and_run(
    cc: list[str], source_blocks: list[str], label: str, temp: Path, *, baseline: bool
) -> subprocess.CompletedProcess[str]:
    harness = HARNESS.read_text()
    if harness.count(MARKER) != 1:
        raise RuntimeError("expected exactly one NOMMU production-code marker")
    generated = temp / f"{label}-nommu-tests.c"
    binary = temp / f"{label}-nommu-tests"
    prefix = "#define TEST_BASELINE 1\n" if baseline else ""
    generated.write_text(prefix + harness.replace(MARKER, "\n\n".join(source_blocks)))
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
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environment,
    )


def run_candidate_only(
    source_tree: Path,
    compiler: list[str],
    manifest_out: Path | None,
) -> int:
    candidate_path = source_tree / SOURCE
    candidate_text = candidate_path.read_text()
    candidate_blocks, candidate_functions = extract_functions(
        candidate_text, allow_missing=False
    )
    candidate_hash = hashlib.sha256(candidate_text.encode()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="virtio-snd-nommu-") as temporary:
        result = compile_and_run(
            compiler,
            candidate_blocks,
            "candidate",
            Path(temporary),
            baseline=False,
        )
    if result.returncode != 0 or "RESULT PASS assertions=" not in result.stdout:
        raise RuntimeError("candidate failed NOMMU PCM lifetime coverage")
    if "AddressSanitizer" in result.stdout or "runtime error:" in result.stdout:
        raise RuntimeError("candidate failed under a sanitizer")
    if hashlib.sha256(candidate_path.read_bytes()).hexdigest() != candidate_hash:
        raise RuntimeError("PCM source changed while NOMMU tests were running")
    if manifest_out:
        output = manifest_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "schema": "virtio-snd-nommu-candidate-tests-v1",
                    "candidate_tree": str(source_tree),
                    "candidate_only": True,
                    "candidate_source_sha256": candidate_hash,
                    "candidate_extracted_functions": candidate_functions,
                    "candidate_result": result.stdout.splitlines()[-1],
                    "kernel_built_or_booted": False,
                    "sanitizers": ["address", "undefined"],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        print(f"SOURCE_MANIFEST {output}")
    print("NOMMU_CANDIDATE_PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kernel-tree",
        "--source-tree",
        dest="kernel_tree",
        type=Path,
        required=True,
        help="Linux kernel candidate source tree",
    )
    parser.add_argument(
        "--baseline-ref", "--baseline", help="Git ref before the NOMMU PCM fix"
    )
    parser.add_argument(
        "--baseline-tree",
        type=Path,
        help="filesystem source tree before the NOMMU PCM fix",
    )
    parser.add_argument(
        "--candidate-only",
        action="store_true",
        help="run candidate coverage without a baseline",
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        help="optional JSON result and source hash manifest",
    )
    args = parser.parse_args()

    source_tree = args.kernel_tree.resolve()
    if not source_tree.is_dir():
        raise RuntimeError(f"kernel source tree not found: {source_tree}")
    compiler = shlex.split(os.environ.get("CC", "cc"))
    if not compiler or not shutil.which(compiler[0]):
        raise RuntimeError("a C compiler is required (set CC if needed)")
    if args.candidate_only:
        if args.baseline_ref or args.baseline_tree:
            raise RuntimeError("omit baseline inputs with --candidate-only")
        return run_candidate_only(source_tree, compiler, args.manifest_out)
    if (args.baseline_ref is None) == (args.baseline_tree is None):
        raise RuntimeError("provide exactly one --baseline-ref or --baseline-tree")
    baseline_tree = args.baseline_tree.resolve() if args.baseline_tree else None
    baseline_commit = (
        generic.resolve_commit(source_tree, args.baseline_ref)
        if args.baseline_ref
        else None
    )
    candidate_text = (source_tree / SOURCE).read_text()
    baseline_text = generic.read_baseline(
        source_tree, args.baseline_ref, SOURCE, baseline_tree
    )
    candidate_blocks, candidate_functions = extract_functions(
        candidate_text, allow_missing=False
    )
    baseline_blocks, baseline_functions = extract_functions(
        baseline_text, allow_missing=True
    )
    source_hashes = {
        "baseline": hashlib.sha256(baseline_text.encode()).hexdigest(),
        "candidate": hashlib.sha256(candidate_text.encode()).hexdigest(),
    }
    with tempfile.TemporaryDirectory(prefix="virtio-snd-nommu-") as temporary:
        temp = Path(temporary)
        baseline_result = compile_and_run(
            compiler, baseline_blocks, "baseline", temp, baseline=True
        )
        candidate_result = compile_and_run(
            compiler, candidate_blocks, "candidate", temp, baseline=False
        )

    print("--- BASELINE NOMMU OUTPUT ---")
    print(f"returncode={baseline_result.returncode}")
    print(baseline_result.stdout, end="")
    print("--- CANDIDATE NOMMU OUTPUT ---")
    print(f"returncode={candidate_result.returncode}")
    print(candidate_result.stdout, end="")

    if (
        baseline_result.returncode != 0
        or "RESULT BASELINE_REPRODUCED rc=-22" not in baseline_result.stdout
    ):
        raise RuntimeError("baseline did not reproduce the NOMMU vmalloc rejection")
    if (
        candidate_result.returncode != 0
        or "RESULT PASS assertions=" not in candidate_result.stdout
    ):
        raise RuntimeError("candidate failed NOMMU PCM lifetime coverage")
    if (
        "AddressSanitizer" in candidate_result.stdout
        or "runtime error:" in candidate_result.stdout
    ):
        raise RuntimeError("candidate failed under a sanitizer")

    after_hash = hashlib.sha256((source_tree / SOURCE).read_bytes()).hexdigest()
    if after_hash != source_hashes["candidate"]:
        raise RuntimeError("PCM source changed while NOMMU tests were running")

    if args.manifest_out:
        output = args.manifest_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema": "virtio-snd-nommu-pcm-lifetime-tests-v1",
            "candidate": str(source_tree),
            "baseline_ref": args.baseline_ref,
            "baseline_tree": str(baseline_tree) if baseline_tree else None,
            "baseline_commit": baseline_commit,
            "source": SOURCE,
            "source_sha256": source_hashes,
            "baseline_extracted_functions": baseline_functions,
            "candidate_extracted_functions": candidate_functions,
            "baseline_result": "RESULT BASELINE_REPRODUCED rc=-22",
            "candidate_result": candidate_result.stdout.splitlines()[-1],
            "candidate_cases": [
                "allocation-unwind-and-recovery",
                "playback-full-period-copy",
                "capture-capacity-clamp-and-copyback",
                "stopped-completion-after-alsa-free",
                "reset-cancel-count-and-wakeup",
            ],
            "kernel_built_or_booted": False,
            "source_unchanged_during_run": True,
        }
        output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        print(f"SOURCE_MANIFEST {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
