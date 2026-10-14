#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

usage() {
  cat <<'USAGE'
Usage: build-kernel.sh --source DIR --output DIR [options]

Build a fresh ARM64 KASAN Image and complete runtime module tree from a
prepared full kernel source tree using the pinned local builder image.

Options:
  --config FILE        ARM64 KASAN config (defaults to this bundle's reference)
  --jobs N             Parallel make jobs, 1 through 4 (default: 4)
  --builder-image REF  Existing ARM64 builder image (default:
                       industrial-virtio-module-builder:alpine322-gnu)
USAGE
}

source_dir=
output_dir=
config_file="$SCRIPT_DIR/../configs/arm64-kasan-reference.config"
jobs=4
builder_image=industrial-virtio-module-builder:alpine322-gnu

while (($#)); do
  case "$1" in
    --source) source_dir=${2:?missing --source value}; shift 2 ;;
    --output) output_dir=${2:?missing --output value}; shift 2 ;;
    --config) config_file=${2:?missing --config value}; shift 2 ;;
    --jobs) jobs=${2:?missing --jobs value}; shift 2 ;;
    --builder-image) builder_image=${2:?missing --builder-image value}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$source_dir" && -n "$output_dir" ]] || { usage >&2; exit 2; }
[[ "$jobs" =~ ^[1-4]$ ]] || { echo '--jobs must be between 1 and 4' >&2; exit 2; }
[[ -f "$config_file" ]] || { echo "config file missing: $config_file" >&2; exit 1; }
[[ -f "$source_dir/Makefile" ]] || { echo "kernel Makefile missing: $source_dir/Makefile" >&2; exit 1; }
[[ ! -e "$source_dir/.config" ]] || { echo 'prepared source already contains .config; use a clean source export' >&2; exit 1; }
[[ ! -e "$source_dir/include/config/auto.conf" ]] || { echo 'prepared source contains generated config; use a clean source export' >&2; exit 1; }

source_dir=$(cd -- "$source_dir" && pwd -P)
config_file=$(cd -- "$(dirname -- "$config_file")" && pwd -P)/$(basename -- "$config_file")
out_parent=$(cd -- "$(dirname -- "$output_dir")" && pwd -P)
output_dir="$out_parent/$(basename -- "$output_dir")"
if [[ -e "$output_dir" || -L "$output_dir" ]]; then
  echo "refusing to reuse an existing output directory: $output_dir" >&2
  exit 1
fi
mkdir -- "$output_dir"

# Hash every source entry, including symlink targets and file modes. The same
# manifest is recomputed inside the read-only build container before make.
python3 - "$source_dir" "$output_dir/source-tree-manifest.jsonl" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest = Path(sys.argv[2])
entries = []
for current, directories, files in os.walk(root, topdown=True, followlinks=False):
    directories.sort()
    files.sort()
    current_path = Path(current)
    for name in list(directories):
        path = current_path / name
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            entries.append({"path": relative, "type": "symlink", "target": os.readlink(path)})
            directories.remove(name)
        else:
            entries.append({"path": relative, "type": "directory", "mode": stat.S_IMODE(info.st_mode)})
    for name in files:
        path = current_path / name
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            entry = {"path": relative, "type": "symlink", "target": os.readlink(path)}
        elif stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            entry = {
                "path": relative,
                "type": "file",
                "mode": stat.S_IMODE(info.st_mode),
                "size": info.st_size,
                "sha256": digest.hexdigest(),
            }
        else:
            entry = {"path": relative, "type": "other", "mode": stat.S_IMODE(info.st_mode)}
        entries.append(entry)
entries.sort(key=lambda entry: entry["path"])
with manifest.open("w", encoding="utf-8", newline="\n") as stream:
    for entry in entries:
        stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
print(f"entries={len(entries)}")
PY

builder_id=$(docker image inspect --format '{{.Id}}' "$builder_image")
builder_arch=$(docker image inspect --format '{{.Architecture}}' "$builder_image")
builder_os=$(docker image inspect --format '{{.Os}}' "$builder_image")
[[ "$builder_arch" == arm64 && "$builder_os" == linux ]] || {
  echo "builder must be linux/arm64; got $builder_os/$builder_arch" >&2
  exit 1
}

