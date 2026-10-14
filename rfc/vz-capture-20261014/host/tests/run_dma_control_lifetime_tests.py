#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile DMA and external-SG lifetime tests from kernel source functions."""

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
DMA_HARNESS = LAB / "virtio_snd_dma_lifetime_harness.c"
CTL_HARNESS = LAB / "virtio_snd_payload_lifetime_harness.c"

DMA_FILES = (
    "sound/virtio/virtio_pcm_msg.c",
    "sound/virtio/virtio_pcm_ops.c",
    "sound/virtio/virtio_pcm.h",
    "sound/core/pcm_native.c",
)
CTL_FILES = (
    "sound/virtio/virtio_ctl_msg.c",
    "sound/virtio/virtio_kctl.c",
)
DMA_FUNCTIONS = (
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_sg_num"),
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_dma_pages_put"),
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_dma_pages_get"),
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_sg_from"),
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_msg_alloc"),
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_msg_free"),
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_msg_send"),
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_msg_pending_num"),
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_msg_complete"),
    ("sound/virtio/virtio_pcm_msg.c", "virtsnd_pcm_msg_cancel"),
    ("sound/virtio/virtio_pcm_ops.c", "virtsnd_pcm_pause_restart_enabled"),
    ("sound/virtio/virtio_pcm_ops.c", "virtsnd_pcm_sync_stop"),
    ("sound/virtio/virtio_pcm_ops.c", "virtsnd_pcm_hw_free"),
    ("sound/core/pcm_native.c", "do_hw_free"),
)
CTL_FUNCTIONS = (
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_sg_init"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_ref"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_unref"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_request"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_response"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_request_data"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_response_data"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_alloc"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_send"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_complete"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_cancel_all"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_cleanup"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_msg_enable"),
    ("sound/virtio/virtio_ctl_msg.c", "virtsnd_ctl_query_info"),
    ("sound/virtio/virtio_kctl.c", "virtsnd_kctl_tlv_op"),
    ("sound/virtio/virtio_kctl.c", "virtsnd_kctl_get_enum_items"),
)


def extract_braced(source: str, pattern: str, label: str) -> str:
    match = re.search(pattern, source, re.MULTILINE)
    if not match:
        raise RuntimeError(f"source block not found: {label}")
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
    raise RuntimeError(f"unterminated source block: {label}")


def extract_function(source: str, name: str) -> str:
    signature = re.compile(
        r"(?m)^(?:static\s+)?(?:[\w]+\s+|struct\s+[\w]+\s+|"
        r"[\w]+\s*\*\s*)+" + re.escape(name) + r"\s*\([^;{}]*?\)\s*\{"
    )
    return extract_braced(source, signature.pattern, name)


def load_sources(
    tree: Path,
    files: tuple[str, ...],
    baseline_ref: str | None,
    baseline_tree: Path | None = None,
) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for relative in files:
        if baseline_tree is not None:
            path = baseline_tree / relative
            if not path.is_file():
                raise RuntimeError(f"baseline source file not found: {path}")
            loaded[relative] = path.read_text()
        elif baseline_ref:
            result = subprocess.run(
                ["git", "-C", str(tree), "show", f"{baseline_ref}:{relative}"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )
            loaded[relative] = result.stdout
        else:
            loaded[relative] = (tree / relative).read_text()
    return loaded


def resolve_commit(tree: Path, reference: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "--verify", f"{reference}^{{commit}}"],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(f"baseline ref does not resolve to a commit: {reference}")
    return result.stdout.strip()


def functions_in_order(
    sources: dict[str, str],
    specifications: tuple[tuple[str, str], ...],
    *,
    optional: frozenset[str] = frozenset(),
) -> list[str]:
    ranks = {path: rank for rank, path in enumerate(sources)}
    found: list[tuple[int, int, str]] = []
    for relative, name in specifications:
        try:
            body = extract_function(sources[relative], name)
            offset = sources[relative].find(body)
        except RuntimeError:
            if name in optional:
                continue
            raise
        found.append((ranks[relative], offset, body))
    return [body for _, _, body in sorted(found)]


def type_block(source: str, name: str, keyword: str = "struct") -> str:
    pattern = rf"(?m)^{keyword}\s+{re.escape(name)}\s*\{{"
    return extract_braced(source, pattern, f"{keyword} {name}") + ";"


def compose(
    harness_path: Path,
    marker: str,
    blocks: list[str],
    output: Path,
    defines: tuple[str, ...],
) -> Path:
    harness = harness_path.read_text()
    if harness.count(marker) != 1:
        raise RuntimeError(f"expected one marker in {harness_path}")
    source = harness.replace(marker, "\n\n".join(blocks))
    if defines:
        source = source.replace("/* TEST_DEFINES */", "\n".join(defines))
    output.write_text(source)
    return output


def compile_binary(cc: list[str], source: Path, binary: Path) -> None:
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
        str(source),
        "-o",
        str(binary),
    ]
    print(f"COMPILE {binary.name}: {' '.join(command)}")
    subprocess.run(command, check=True)


