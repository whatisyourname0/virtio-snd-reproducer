#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly QEMU_VERSION=11.1.2
readonly QEMU_URL="https://download.qemu.org/qemu-${QEMU_VERSION}.tar.xz"
readonly QEMU_SHA256=731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016
readonly PATCH_FILE="${SCRIPT_DIR}/../qemu-test-backend.patch"

usage() {
  cat <<'EOF'
Usage: build-qemu.sh --work-dir DIR --output-dir DIR [--jobs N]

Downloads and verifies QEMU 11.1.2, applies the bundled test-backend patch,
builds aarch64-softmmu, and writes a fresh build manifest beside the binary.
The work directory must be new. The output directory may already exist.
EOF
}

work_dir=
output_dir=
jobs=2
while (($#)); do
  case "$1" in
    --work-dir) work_dir=${2:?missing value for --work-dir}; shift 2 ;;
    --output-dir) output_dir=${2:?missing value for --output-dir}; shift 2 ;;
    --jobs) jobs=${2:?missing value for --jobs}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$work_dir" && -n "$output_dir" ]] || { usage >&2; exit 2; }
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo "--jobs must be a positive integer" >&2; exit 2; }
[[ -f "$PATCH_FILE" ]] || { echo "missing QEMU test patch: $PATCH_FILE" >&2; exit 1; }
work_dir=$(mkdir -p "$work_dir" && cd "$work_dir" && pwd)
output_dir=$(mkdir -p "$output_dir" && cd "$output_dir" && pwd)
source_dir="$work_dir/qemu-${QEMU_VERSION}"
build_dir="$work_dir/build"
archive="$work_dir/qemu-${QEMU_VERSION}.tar.xz"
binary="$build_dir/qemu-system-aarch64"

for path in "$source_dir" "$build_dir" "$archive"; do
  [[ ! -e "$path" ]] || { echo "work path already exists: $path (choose a fresh --work-dir)" >&2; exit 1; }
done

curl --fail --location --retry 3 "$QEMU_URL" --output "$archive"
actual=$(python3 - "$archive" <<'PY'
import hashlib
import sys
digest = hashlib.sha256()
with open(sys.argv[1], "rb") as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
print(digest.hexdigest())
PY
)
[[ "$actual" == "$QEMU_SHA256" ]] || {
  echo "QEMU source checksum mismatch: $actual" >&2
  exit 1
}
tar -xf "$archive" -C "$work_dir"
patch --batch --forward --dry-run -p1 -d "$source_dir" < "$PATCH_FILE"
patch --batch --forward -p1 -d "$source_dir" < "$PATCH_FILE"
mkdir -p "$build_dir"
(
  cd "$build_dir"
  "$source_dir/configure" --target-list=aarch64-softmmu -Dpvg=disabled
)
ninja -C "$build_dir" -j"$jobs" qemu-system-aarch64

install -m 0755 "$binary" "$output_dir/qemu-system-aarch64"
python3 - "$output_dir" "$archive" "$PATCH_FILE" <<'PY'
import hashlib
import json
import platform
import sys
from pathlib import Path

output_dir, archive_path, patch_path = map(Path, sys.argv[1:])
binary = output_dir / "qemu-system-aarch64"
def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()
manifest = {
    "schema_version": 1,
    "artifact_origin": "built_from_source",
    "qemu_version": "11.1.2",
    "source_url": "https://download.qemu.org/qemu-11.1.2.tar.xz",
    "source_sha256": digest(archive_path),
    "test_patch_sha256": digest(patch_path),
    "configure_args": ["--target-list=aarch64-softmmu", "-Dpvg=disabled"],
    "binary": str(binary.resolve()),
    "binary_sha256": digest(binary),
    "build_host": {"system": platform.system(), "machine": platform.machine()},
}
(output_dir / "qemu-build.json").write_text(
    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
)
PY
printf 'QEMU %s built: %s\n' "$QEMU_VERSION" "$output_dir/qemu-system-aarch64"
