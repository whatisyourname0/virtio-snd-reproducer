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
BASELINE_DEFAULT = "HEAD~2"
V1_CONTROL_REF = "HEAD"

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
                return source[match.start():index + 1]
        index += 1
    raise RuntimeError(f"unterminated source block: {label}")


def extract_function(source: str, name: str) -> str:
    signature = re.compile(
        r"(?m)^(?:static\s+)?(?:[\w]+\s+|struct\s+[\w]+\s+|"
        r"[\w]+\s*\*\s*)+" + re.escape(name) + r"\s*\([^;{}]*?\)\s*\{"
    )
    return extract_braced(source, signature.pattern, name)


def load_sources(tree: Path, files: tuple[str, ...], baseline: str | None) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for relative in files:
        if baseline:
            result = subprocess.run(
                ["git", "-C", str(tree), "show", f"{baseline}:{relative}"],
                check=True, text=True, stdout=subprocess.PIPE,
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError(f"baseline ref does not resolve to a commit: {reference}")
    return result.stdout.strip()


def functions_in_order(sources: dict[str, str], specifications: tuple[tuple[str, str], ...],
                       *, optional: frozenset[str] = frozenset()) -> list[str]:
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


def compose(harness_path: Path, marker: str, blocks: list[str], output: Path,
            defines: tuple[str, ...]) -> Path:
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
        *cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-function", "-O1", "-g", "-fno-omit-frame-pointer",
        "-fsanitize=address,undefined", str(source), "-o", str(binary),
    ]
    print(f"COMPILE {binary.name}: {' '.join(command)}")
    subprocess.run(command, check=True)


