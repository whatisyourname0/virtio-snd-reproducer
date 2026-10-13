#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly QEMU_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: build-initramfs.sh --kernel-output-dir DIR

Builds baseline/initramfs.cpio.gz and candidate/initramfs.cpio.gz from the
matching module trees and guest observer produced by build-kasan-kernels.sh.
EOF
}

kernel_output_dir=
while (($#)); do
  case "$1" in
    --kernel-output-dir) kernel_output_dir=${2:?missing value for --kernel-output-dir}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$kernel_output_dir" ]] || { usage >&2; exit 2; }
kernel_output_dir=$(cd "$kernel_output_dir" && pwd)

for mode in baseline candidate; do
  artifact_dir="$kernel_output_dir/$mode"
  [[ -s "$artifact_dir/Image" && -s "$artifact_dir/modules.tar.gz" && \
     -s "$artifact_dir/virtio_snd_lifetime_observer.ko" ]] || {
    echo "missing $mode KASAN build outputs in $artifact_dir" >&2
    exit 1
  }
  docker run --rm --platform linux/arm64 \
    --mount "type=bind,source=$artifact_dir/modules.tar.gz,target=/modules.tar.gz,readonly" \
    --mount "type=bind,source=$artifact_dir/kernel-release.txt,target=/kernel-release.txt,readonly" \
    --mount "type=bind,source=$artifact_dir/virtio_snd_lifetime_observer.ko,target=/observer.ko,readonly" \
    --mount "type=bind,source=$QEMU_DIR/guest/init,target=/guest-init,readonly" \
    --mount "type=bind,source=$SCRIPT_DIR/build-initramfs-in-container.sh,target=/build-initramfs.sh,readonly" \
    --mount "type=bind,source=$artifact_dir,target=/out" \
    alpine:3.22 /bin/sh /build-initramfs.sh
  printf '%s initramfs built: %s\n' "$mode" "$artifact_dir/initramfs.cpio.gz"
done
