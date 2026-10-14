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


def baseline_argument(
    ref_flag: str,
    ref: str | None,
    tree_flag: str,
    tree: Path | None,
) -> list[str]:
    if tree is not None:
        return [tree_flag, str(tree.resolve())]
    if ref is not None:
        return [ref_flag, ref]
    raise RuntimeError(f"provide either {ref_flag} or {tree_flag}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the source-extracted virtio-snd host regression suites."
    )
    parser.add_argument(
        "--kernel-tree",
        type=Path,
        required=True,
        help="Linux kernel source tree for candidate extraction",
    )
    for name, description in (
        ("generic", "before the candidate PCM and control lifetime fixes"),
        ("lifetime", "before the DMA and control payload lifetime fixes"),
        ("ack", "before the ACK fix"),
        ("nommu", "before the NOMMU fix"),
        ("stopped", "before the stopped-lock fix"),
        ("v1-control", "with the v1 control message layout"),
        ("v2-control", "with the v2 control message layout"),
    ):
        parser.add_argument(
            f"--{name}-baseline-ref",
            help=f"Git ref {description}",
        )
        parser.add_argument(
            f"--{name}-baseline-tree",
            type=Path,
            help=f"filesystem source tree {description}",
        )
    parser.add_argument(
        "--candidate-only",
        action="store_true",
        help="run all candidate suites without historical baseline inputs",
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
    baseline_values = {
        name: (
            getattr(args, f"{name.replace('-', '_')}_baseline_ref"),
            getattr(args, f"{name.replace('-', '_')}_baseline_tree"),
        )
        for name in ("generic", "lifetime", "ack", "nommu")
    }
    stopped_baseline_values = {
        "stopped": (args.stopped_baseline_ref, args.stopped_baseline_tree)
    }
    layout_baseline_values = {
        name: (
            getattr(args, f"{name.replace('-', '_')}_baseline_ref"),
            getattr(args, f"{name.replace('-', '_')}_baseline_tree"),
        )
        for name in ("v1-control", "v2-control")
    }
    all_baseline_values = {
        **baseline_values,
        **stopped_baseline_values,
        **layout_baseline_values,
    }
    if args.candidate_only:
        if any(
            ref is not None or tree is not None
            for ref, tree in all_baseline_values.values()
        ):
            raise RuntimeError("omit baseline inputs with --candidate-only")
        print("BASELINE checks skipped (candidate-only mode)")
    else:
        for name, (ref, tree) in baseline_values.items():
            if (ref is None) == (tree is None):
                raise RuntimeError(
                    f"provide exactly one --{name}-baseline-ref or "
                    f"--{name}-baseline-tree"
                )
            print(
                f"{name.upper().replace('-', '_')}_BASELINE "
                f"{tree.resolve() if tree else ref}"
            )
        stopped_ref, stopped_tree = stopped_baseline_values["stopped"]
        if stopped_ref is not None or stopped_tree is not None:
            if (stopped_ref is None) == (stopped_tree is None):
                raise RuntimeError(
                    "provide exactly one --stopped-baseline-ref or "
                    "--stopped-baseline-tree"
                )
            print(
                "STOPPED_BASELINE "
                f"{stopped_tree.resolve() if stopped_tree else stopped_ref}"
            )
        else:
            print("STOPPED_BASELINE skipped (optional historical negative control)")
        layout_configured = {
            name
            for name, (ref, tree) in layout_baseline_values.items()
            if ref is not None or tree is not None
        }
        if layout_configured and len(layout_configured) != 2:
            raise RuntimeError(
                "v1/v2 layout baselines are an optional pair; provide both or neither"
            )
        if layout_configured:
            for name, (ref, tree) in layout_baseline_values.items():
                if (ref is None) == (tree is None):
                    raise RuntimeError(
                        f"provide exactly one --{name}-baseline-ref or "
                        f"--{name}-baseline-tree"
                    )
                print(
                    f"{name.upper().replace('-', '_')}_BASELINE "
                    f"{tree.resolve() if tree else ref}"
                )
        else:
            print("HISTORICAL_LAYOUT_BASELINES skipped (optional)")

    suites = (
        (
            "generic",
            TESTS / "run_generic_tests.py",
        ),
        (
            "dma-control-lifetime",
            TESTS / "run_dma_control_lifetime_tests.py",
        ),
        (
            "nommu-lifetime",
            TESTS / "run_nommu_lifetime_tests.py",
        ),
    )
    for name, runner in suites:
        command = [
            sys.executable,
            str(runner),
            "--kernel-tree",
            str(kernel_tree),
        ]
        if args.candidate_only:
            command.append("--candidate-only")
        elif name == "generic":
            command.extend(
                baseline_argument(
                    "--baseline-ref",
                    args.generic_baseline_ref,
                    "--baseline-tree",
                    args.generic_baseline_tree,
                )
            )
            if args.stopped_baseline_ref or args.stopped_baseline_tree:
                command.extend(
                    baseline_argument(
                        "--stopped-baseline-ref",
                        args.stopped_baseline_ref,
                        "--stopped-baseline-tree",
                        args.stopped_baseline_tree,
                    )
                )
            else:
                command.append("--skip-stopped-baseline")
        elif name == "dma-control-lifetime":
            command.extend(
                baseline_argument(
                    "--baseline-ref",
                    args.lifetime_baseline_ref,
                    "--baseline-tree",
                    args.lifetime_baseline_tree,
                )
            )
            if args.v1_control_baseline_ref or args.v1_control_baseline_tree:
                command.extend(
                    baseline_argument(
                        "--v1-control-baseline-ref",
                        args.v1_control_baseline_ref,
                        "--v1-control-baseline-tree",
                        args.v1_control_baseline_tree,
                    )
                )
                command.extend(
                    baseline_argument(
                        "--v2-control-baseline-ref",
                        args.v2_control_baseline_ref,
                        "--v2-control-baseline-tree",
                        args.v2_control_baseline_tree,
                    )
                )
            else:
                command.append("--skip-layout-baselines")
        elif name == "nommu-lifetime":
            command.extend(
                baseline_argument(
                    "--baseline-ref",
                    args.nommu_baseline_ref,
                    "--baseline-tree",
                    args.nommu_baseline_tree,
                )
            )
        if name == "generic" and output_dir:
            command.extend(
                [
                    "--stopped-manifest-out",
                    str(output_dir / "generic-stopped-manifest.json"),
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
    ]
    if args.candidate_only:
        ack_command.append("--candidate-only")
    else:
        ack_command.extend(
            baseline_argument(
                "--baseline-ref",
                args.ack_baseline_ref,
                "--baseline-tree",
                args.ack_baseline_tree,
            )
        )
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

    print("HOST_CANDIDATE_PASS" if args.candidate_only else "HOST_REPRODUCER_PASS")
    if output_dir:
        print(f"OUTPUT_DIR {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