exec > >(tee -a "$output_dir/build.log") 2>&1
printf 'source=%s\n' "$source_dir"
printf 'config=%s\n' "$config_file"
printf 'output=%s\n' "$output_dir"
printf 'jobs=%s\n' "$jobs"
printf 'builder_image=%s\n' "$builder_image"
printf 'builder_image_id=%s\n' "$builder_id"
printf 'builder_platform=%s/%s\n' "$builder_os" "$builder_arch"
printf 'source_manifest_sha256='
python3 - "$output_dir/source-tree-manifest.jsonl" <<'PYHASH'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PYHASH
printf 'input_config_sha256='
python3 - "$config_file" <<'PYHASH'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PYHASH
printf '%s\n' "$builder_image" > "$output_dir/builder-image-ref.txt"
printf '%s\n' "$builder_id" > "$output_dir/builder-image-id.txt"
printf '%s\n' "$source_dir" > "$output_dir/source-path.txt"
printf '%s\n' "$config_file" > "$output_dir/config-path.txt"

docker run --rm --pull=never --platform linux/arm64 --network none --cpus "$jobs" \
  --mount "type=bind,source=$source_dir,target=/source,readonly" \
  --mount "type=bind,source=$config_file,target=/kernel.config,readonly" \
  --mount "type=bind,source=$output_dir/source-tree-manifest.jsonl,target=/source-tree-manifest.jsonl,readonly" \
  --mount "type=bind,source=$SCRIPT_DIR/kernel-build-in-container.sh,target=/kernel-build-in-container.sh,readonly" \
  --mount "type=bind,source=$output_dir,target=/out" \
  --env BUILD_JOBS="$jobs" \
  --env BUILDER_IMAGE_REF="$builder_image" \
  --env BUILDER_IMAGE_ID="$builder_id" \
  "$builder_id" /bin/sh /kernel-build-in-container.sh

python3 - "$output_dir" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_root = root / "modules" / "lib" / "modules"
release_file = root / "kernel-release.txt"
release = release_file.read_text(encoding="utf-8").strip()
module_tree = module_root / release
if not module_tree.is_dir():
    raise SystemExit(f"module tree missing for release {release}")
entries = []
for current, directories, files in os.walk(module_tree, topdown=True, followlinks=False):
    directories.sort()
    files.sort()
    current_path = Path(current)
    for name in list(directories):
        path = current_path / name
        if path.is_symlink():
            entries.append({"path": path.relative_to(module_tree).as_posix(), "type": "symlink", "target": os.readlink(path)})
            directories.remove(name)
        else:
            info = path.lstat()
            entries.append({"path": path.relative_to(module_tree).as_posix(), "type": "directory", "mode": stat.S_IMODE(info.st_mode)})
    for name in files:
        path = current_path / name
        info = path.lstat()
        relative = path.relative_to(module_tree).as_posix()
        if stat.S_ISLNK(info.st_mode):
            entry = {"path": relative, "type": "symlink", "target": os.readlink(path)}
        elif stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            entry = {"path": relative, "type": "file", "mode": stat.S_IMODE(info.st_mode), "size": info.st_size, "sha256": digest.hexdigest()}
        else:
            entry = {"path": relative, "type": "other", "mode": stat.S_IMODE(info.st_mode)}
        entries.append(entry)
entries.sort(key=lambda entry: entry["path"])
manifest = root / "module-tree-manifest.jsonl"
with manifest.open("w", encoding="utf-8", newline="\n") as stream:
    for entry in entries:
        stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
artifacts = [
    "Image", "kernel.config", "System.map", "Module.symvers", "kernel-release.txt",
    "source-tree-manifest.jsonl", "module-tree-manifest.jsonl", "builder-packages.txt",
    "toolchain.txt", "depmod.log", "build-inputs.txt",
]
with (root / "artifact-sha256.txt").open("w", encoding="utf-8") as stream:
    for name in artifacts:
        path = root / name
        if not path.is_file():
            raise SystemExit(f"required build output missing: {name}")
        stream.write(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}\n")
print(f"module_tree_entries={len(entries)}")
print(f"module_tree={module_tree}")
PY
printf 'fresh kernel artifacts written to %s\n' "$output_dir"
