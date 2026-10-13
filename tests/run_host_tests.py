#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run source-extracted host regressions for the v3 review overlay."""

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
GENERIC_BASELINE_DEFAULT = "HEAD~6"
LIFETIME_BASELINE_DEFAULT = "HEAD~3"
ACK_BASELINE_DEFAULT = "HEAD~1"
NOMMU_BASELINE_DEFAULT = "HEAD~1"
STOPPED_BASELINE_DEFAULT = "HEAD"
V1_CONTROL_BASELINE_DEFAULT = "HEAD~1"
V2_CONTROL_BASELINE_DEFAULT = "HEAD"


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
        help=(
            "Linux Git worktree with v1 patches and committed v2 overlay, "
            "plus v3 overlay"
        ),
    )
    parser.add_argument(
        "--generic-baseline-ref",
        default=GENERIC_BASELINE_DEFAULT,
        help="source ref before all five patches (default: HEAD~6)",
    )
    parser.add_argument(
        "--lifetime-baseline-ref",
        default=LIFETIME_BASELINE_DEFAULT,
        help="source ref after patches 1-3 and before patches 4-5 (default: HEAD~3)",
    )
    parser.add_argument(
        "--ack-baseline-ref",
        default=ACK_BASELINE_DEFAULT,
        help="source ref after the signed v1 patch series (default: HEAD~1)",
    )
    parser.add_argument(
        "--nommu-baseline-ref",
        default=NOMMU_BASELINE_DEFAULT,
        help="source ref after the signed v1 patch series (default: HEAD~1)",
    )
    parser.add_argument(
        "--stopped-baseline-ref",
        default=STOPPED_BASELINE_DEFAULT,
        help="source ref before the stopped-lock fix (default: committed v2 overlay)",
    )
    parser.add_argument(
        "--v1-control-baseline-ref",
        default=V1_CONTROL_BASELINE_DEFAULT,
        help="source ref after the signed v1 patch series (default: HEAD~1)",
    )
    parser.add_argument(
        "--v2-control-baseline-ref",
        default=V2_CONTROL_BASELINE_DEFAULT,
        help="source ref after the committed v2 overlay (default: HEAD)",
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
    print(f"STOPPED_BASELINE_REF {args.stopped_baseline_ref}")
    print(f"V1_CONTROL_BASELINE_REF {args.v1_control_baseline_ref}")
    print(f"V2_CONTROL_BASELINE_REF {args.v2_control_baseline_ref}")

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
        if name == "generic":
            command.extend(
                ["--stopped-baseline-ref", args.stopped_baseline_ref]
            )
            if output_dir:
                command.extend(
                    [
                        "--stopped-manifest-out",
                        str(output_dir / "generic-stopped-manifest.json"),
                    ]
                )
        elif name == "dma-control-lifetime":
            command.extend(
                [
                    "--v1-control-baseline-ref",
                    args.v1_control_baseline_ref,
                    "--v2-control-baseline-ref",
                    args.v2_control_baseline_ref,
                ]
            )
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
