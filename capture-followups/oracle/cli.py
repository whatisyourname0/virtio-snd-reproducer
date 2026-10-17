# SPDX-License-Identifier: GPL-2.0-only
"""Command-line entrypoint for the deterministic S16_LE oracle."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .SequenceOracle import SequenceOracle


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    for name in ("generate", "check"):
        command = commands.add_parser(name)
        command.add_argument("raw_file", type=Path)
        command.add_argument("--channels", type=int, required=True)
        command.add_argument("--epoch", type=int, required=True)
        command.add_argument("--start-frame", type=int, required=True)
        command.add_argument("--count", type=int, required=True)
        command.add_argument(
            "--seed",
            type=lambda value: int(value, 0),
            default=SequenceOracle.DEFAULT_SEED,
        )

    self_test = commands.add_parser("self-test")
    self_test.add_argument("--channels", type=int, default=2)
    self_test.add_argument("--epoch", type=int, default=7)
    self_test.add_argument("--start-frame", type=int, default=1000)
    self_test.add_argument("--count", type=int, default=257)
    self_test.add_argument(
        "--seed", type=lambda value: int(value, 0), default=SequenceOracle.DEFAULT_SEED
    )

    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "generate":
            size = SequenceOracle.write_raw(
                arguments.raw_file,
                channels=arguments.channels,
                epoch=arguments.epoch,
                start_frame=arguments.start_frame,
                count=arguments.count,
                seed=arguments.seed,
            )
            result = {
                "status": "GENERATED",
                "formula": SequenceOracle.FORMULA,
                "raw_file": str(arguments.raw_file),
                "channels": arguments.channels,
                "epoch": arguments.epoch,
                "start_frame": arguments.start_frame,
                "count": arguments.count,
                "bytes": size,
                "seed": f"0x{arguments.seed:016x}",
            }
        elif arguments.command == "check":
            result = SequenceOracle.check_raw(
                arguments.raw_file,
                channels=arguments.channels,
                epoch=arguments.epoch,
                start_frame=arguments.start_frame,
                count=arguments.count,
                seed=arguments.seed,
            )
            result["raw_file"] = str(arguments.raw_file)
        else:
            result = SequenceOracle.self_test(
                channels=arguments.channels,
                epoch=arguments.epoch,
                start_frame=arguments.start_frame,
                count=arguments.count,
                seed=arguments.seed,
            )
    except (AssertionError, OSError, ValueError) as error:
        print(
            json.dumps({"status": "FAIL", "error": str(error)}, sort_keys=True),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
