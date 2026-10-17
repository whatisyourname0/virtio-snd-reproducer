#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate an external kernel module from the baseline and ACK-only sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPS = "sound/virtio/virtio_pcm_ops.c"
MSG = "sound/virtio/virtio_pcm_msg.c"
PCM_HEADER = "sound/virtio/virtio_pcm.h"
PCM_LIB = "sound/core/pcm_lib.c"
TEMPLATE = HERE / "ack_fixture_module.c.in"
OUTPUT = HERE / "ack_fixture_module.c"
PROVENANCE = HERE / "source-provenance.json"


def extract_function(source: str, name: str) -> str:
    """Return one complete C function while ignoring braces in comments/strings."""
    pattern = re.compile(
        r"(?m)^(?:static\s+)?(?:[\w]+\s+|struct\s+[\w]+\s+|"
        r"[\w]+\s*\*\s*)+" + re.escape(name) + r"\s*\([^;{}]*?\)\s*\{"
    )
    match = pattern.search(source)
    if not match:
        raise ValueError(f"source function not found: {name}")

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
    raise ValueError(f"unterminated function body: {name}")


def extract_struct(source: str, name: str) -> str:
    """Return a complete source struct declaration."""
    marker = f"struct {name} {{"
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                end = source.index(";", index) + 1
                return source[start:end]
    raise ValueError(f"unterminated source struct: {name}")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def rename_identifiers(source: str, replacements: dict[str, str]) -> str:
    """Rename extracted function references without changing function bodies."""
    for old, new in replacements.items():
        source = re.sub(rf"\b{re.escape(old)}\b", new, source)
    return source


def instrument_queue_wait(source: str) -> str:
    """Signal immediately before the real queue spinlock acquisition."""
    needle = "scoped_guard(spinlock_irqsave, &queue->lock) {"
    if needle not in source:
        raise ValueError("ACK queue lock site changed; refusing to lose race hook")
    return source.replace(
        needle,
        "fixture_note_queue_lock_attempt();\n\t" + needle,
    )


def replace_xrun_work(source: str) -> str:
    needle = "schedule_work(&vss->elapsed_period);"
    return source.replace(needle, "fixture_schedule_work(vss);")


