#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check source extraction equivalence for commit refs and their tree objects."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-tree", type=Path, required=True)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--candidate-ref", required=True)
    args = parser.parse_args()
    commits = []
    trees = []
    for ref in (args.baseline_ref, args.candidate_ref):
        for kind, target in (("commit", commits), ("tree", trees)):
            target.append(
                subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(args.kernel_tree),
                        "rev-parse",
                        "--verify",
                        f"{ref}^{{{kind}}}",
                    ],
                    text=True,
                ).strip()
            )
    with tempfile.TemporaryDirectory(prefix="ack-ref-equivalence-") as temporary:
        outputs = []
        for kind, refs in (("commit", commits), ("tree", trees)):
            output = Path(temporary) / kind / "fixture.c"
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("generate_fixture.py")),
                    "--kernel-tree",
                    str(args.kernel_tree),
                    "--baseline-ref",
                    refs[0],
                    "--candidate-ref",
                    refs[1],
                    "--output",
                    str(output),
                ],
                check=True,
            )
            outputs.append(output.read_bytes())
            record = json.loads(output.with_name("source-provenance.json").read_text())
            for index, label in enumerate(("baseline", "candidate")):
                revision = record["revisions"][label]
                assert revision["tree"] == trees[index]
                assert revision["commit"] == (
                    commits[index] if kind == "commit" else None
                )
        assert outputs[0] == outputs[1], "commit and tree extraction differ"
    print("ACK_REF_EQUIVALENCE_PASS source=identical provenance=accurate")


if __name__ == "__main__":
    main()
