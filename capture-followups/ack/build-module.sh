#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
usage() {
    echo 'Usage: build-module.sh --kernel-tree PATH --build-dir PATH --baseline-ref REF --candidate-ref REF --output-dir PATH [--source-git PATH] [--arch arm64] [--cross-compile PREFIX] [--jobs N]' >&2
    exit 2
}
KERNEL_TREE= BUILD_DIR= BASELINE_REF= CANDIDATE_REF= OUTPUT_DIR=
ARCH=arm64 CROSS_COMPILE= JOBS=2 SOURCE_GIT=
while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || usage
    case "$1" in
        --kernel-tree) KERNEL_TREE=$2;;
        --source-git) SOURCE_GIT=$2;;
        --build-dir|--kernel-build-dir) BUILD_DIR=$2;;
        --baseline-ref) BASELINE_REF=$2;;
        --candidate-ref) CANDIDATE_REF=$2;;
        --output-dir) OUTPUT_DIR=$2;;
        --arch) ARCH=$2;;
        --cross-compile) CROSS_COMPILE=$2;;
        --jobs) JOBS=$2;;
        *) usage;;
    esac
    shift 2
done
[ -n "$KERNEL_TREE" ] && [ -n "$BUILD_DIR" ] && [ -n "$BASELINE_REF" ] && [ -n "$CANDIDATE_REF" ] && [ -n "$OUTPUT_DIR" ] || usage
case "$JOBS" in ''|*[!0-9]*|0) usage;; esac
KERNEL_TREE=$(cd "$KERNEL_TREE" && pwd -P)
BUILD_DIR=$(cd "$BUILD_DIR" && pwd -P)
SOURCE_GIT=${SOURCE_GIT:-$KERNEL_TREE}
SOURCE_GIT=$(cd "$SOURCE_GIT" && pwd -P)
[ ! -e "$OUTPUT_DIR" ] || { echo 'Output directory already exists' >&2; exit 1; }
for config in KASAN PROVE_LOCKING SMP MODULES; do
    grep -qx "CONFIG_$config=y" "$BUILD_DIR/.config"
done
test -s "$BUILD_DIR/Module.symvers"
test -s "$BUILD_DIR/include/generated/autoconf.h"
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd -P)
cp "$SCRIPT_DIR/Makefile" "$OUTPUT_DIR/Makefile"
python3 "$SCRIPT_DIR/generate_fixture.py" --kernel-tree "$SOURCE_GIT" \
    --baseline-ref "$BASELINE_REF" --candidate-ref "$CANDIDATE_REF" \
    --output "$OUTPUT_DIR/ack_fixture_module.c"
python3 - "$OUTPUT_DIR/source-provenance.json" "$KERNEL_TREE" <<'PY_GUARD'
import hashlib
import json
import sys
from pathlib import Path

record = json.loads(Path(sys.argv[1]).read_text())
source = Path(sys.argv[2])
for name, expected in record["sha256"].items():
    if name.startswith("candidate:"):
        path = source / name.removeprefix("candidate:")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"prepared kernel source differs from candidate ref: {path}")
print("ACK_FIXTURE_PREPARED_SOURCE_MATCH")
PY_GUARD
make -C "$KERNEL_TREE" O="$BUILD_DIR" M="$OUTPUT_DIR" \
    ARCH="$ARCH" CROSS_COMPILE="$CROSS_COMPILE" -j "$JOBS" modules
test -s "$OUTPUT_DIR/ack_fixture_module.ko"
printf 'ACK_FIXTURE_MODULE_BUILD_PASS module=%s\n' "$OUTPUT_DIR/ack_fixture_module.ko"
