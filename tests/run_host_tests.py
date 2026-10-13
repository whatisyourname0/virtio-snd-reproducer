#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run source-extracted host regressions for the v2 review overlay."""

from __future__ import annotations

import argparse
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ACK_BASELINE_DEFAULT = "9aac47803de024cd66c56cc594560c69b5a8a526"


def compiler_description() -> str:
    compiler = shlex.split(os.environ.get("CC", "cc"))
    if not compiler or not shutil.which(compiler[0]):
        raise RuntimeError("a C compiler is required; set CC to GCC or Clang")
    result = subprocess.run(
        [*compiler, "--version"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    version = result.stdout.splitlines()[0] if result.stdout else "version unavailable"
    return f"{' '.join(compiler)}: {version}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the source-extracted virtio-snd host regression suites."
    )
    parser.add_argument(
        "--kernel-tree",
        type=Path,
        required=True,
        help="Linux Git worktree with the five patches and v2 overlay applied",
    )
    parser.add_argument(
        "--generic-baseline-ref",
        default="HEAD~5",
        help="source ref before all five patches (default: HEAD~5)",
    )
    parser.add_argument(
        "--lifetime-baseline-ref",
        default="HEAD~2",
        help="source ref after patches 1-3 and before patches 4-5 (default: HEAD~2)",
    )
    parser.add_argument(
        "--ack-baseline-ref",
        default=ACK_BASELINE_DEFAULT,
        help="source ref before the PCM ACK fix (default: pinned v1 source)",
    )
    parser.add_argument(
        "--nommu-baseline-ref",
        default=ACK_BASELINE_DEFAULT,
        help="source ref before NOMMU PCM bounce pages (default: pinned v1 source)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="optional directory for run logs and source-hash manifests",
    )
    args = parser.parse_args()

    kernel_tree = args.kernel_tree.resolve()
    if not kernel_tree.is_dir():
        raise RuntimeError(f"kernel source tree not found: {kernel_tree}")

    output_dir = args.output_dir.resolve() if args.output_dir else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    print(f"HOST {platform.system()} {platform.machine()}")
    print(f"COMPILER {compiler_description()}")
    print(f"KERNEL_TREE {kernel_tree}")
    print(f"GENERIC_BASELINE_REF {args.generic_baseline_ref}")
    print(f"LIFETIME_BASELINE_REF {args.lifetime_baseline_ref}")
    print(f"ACK_BASELINE_REF {args.ack_baseline_ref}")
    print(f"NOMMU_BASELINE_REF {args.nommu_baseline_ref}")

    suites = (
        (
            "generic",
            TESTS / "run_generic_tests.py",
            args.generic_baseline_ref,
        ),
        (
            "dma-control-lifetime",
            TESTS / "run_dma_control_lifetime_tests.py",
            args.lifetime_baseline_ref,
        ),
        (
            "nommu-lifetime",
            TESTS / "run_nommu_lifetime_tests.py",
            args.nommu_baseline_ref,
        ),
    )
    for name, runner, baseline_ref in suites:
        command = [
            sys.executable,
            str(runner),
            "--kernel-tree",
            str(kernel_tree),
            "--baseline-ref",
            baseline_ref,
        ]
        if output_dir:
            command.extend(
                ["--manifest-out", str(output_dir / f"{name}-manifest.json")]
            )

        print(f"RUN {' '.join(command)}", flush=True)
        if output_dir:
            result = subprocess.run(
                command,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            (output_dir / f"{name}.log").write_text(result.stdout)
            print(result.stdout, end="")
        else:
            result = subprocess.run(command, check=False)

        if result.returncode:
            print(f"SUITE_FAILED {name} rc={result.returncode}")
            return result.returncode

    ack_command = [
        sys.executable,
        str(TESTS / "run_ack_tests.py"),
        "--kernel-source",
        str(kernel_tree),
        "--baseline-ref",
        args.ack_baseline_ref,
    ]
    print(f"RUN {' '.join(ack_command)}", flush=True)
    if output_dir:
        ack_result = subprocess.run(
            ack_command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        (output_dir / "ack.log").write_text(ack_result.stdout)
        print(ack_result.stdout, end="")
    else:
        ack_result = subprocess.run(ack_command, check=False)

    if ack_result.returncode:
        print(f"SUITE_FAILED ack rc={ack_result.returncode}")
        return ack_result.returncode

    print("HOST_REPRODUCER_PASS")
    if output_dir:
        print(f"OUTPUT_DIR {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
