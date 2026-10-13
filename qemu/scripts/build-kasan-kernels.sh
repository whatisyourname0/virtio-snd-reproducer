#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly QEMU_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly SERIES_BASE=8d70e364e866bdfca81eee24d0688fac9472cfb9

usage() {
  cat <<'EOF'
Usage: build-kasan-kernels.sh --kernel-tree DIR --output-dir DIR
       [--baseline-ref REF] [--candidate-ref REF] [--jobs N]

The supplied Linux checkout must contain the five patch commits at its tip.
Defaults: baseline REF=HEAD~2 (base plus patches 1-3), candidate REF=HEAD.
Builds only ARM64 KASAN images, matching modules, and the guest observer.
EOF
}

kernel_tree=
output_dir=
baseline_ref=HEAD~2
candidate_ref=HEAD
jobs=2
while (($#)); do
  case "$1" in
    --kernel-tree) kernel_tree=${2:?missing value for --kernel-tree}; shift 2 ;;
    --output-dir) output_dir=${2:?missing value for --output-dir}; shift 2 ;;
    --baseline-ref) baseline_ref=${2:?missing value for --baseline-ref}; shift 2 ;;
    --candidate-ref) candidate_ref=${2:?missing value for --candidate-ref}; shift 2 ;;
    --jobs) jobs=${2:?missing value for --jobs}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$kernel_tree" && -n "$output_dir" ]] || { usage >&2; exit 2; }
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo "--jobs must be a positive integer" >&2; exit 2; }
kernel_tree=$(cd "$kernel_tree" && pwd)
output_dir=$(mkdir -p "$output_dir" && cd "$output_dir" && pwd)
git -C "$kernel_tree" rev-parse --show-toplevel >/dev/null
candidate_commit=$(git -C "$kernel_tree" rev-parse --verify "${candidate_ref}^{commit}")
baseline_commit=$(git -C "$kernel_tree" rev-parse --verify "${baseline_ref}^{commit}")
series_base=$(git -C "$kernel_tree" rev-parse --verify "${candidate_commit}~5")
expected_baseline=$(git -C "$kernel_tree" rev-parse --verify "${candidate_commit}~2")
[[ "$series_base" == "$SERIES_BASE" ]] || {
  echo "candidate ref must contain the five-patch series based on $SERIES_BASE" >&2
  exit 1
}
[[ "$baseline_commit" == "$expected_baseline" ]] || {
  echo "baseline ref must resolve to candidate-ref~2 (patches 1-3)" >&2
  exit 1
}

build_variant() {
  local mode=$1 ref=$2 commit archive archive_sha variant_dir
  commit=$(git -C "$kernel_tree" rev-parse --verify "${ref}^{commit}")
  variant_dir="$output_dir/$mode"
  mkdir -p "$variant_dir"
  [[ ! -s "$variant_dir/Image" ]] || {
    echo "refusing to overwrite existing artifact: $variant_dir/Image" >&2
    exit 1
  }
  archive=$(mktemp "${TMPDIR:-/tmp}/virtio-snd-${mode}.XXXXXX")
  trap 'rm -f "$archive"' EXIT
  git -C "$kernel_tree" archive --format=tar "$commit" | gzip -1 > "$archive"
  archive_sha=$(python3 - "$archive" <<'PY'
import hashlib
import sys
digest = hashlib.sha256()
with open(sys.argv[1], "rb") as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
print(digest.hexdigest())
PY
)

  docker run --rm --platform linux/arm64 \
    --mount "type=bind,source=$archive,target=/source.tar.gz,readonly" \
    --mount "type=bind,source=$QEMU_DIR/guest,target=/guest,readonly" \
    --mount "type=bind,source=$SCRIPT_DIR/configure-kasan.sh,target=/configure-kasan.sh,readonly" \
    --mount "type=bind,source=$SCRIPT_DIR/kernel-build-in-container.sh,target=/kernel-build.sh,readonly" \
    --mount "type=bind,source=$variant_dir,target=/out" \
    --env KERNEL_REF="$ref" --env KERNEL_COMMIT="$commit" \
    --env BUILD_JOBS="$jobs" debian:bookworm-slim /bin/bash /kernel-build.sh

  python3 - "$variant_dir" "$ref" "$commit" "$archive_sha" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

directory = Path(sys.argv[1])
ref, commit, archive_sha = sys.argv[2:]
names = ("Image", "virtio_snd.ko", "virtio_snd_lifetime_observer.ko", "kernel.config", "modules.tar.gz")
manifest = {
    "schema_version": 1,
    "artifact_origin": "built_from_source",
    "arch": "arm64",
    "mode": directory.name,
    "source_ref": ref,
    "source_commit": commit,
    "source_archive_sha256": archive_sha,
    "kernel_release": (directory / "kernel-release.txt").read_text().strip(),
    "artifacts": {
        name: {
            "path": str((directory / name).resolve()),
            "sha256": hashlib.sha256((directory / name).read_bytes()).hexdigest(),
        }
        for name in names
    },
}
(directory / "kernel-build.json").write_text(
    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
)
PY
  rm -f "$archive"
  trap - EXIT
  printf '%s kernel built from %s (%s)\n' "$mode" "$ref" "$commit"
}

build_variant baseline "$baseline_ref"
build_variant candidate "$candidate_ref"