def run(binary: Path, *arguments: str, expect_uaf: bool = False) -> str:
    env = os.environ.copy()
    env.setdefault("ASAN_OPTIONS", "detect_leaks=0:halt_on_error=1")
    env.setdefault("UBSAN_OPTIONS", "halt_on_error=1:print_stacktrace=1")
    result = subprocess.run(
        [str(binary), *arguments], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, env=env,
    )
    print(f"RUN {' '.join((binary.name, *arguments))}: rc={result.returncode}")
    print(result.stdout, end="")
    if expect_uaf:
        if result.returncode == 0 or "heap-use-after-free" not in result.stdout:
            raise RuntimeError(f"expected an ASan UAF from {' '.join(arguments)}")
    elif result.returncode or "RESULT PASS" not in result.stdout:
        raise RuntimeError(f"lifetime contract failed in {' '.join(arguments)}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel-tree", "--source-tree", dest="kernel_tree", type=Path,
                        required=True, help="patched Linux source Git worktree")
    parser.add_argument("--baseline-ref", "--baseline", dest="baseline_ref",
                        default=BASELINE_DEFAULT,
                        help="Git commit/ref before the PCM/control lifetime patches")
    parser.add_argument("--manifest-out", type=Path,
                        help="optional JSON file for extracted-source hashes and results")
    args = parser.parse_args()
    tree = args.kernel_tree.resolve()
    cc = shlex.split(os.environ.get("CC", "cc"))
    if not cc or not shutil.which(cc[0]):
        raise RuntimeError("a C compiler is required (set CC if needed)")
    if not tree.is_dir():
        raise RuntimeError(f"kernel source tree not found: {tree}")
    baseline_commit = resolve_commit(tree, args.baseline_ref)

    source_paths = (*DMA_FILES, *CTL_FILES)
    hashes_before = {
        relative: hashlib.sha256((tree / relative).read_bytes()).hexdigest()
        for relative in source_paths
    }

    baseline_dma = load_sources(tree, DMA_FILES, args.baseline_ref)
    candidate_dma = load_sources(tree, DMA_FILES, None)
    baseline_ctl = load_sources(tree, CTL_FILES, args.baseline_ref)
    v1_ctl = load_sources(tree, CTL_FILES, V1_CONTROL_REF)
    candidate_ctl = load_sources(tree, CTL_FILES, None)

    dma_structs = ("virtio_pcm_msg", "virtio_pcm_substream")
    dma_enums = ("pcm_msg_sg_index",)
    ctl_structs = ("virtio_snd_msg",)

    with tempfile.TemporaryDirectory(prefix="virtio-snd-buffer-life-") as temporary:
        temp = Path(temporary)
        baseline_dma_blocks = [
            type_block(baseline_dma["sound/virtio/virtio_pcm_msg.c"], dma_structs[0]),
            type_block(baseline_dma["sound/virtio/virtio_pcm_msg.c"], dma_enums[0], "enum"),
            type_block(baseline_dma["sound/virtio/virtio_pcm.h"], dma_structs[1]),
            *functions_in_order(baseline_dma, DMA_FUNCTIONS,
                                optional=frozenset({"virtsnd_pcm_dma_pages_put",
                                                    "virtsnd_pcm_dma_pages_get",
                                                    "virtsnd_pcm_msg_cancel"})),
        ]
        candidate_dma_blocks = [
            type_block(candidate_dma["sound/virtio/virtio_pcm_msg.c"], dma_structs[0]),
            type_block(candidate_dma["sound/virtio/virtio_pcm_msg.c"], dma_enums[0], "enum"),
            type_block(candidate_dma["sound/virtio/virtio_pcm.h"], dma_structs[1]),
            *functions_in_order(candidate_dma, DMA_FUNCTIONS),
        ]
        baseline_ctl_blocks = [
            type_block(baseline_ctl["sound/virtio/virtio_ctl_msg.c"], ctl_structs[0]),
            *functions_in_order(baseline_ctl, CTL_FUNCTIONS,
                                optional=frozenset({"virtsnd_ctl_msg_sg_init",
                                                    "virtsnd_ctl_msg_request_data",
                                                    "virtsnd_ctl_msg_response_data",
                                                    "virtsnd_ctl_msg_cleanup",
                                                    "virtsnd_ctl_msg_enable"})),
        ]
        v1_ctl_blocks = [
            type_block(v1_ctl["sound/virtio/virtio_ctl_msg.c"], ctl_structs[0]),
            *functions_in_order(v1_ctl, CTL_FUNCTIONS,
                                optional=frozenset({"virtsnd_ctl_msg_sg_init",
                                                    "virtsnd_ctl_msg_request_data",
                                                    "virtsnd_ctl_msg_response_data",
                                                    "virtsnd_ctl_msg_cleanup",
                                                    "virtsnd_ctl_msg_enable"})),
        ]
        candidate_ctl_blocks = [
            type_block(candidate_ctl["sound/virtio/virtio_ctl_msg.c"], ctl_structs[0]),
            *functions_in_order(candidate_ctl, CTL_FUNCTIONS),
        ]

        baseline_dma_c = compose(
            DMA_HARNESS, "/* EXTRACTED_DMA_FUNCTIONS */", baseline_dma_blocks,
            temp / "baseline-dma.c",
            ("#define TEST_BASELINE 1", "#define CONFIG_MMU 1"),
        )
        candidate_dma_c = compose(
            DMA_HARNESS, "/* EXTRACTED_DMA_FUNCTIONS */", candidate_dma_blocks,
            temp / "candidate-dma.c",
            ("#define TEST_CANDIDATE 1", "#define CONFIG_MMU 1"),
        )
        baseline_ctl_c = compose(
            CTL_HARNESS, "/* EXTRACTED_CONTROL_FUNCTIONS */", baseline_ctl_blocks,
            temp / "baseline-control.c", ("#define TEST_BASELINE 1",),
        )
        v1_ctl_c = compose(
            CTL_HARNESS, "/* EXTRACTED_CONTROL_FUNCTIONS */", v1_ctl_blocks,
            temp / "v1-control-layout.c", ("#define TEST_LAYOUT_REGRESSION 1",),
        )
        candidate_ctl_c = compose(
            CTL_HARNESS, "/* EXTRACTED_CONTROL_FUNCTIONS */", candidate_ctl_blocks,
            temp / "candidate-control.c", ("#define TEST_CANDIDATE 1",),
        )

        binaries = [temp / "baseline-dma", temp / "candidate-dma",
                    temp / "baseline-control", temp / "candidate-control",
                    temp / "v1-control-layout"]
        for source, binary in zip((baseline_dma_c, candidate_dma_c,
                                   baseline_ctl_c, candidate_ctl_c, v1_ctl_c), binaries):
            compile_binary(cc, source, binary)

        run(binaries[0], "late-capture", expect_uaf=True)
        run(binaries[0], "late-playback", expect_uaf=True)
        run(binaries[2], "tlv-read", expect_uaf=True)
        run(binaries[2], "tlv-write", expect_uaf=True)
        run(binaries[2], "query-info", expect_uaf=True)
        dma_output = run(binaries[1], "all")
        ctl_output = run(binaries[3], "all")
        v1_layout_output = run(binaries[4])

    hashes_after = {
        relative: hashlib.sha256((tree / relative).read_bytes()).hexdigest()
        for relative in source_paths
    }
    if hashes_after != hashes_before:
        raise RuntimeError("kernel source changed while the test runner was active")
    manifest = {
        "schema": "virtio-snd-buffer-lifetime-source-extracted-v1",
        "source_tree": str(tree),
        "baseline_ref": args.baseline_ref,
        "baseline_commit": baseline_commit,
        "v1_control_layout_ref": V1_CONTROL_REF,
        "v1_control_layout_commit": resolve_commit(tree, V1_CONTROL_REF),
        "kernel_built_or_booted": False,
        "host_shims_model_vmalloc_backing_and_virtqueue_dma": True,
        "baseline_expected_uaf_cases": [
            "late-capture", "late-playback", "tlv-read", "tlv-write", "query-info",
        ],
        "candidate_dma_assertions": re.search(r"RESULT PASS assertions=(\d+)", dma_output).group(1),
        "candidate_control_assertions": re.search(r"RESULT PASS assertions=(\d+)", ctl_output).group(1),
        "v1_layout_regression_assertions": re.search(
            r"RESULT PASS assertions=(\d+)", v1_layout_output).group(1),
        "v1_layout_regression": "packed layout violates 64-byte and 128-byte cacheline isolation",
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