def run(
    binary: Path,
    *arguments: str,
    expect_uaf: bool = False,
    expect_alignment_ubsan: bool = False,
) -> str:
    env = os.environ.copy()
    env.setdefault("ASAN_OPTIONS", "detect_leaks=0:halt_on_error=1")
    env.setdefault("UBSAN_OPTIONS", "halt_on_error=1:print_stacktrace=1")
    result = subprocess.run(
        [str(binary), *arguments],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    print(f"RUN {' '.join((binary.name, *arguments))}: rc={result.returncode}")
    print(result.stdout, end="")
    if expect_uaf:
        if result.returncode == 0 or "heap-use-after-free" not in result.stdout:
            raise RuntimeError(f"expected an ASan UAF from {' '.join(arguments)}")
    elif expect_alignment_ubsan:
        if result.returncode == 0 or "misaligned address" not in result.stdout:
            raise RuntimeError(
                f"expected a UBSan alignment failure from {' '.join(arguments)}"
            )
    elif result.returncode or "RESULT PASS" not in result.stdout:
        raise RuntimeError(f"lifetime contract failed in {' '.join(arguments)}")
    return result.stdout


def assertion_count(output: str, label: str) -> str:
    match = re.search(r"RESULT PASS assertions=(\d+)", output)
    if match is None:
        raise RuntimeError(f"{label} output is missing its assertion count")
    return match.group(1)


def run_candidate_only(tree: Path, cc: list[str], manifest_out: Path | None) -> int:
    source_paths = (*DMA_FILES, *CTL_FILES)
    hashes_before = {
        relative: hashlib.sha256((tree / relative).read_bytes()).hexdigest()
        for relative in source_paths
    }
    candidate_dma = load_sources(tree, DMA_FILES, None)
    candidate_ctl = load_sources(tree, CTL_FILES, None)
    dma_blocks = [
        type_block(candidate_dma["sound/virtio/virtio_pcm_msg.c"], "virtio_pcm_msg"),
        type_block(
            candidate_dma["sound/virtio/virtio_pcm_msg.c"], "pcm_msg_sg_index", "enum"
        ),
        type_block(candidate_dma["sound/virtio/virtio_pcm.h"], "virtio_pcm_substream"),
        *functions_in_order(candidate_dma, DMA_FUNCTIONS),
    ]
    control_blocks = [
        type_block(candidate_ctl["sound/virtio/virtio_ctl_msg.c"], "virtio_snd_msg"),
        *functions_in_order(candidate_ctl, CTL_FUNCTIONS),
    ]

    with tempfile.TemporaryDirectory(prefix="virtio-snd-candidate-") as temporary:
        temp = Path(temporary)
        dma_source = compose(
            DMA_HARNESS,
            "/* EXTRACTED_DMA_FUNCTIONS */",
            dma_blocks,
            temp / "candidate-dma.c",
            ("#define TEST_CANDIDATE 1", "#define CONFIG_MMU 1"),
        )
        control_source = compose(
            CTL_HARNESS,
            "/* EXTRACTED_CONTROL_FUNCTIONS */",
            control_blocks,
            temp / "candidate-control.c",
            ("#define TEST_CANDIDATE 1",),
        )
        dma_binary = temp / "candidate-dma"
        control_binary = temp / "candidate-control"
        compile_binary(cc, dma_source, dma_binary)
        compile_binary(cc, control_source, control_binary)
        dma_output = run(dma_binary, "all")
        control_output = run(control_binary, "all")

    hashes_after = {
        relative: hashlib.sha256((tree / relative).read_bytes()).hexdigest()
        for relative in source_paths
    }
    if hashes_after != hashes_before:
        raise RuntimeError(
            "kernel candidate source changed while the test runner was active"
        )
    if manifest_out:
        output = manifest_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "schema": "virtio-snd-dma-control-candidate-tests-v1",
                    "candidate_tree": str(tree),
                    "candidate_only": True,
                    "candidate_dma_assertions": assertion_count(
                        dma_output, "candidate DMA"
                    ),
                    "candidate_control_assertions": assertion_count(
                        control_output, "candidate control"
                    ),
                    "source_sha256": hashes_after,
                    "kernel_built_or_booted": False,
                    "sanitizers": ["address", "undefined"],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        print(f"SOURCE_MANIFEST {output}")
    print("DMA_CONTROL_CANDIDATE_PASS")
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
        "--baseline-ref",
        "--baseline",
        dest="baseline_ref",
        help="Git ref after patches 1-3 and before patches 4-5",
    )
    parser.add_argument(
        "--baseline-tree", type=Path, help="filesystem source tree after patches 1-3"
    )
    parser.add_argument(
        "--v1-control-baseline-ref", help="source ref after the signed v1 patch series"
    )
    parser.add_argument(
        "--v1-control-baseline-tree",
        type=Path,
        help="filesystem source tree for the v1 control layout",
    )
    parser.add_argument(
        "--v2-control-baseline-ref", help="source ref after the committed v2 overlay"
    )
    parser.add_argument(
        "--v2-control-baseline-tree",
        type=Path,
        help="filesystem source tree for the v2 control layout",
    )
    parser.add_argument(
        "--skip-layout-baselines",
        action="store_true",
        help="skip the optional historical v1/v2 layout regressions",
    )
    parser.add_argument(
        "--candidate-only",
        action="store_true",
        help="run candidate DMA and control tests without baselines",
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        help="optional JSON file for extracted-source hashes and results",
    )
    args = parser.parse_args()
    tree = args.kernel_tree.resolve()
    cc = shlex.split(os.environ.get("CC", "cc"))
    if not cc or not shutil.which(cc[0]):
        raise RuntimeError("a C compiler is required (set CC if needed)")
    if not tree.is_dir():
        raise RuntimeError(f"kernel source tree not found: {tree}")
    if args.candidate_only:
        baseline_values = (
            args.baseline_ref,
            args.baseline_tree,
            args.v1_control_baseline_ref,
            args.v1_control_baseline_tree,
            args.v2_control_baseline_ref,
            args.v2_control_baseline_tree,
        )
        if any(value is not None for value in baseline_values):
            raise RuntimeError("omit baseline inputs with --candidate-only")
        if args.skip_layout_baselines:
            raise RuntimeError(
                "--skip-layout-baselines is redundant with --candidate-only"
            )
        return run_candidate_only(tree, cc, args.manifest_out)
    if (args.baseline_ref is None) == (args.baseline_tree is None):
        raise RuntimeError(
            "provide exactly one baseline ref or source tree for DMA/control lifetime"
        )
    layout_inputs = (
        (
            "v1 control layout",
            args.v1_control_baseline_ref,
            args.v1_control_baseline_tree,
        ),
        (
            "v2 control layout",
            args.v2_control_baseline_ref,
            args.v2_control_baseline_tree,
        ),
    )
    if args.skip_layout_baselines:
        if any(
            reference is not None or source_tree is not None
            for _, reference, source_tree in layout_inputs
        ):
            raise RuntimeError(
                "omit v1/v2 layout baseline inputs with --skip-layout-baselines"
            )
    else:
        for label, reference, source_tree in layout_inputs:
            if (reference is None) == (source_tree is None):
                raise RuntimeError(
                    f"provide exactly one baseline ref or source tree for {label}, "
                    "or select --skip-layout-baselines"
                )
    layout_baselines_enabled = not args.skip_layout_baselines
    baseline_trees = tuple(
        source_tree.resolve() if source_tree else None
        for _, _, source_tree in (
            ("DMA/control lifetime", args.baseline_ref, args.baseline_tree),
            *layout_inputs,
        )
    )
    baseline_commit = (
        resolve_commit(tree, args.baseline_ref) if args.baseline_ref else None
    )

    source_paths = (*DMA_FILES, *CTL_FILES)
    hashes_before = {
        relative: hashlib.sha256((tree / relative).read_bytes()).hexdigest()
        for relative in source_paths
    }

    baseline_dma = load_sources(tree, DMA_FILES, args.baseline_ref, baseline_trees[0])
    candidate_dma = load_sources(tree, DMA_FILES, None)
    baseline_ctl = load_sources(tree, CTL_FILES, args.baseline_ref, baseline_trees[0])
    v1_ctl = (
        load_sources(tree, CTL_FILES, args.v1_control_baseline_ref, baseline_trees[1])
        if layout_baselines_enabled
        else None
    )
    v2_ctl = (
        load_sources(tree, CTL_FILES, args.v2_control_baseline_ref, baseline_trees[2])
        if layout_baselines_enabled
        else None
    )
    candidate_ctl = load_sources(tree, CTL_FILES, None)

    dma_structs = ("virtio_pcm_msg", "virtio_pcm_substream")
    dma_enums = ("pcm_msg_sg_index",)
    ctl_structs = ("virtio_snd_msg",)

    with tempfile.TemporaryDirectory(prefix="virtio-snd-buffer-life-") as temporary:
        temp = Path(temporary)
        baseline_dma_blocks = [
            type_block(baseline_dma["sound/virtio/virtio_pcm_msg.c"], dma_structs[0]),
            type_block(
                baseline_dma["sound/virtio/virtio_pcm_msg.c"], dma_enums[0], "enum"
            ),
            type_block(baseline_dma["sound/virtio/virtio_pcm.h"], dma_structs[1]),
            *functions_in_order(
                baseline_dma,
                DMA_FUNCTIONS,
                optional=frozenset(
                    {
                        "virtsnd_pcm_dma_pages_put",
                        "virtsnd_pcm_dma_pages_get",
                        "virtsnd_pcm_msg_cancel",
                        "virtsnd_pcm_pause_restart_enabled",
                    }
                ),
            ),
        ]
        candidate_dma_blocks = [
            type_block(candidate_dma["sound/virtio/virtio_pcm_msg.c"], dma_structs[0]),
            type_block(
                candidate_dma["sound/virtio/virtio_pcm_msg.c"], dma_enums[0], "enum"
            ),
            type_block(candidate_dma["sound/virtio/virtio_pcm.h"], dma_structs[1]),
            *functions_in_order(candidate_dma, DMA_FUNCTIONS),
        ]
        baseline_ctl_blocks = [
            type_block(baseline_ctl["sound/virtio/virtio_ctl_msg.c"], ctl_structs[0]),
            *functions_in_order(
                baseline_ctl,
                CTL_FUNCTIONS,
                optional=frozenset(
                    {
                        "virtsnd_ctl_msg_sg_init",
                        "virtsnd_ctl_msg_request_data",
                        "virtsnd_ctl_msg_response_data",
                        "virtsnd_ctl_msg_cleanup",
                        "virtsnd_ctl_msg_enable",
                    }
                ),
            ),
        ]
        v1_ctl_blocks = None
        v2_ctl_blocks = None
        if layout_baselines_enabled:
            if v1_ctl is None or v2_ctl is None:
                raise RuntimeError("historical layout sources were not loaded")
            v1_ctl_blocks = [
                type_block(v1_ctl["sound/virtio/virtio_ctl_msg.c"], ctl_structs[0]),
                *functions_in_order(
                    v1_ctl,
                    CTL_FUNCTIONS,
                    optional=frozenset(
                        {
                            "virtsnd_ctl_msg_sg_init",
                            "virtsnd_ctl_msg_request_data",
                            "virtsnd_ctl_msg_response_data",
                            "virtsnd_ctl_msg_cleanup",
                            "virtsnd_ctl_msg_enable",
                        }
                    ),
                ),
            ]
            v2_ctl_blocks = [
                type_block(v2_ctl["sound/virtio/virtio_ctl_msg.c"], ctl_structs[0]),
                *functions_in_order(v2_ctl, CTL_FUNCTIONS),
            ]
        candidate_ctl_blocks = [
            type_block(candidate_ctl["sound/virtio/virtio_ctl_msg.c"], ctl_structs[0]),
            *functions_in_order(candidate_ctl, CTL_FUNCTIONS),
        ]

        baseline_dma_c = compose(
            DMA_HARNESS,
            "/* EXTRACTED_DMA_FUNCTIONS */",
            baseline_dma_blocks,
            temp / "baseline-dma.c",
            ("#define TEST_BASELINE 1", "#define CONFIG_MMU 1"),
        )
        candidate_dma_c = compose(
            DMA_HARNESS,
            "/* EXTRACTED_DMA_FUNCTIONS */",
            candidate_dma_blocks,
            temp / "candidate-dma.c",
            ("#define TEST_CANDIDATE 1", "#define CONFIG_MMU 1"),
        )
        baseline_ctl_c = compose(
            CTL_HARNESS,
            "/* EXTRACTED_CONTROL_FUNCTIONS */",
            baseline_ctl_blocks,
            temp / "baseline-control.c",
            ("#define TEST_BASELINE 1",),
        )
        v1_ctl_c = None
        v2_ctl_c = None
        if layout_baselines_enabled:
            if v1_ctl_blocks is None or v2_ctl_blocks is None:
                raise RuntimeError("historical layout blocks were not extracted")
            v1_ctl_c = compose(
                CTL_HARNESS,
                "/* EXTRACTED_CONTROL_FUNCTIONS */",
                v1_ctl_blocks,
                temp / "v1-control-layout.c",
                ("#define TEST_LAYOUT_REGRESSION 1",),
            )
            v2_ctl_c = compose(
                CTL_HARNESS,
                "/* EXTRACTED_CONTROL_FUNCTIONS */",
                v2_ctl_blocks,
                temp / "v2-control-alignment.c",
                ("#define TEST_V2_ALIGNMENT 1",),
            )
        candidate_ctl_c = compose(
            CTL_HARNESS,
            "/* EXTRACTED_CONTROL_FUNCTIONS */",
            candidate_ctl_blocks,
            temp / "candidate-control.c",
            ("#define TEST_CANDIDATE 1",),
        )

        binaries = {
            "baseline_dma": temp / "baseline-dma",
            "candidate_dma": temp / "candidate-dma",
            "baseline_control": temp / "baseline-control",
            "candidate_control": temp / "candidate-control",
        }
        sources = {
            "baseline_dma": baseline_dma_c,
            "candidate_dma": candidate_dma_c,
            "baseline_control": baseline_ctl_c,
            "candidate_control": candidate_ctl_c,
        }
        if layout_baselines_enabled:
            binaries["v1_layout"] = temp / "v1-control-layout"
            binaries["v2_layout"] = temp / "v2-control-alignment"
            sources["v1_layout"] = v1_ctl_c
            sources["v2_layout"] = v2_ctl_c
        for name, binary in binaries.items():
            source = sources[name]
            if source is None:
                raise RuntimeError(f"source for {name} was not generated")
            compile_binary(cc, source, binary)

        run(binaries["baseline_dma"], "late-capture", expect_uaf=True)
        run(binaries["baseline_dma"], "late-playback", expect_uaf=True)
        run(binaries["baseline_control"], "tlv-read", expect_uaf=True)
        run(binaries["baseline_control"], "tlv-write", expect_uaf=True)
        run(binaries["baseline_control"], "query-info", expect_uaf=True)
        dma_output = run(binaries["candidate_dma"], "all")
        ctl_output = run(binaries["candidate_control"], "all")
        v1_layout_output = None
        v2_layout_output = None
        v2_alignment_output = None
        v2_response_alignment_output = None
        if layout_baselines_enabled:
            v1_layout_output = run(binaries["v1_layout"])
            v2_layout_output = run(binaries["v2_layout"])
            v2_alignment_output = run(
                binaries["v2_layout"], "typed-access", expect_alignment_ubsan=True
            )
            v2_response_alignment_output = run(
                binaries["v2_layout"], "typed-response", expect_alignment_ubsan=True
            )

    hashes_after = {
        relative: hashlib.sha256((tree / relative).read_bytes()).hexdigest()
        for relative in source_paths
    }
    if hashes_after != hashes_before:
        raise RuntimeError("kernel source changed while the test runner was active")
    manifest = {
        "schema": "virtio-snd-control-dma-alignment-source-extracted-v2",
        "source_tree": str(tree),
        "baseline_ref": args.baseline_ref,
        "baseline_commit": baseline_commit,
        "baseline_tree": str(baseline_trees[0]) if baseline_trees[0] else None,
        "v1_control_layout_ref": args.v1_control_baseline_ref,
        "v1_control_layout_tree": (
            str(baseline_trees[1]) if baseline_trees[1] else None
        ),
        "v1_control_layout_commit": (
            resolve_commit(tree, args.v1_control_baseline_ref)
            if args.v1_control_baseline_ref
            else None
        ),
        "v2_control_alignment_ref": args.v2_control_baseline_ref,
        "v2_control_alignment_tree": (
            str(baseline_trees[2]) if baseline_trees[2] else None
        ),
        "v2_control_alignment_commit": (
            resolve_commit(tree, args.v2_control_baseline_ref)
            if args.v2_control_baseline_ref
            else None
        ),
        "kernel_built_or_booted": False,
        "host_shims_model_vmalloc_backing_and_virtqueue_dma": True,
        "baseline_expected_uaf_cases": [
            "late-capture",
            "late-playback",
            "tlv-read",
            "tlv-write",
            "query-info",
        ],
        "historical_layout_baselines_run": layout_baselines_enabled,
        "candidate_dma_assertions": assertion_count(dma_output, "candidate DMA"),
        "candidate_control_assertions": assertion_count(
            ctl_output, "candidate control"
        ),
        "v1_layout_regression_assertions": (
            assertion_count(v1_layout_output, "v1 layout") if v1_layout_output else None
        ),
        "v1_layout_regression": (
            "packed layout violates 64-byte and 128-byte cacheline isolation"
            if v1_layout_output
            else None
        ),
        "v2_layout_assertions": (
            assertion_count(v2_layout_output, "v2 layout") if v2_layout_output else None
        ),
        "v2_typed_alignment_ubsan": (
            "misaligned address" in v2_alignment_output if v2_alignment_output else None
        ),
        "v2_response_alignment_ubsan": (
            "misaligned address" in v2_response_alignment_output
            if v2_response_alignment_output
            else None
        ),
        "source_sha256": hashes_after,
        "source_unchanged_during_run": True,
        "sanitizers": ["address", "undefined"],
    }
    if args.manifest_out:
        output = args.manifest_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        print(f"SOURCE_MANIFEST {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