def generate(baseline: Path, candidate: Path, output: Path) -> None:
    base_ops = (baseline / OPS).read_text()
    base_msg = (baseline / MSG).read_text()
    base_header = (baseline / PCM_HEADER).read_text()
    cand_ops = (candidate / OPS).read_text()
    cand_msg = (candidate / MSG).read_text()
    cand_header = (candidate / PCM_HEADER).read_text()
    pcm_lib = (candidate / PCM_LIB).read_text()

    shared_names = (
        "virtsnd_pcm_capture_status",
        "virtsnd_pcm_capture_xrun_locked",
        "virtsnd_pcm_capture_complete",
    )
    shared = {name: extract_function(base_msg, name) for name in shared_names}
    for name, function in shared.items():
        if extract_function(cand_msg, name) != function:
            raise ValueError(f"baseline/candidate shared function differs: {name}")

    counter_helper = extract_function(base_header, "virtsnd_pcm_capture_counters_valid")
    if (
        extract_function(cand_header, "virtsnd_pcm_capture_counters_valid")
        != counter_helper
    ):
        raise ValueError("baseline/candidate capture counters helper differs")

    transfer = extract_struct(base_ops, "virtsnd_pcm_transfer")
    if extract_struct(cand_ops, "virtsnd_pcm_transfer") != transfer:
        raise ValueError("baseline/candidate PCM transfer struct differs")

    baseline_ack = extract_function(base_ops, "virtsnd_pcm_cp_ack")
    candidate_ack = extract_function(cand_ops, "virtsnd_pcm_cp_ack")
    candidate_staged_ack = extract_function(cand_ops, "virtsnd_pcm_capture_staged_ack")
    alsa_helper = extract_function(pcm_lib, "pcm_lib_apply_appl_ptr")

    shared_replacements = {
        "virtsnd_pcm_capture_status": "fixture_capture_status",
        "virtsnd_pcm_capture_xrun_locked": "fixture_capture_xrun_locked",
        "virtsnd_pcm_capture_complete": "fixture_capture_complete",
        "virtsnd_pcm_capture_counters_valid": "fixture_capture_counters_valid",
        "virtsnd_pcm_capture_send": "fixture_capture_send",
    }
    shared_code = "\n\n".join(
        rename_identifiers(function, shared_replacements)
        for function in shared.values()
    )
    shared_code = replace_xrun_work(shared_code)
    shared_code = rename_identifiers(
        shared_code,
        {
            "virtsnd_capture_packed_status": "fixture_packed_status",
            "virtsnd_capture_allow_empty": "fixture_allow_empty",
            "virtsnd_capture_allow_short": "fixture_allow_short",
        },
    )
    counter_code = rename_identifiers(
        counter_helper,
        {
            "virtsnd_pcm_capture_counters_valid": "fixture_capture_counters_valid",
        },
    )

    baseline_replacements = {
        "virtsnd_pcm_cp_ack": "fixture_baseline_cp_ack",
        "virtsnd_pcm_capture_counters_valid": "fixture_capture_counters_valid",
        "virtsnd_pcm_capture_send": "fixture_capture_send",
        "virtsnd_pcm_queue": "fixture_pcm_queue",
        "virtsnd_pcm_trans_copy": "fixture_pcm_trans_copy",
        "virtsnd_pcm_transfer_failed": "fixture_pcm_transfer_failed",
    }
    candidate_replacements = {
        "virtsnd_pcm_cp_ack": "fixture_candidate_cp_ack",
        "virtsnd_pcm_capture_staged_ack": "fixture_candidate_staged_ack",
        "virtsnd_pcm_capture_counters_valid": "fixture_capture_counters_valid",
        "virtsnd_pcm_capture_send": "fixture_capture_send",
        "virtsnd_pcm_queue": "fixture_pcm_queue",
        "virtsnd_pcm_trans_copy": "fixture_pcm_trans_copy",
        "virtsnd_pcm_transfer_failed": "fixture_pcm_transfer_failed",
    }
    baseline_code = instrument_queue_wait(
        replace_xrun_work(rename_identifiers(baseline_ack, baseline_replacements))
    )
    candidate_staged_code = instrument_queue_wait(
        replace_xrun_work(
            rename_identifiers(candidate_staged_ack, candidate_replacements)
        )
    )
    candidate_ack_code = replace_xrun_work(
        rename_identifiers(candidate_ack, candidate_replacements)
    )

    alsa_code = rename_identifiers(
        alsa_helper,
        {
            "pcm_lib_apply_appl_ptr": "fixture_pcm_lib_apply_appl_ptr",
            "__snd_pcm_xrun": "fixture_pcm_xrun",
        },
    )
    generated_functions = f"{transfer}\n\n{counter_code}\n\n{shared_code}\n\n{baseline_code}\n\n{candidate_staged_code}\n\n{candidate_ack_code}\n\n{alsa_code}"

    template = TEMPLATE.read_text()
    marker = "/* EXTRACTED_KERNEL_FUNCTIONS */"
    if template.count(marker) != 1:
        raise ValueError("kernel module source extraction marker must occur once")
    output.write_text(template.replace(marker, generated_functions))

    sources = {
        "baseline:" + OPS: digest((baseline / OPS).read_text()),
        "baseline:" + MSG: digest(base_msg),
        "baseline:" + PCM_HEADER: digest(base_header),
        "candidate:" + OPS: digest((candidate / OPS).read_text()),
        "candidate:" + MSG: digest(cand_msg),
        "candidate:" + PCM_HEADER: digest(cand_header),
        "candidate:" + PCM_LIB: digest(pcm_lib),
        "extracted:baseline_ack": digest(baseline_ack),
        "extracted:candidate_ack": digest(candidate_ack),
        "extracted:candidate_staged_ack": digest(candidate_staged_ack),
        "extracted:alsa_appl_ptr": digest(alsa_helper),
        "extracted:capture_complete": digest(shared["virtsnd_pcm_capture_complete"]),
    }
    provenance = output.with_name("source-provenance.json")
    provenance.write_text(json.dumps({"sha256": sources}, indent=2) + "\n")
    print(f"generated={output}")
    print(
        "source_functions=baseline_ack,candidate_ack,candidate_staged_ack,alsa_appl_ptr,capture_complete"
    )
    print(f"provenance={provenance}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-tree", type=Path, required=True)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--candidate-ref", default="HEAD")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ack-source-") as temporary:
        roots = []
        revisions = {}
        for label, ref in (
            ("baseline", args.baseline_ref),
            ("candidate", args.candidate_ref),
        ):
            tree = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(args.kernel_tree),
                    "rev-parse",
                    "--verify",
                    ref + "^{tree}",
                ],
                text=True,
            ).strip()
            commit_result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(args.kernel_tree),
                    "rev-parse",
                    "--verify",
                    ref + "^{commit}",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            revisions[label] = {
                "ref": ref,
                "tree": tree,
                "commit": commit_result.stdout.strip()
                if commit_result.returncode == 0
                else None,
            }
            root = Path(temporary) / label
            roots.append(root)
            for relative in (OPS, MSG, PCM_HEADER, PCM_LIB):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(
                    subprocess.check_output(
                        [
                            "git",
                            "-C",
                            str(args.kernel_tree),
                            "show",
                            tree + ":" + relative,
                        ]
                    )
                )
        generate(roots[0], roots[1], args.output)
        provenance = args.output.with_name("source-provenance.json")
        record = json.loads(provenance.read_text())
        record["revisions"] = revisions
        provenance.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
