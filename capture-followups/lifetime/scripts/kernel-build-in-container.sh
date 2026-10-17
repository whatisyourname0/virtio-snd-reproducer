#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

: "${KERNEL_COMMIT:?KERNEL_COMMIT is required}"
: "${KERNEL_REF:?KERNEL_REF is required}"
: "${BUILD_JOBS:?BUILD_JOBS is required}"
CANDIDATE_OVERLAY_SHA256=${CANDIDATE_OVERLAY_SHA256:-}

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  bc bison build-essential ca-certificates cpio flex git libelf-dev libssl-dev \
  make perl python3 rsync tar xz-utils gzip
rm -rf /var/lib/apt/lists/*

[[ "$(uname -m)" == aarch64 ]] || {
  echo "ARM64 Docker execution required; container reports $(uname -m)" >&2
  exit 1
}

src=/tmp/linux-source
out=/tmp/linux-build
rootfs=/tmp/modules-root
observer=/tmp/observer
mkdir -p "$src" "$out" "$rootfs" "$observer" /out
tar -xzf /source.tar.gz -C "$src"
if [[ -n "$CANDIDATE_OVERLAY_SHA256" ]]; then
  [[ -r /out/candidate-overlay.diff ]] || {
    echo "candidate overlay was requested but not mounted" >&2
    exit 1
  }
  actual_overlay_sha=$(sha256sum /out/candidate-overlay.diff | cut -d' ' -f1)
  [[ "$actual_overlay_sha" == "$CANDIDATE_OVERLAY_SHA256" ]] || {
    echo "candidate overlay hash mismatch" >&2
    exit 1
  }
  git -C "$src" apply --check --whitespace=error /out/candidate-overlay.diff
  git -C "$src" apply --whitespace=error /out/candidate-overlay.diff
elif [[ -e /out/candidate-overlay.diff ]]; then
  echo "unexpected candidate overlay in source variant" >&2
  exit 1
fi
printf '%s\n' "${CANDIDATE_OVERLAY_SHA256:-none}" > /out/source-overlay-sha256.txt

/configure-kasan.sh "$src" "$out"
release=$(make -s -C "$src" O="$out" ARCH=arm64 kernelrelease)
export KBUILD_BUILD_USER=virtio-snd-reproducer
export KBUILD_BUILD_HOST=arm64-builder
export KBUILD_BUILD_TIMESTAMP='2024-01-01 00:00:00 UTC'
export SOURCE_DATE_EPOCH=1704067200
make -C "$src" O="$out" ARCH=arm64 W=1 -j"$BUILD_JOBS" Image modules
make -C "$src" O="$out" ARCH=arm64 INSTALL_MOD_PATH="$rootfs" modules_install
cp -a /guest/. "$observer/"
make -C "$src" O="$out" ARCH=arm64 M="$observer" W=1 -j1 modules

mkdir -p /out
cp "$out/arch/arm64/boot/Image" /out/Image
cp "$out/Module.symvers" /out/Module.symvers
cp "$out/.config" /out/kernel.config
cp "$out/sound/virtio/virtio_snd.ko" /out/virtio_snd.ko
cp "$observer/virtio_snd_lifetime_observer.ko" /out/virtio_snd_lifetime_observer.ko
tar -czf /out/modules.tar.gz -C "$rootfs" .
printf '%s\n' "$release" > /out/kernel-release.txt
printf '%s\n' "$KERNEL_REF" > /out/source-ref.txt
printf '%s\n' "$KERNEL_COMMIT" > /out/source-commit.txt
sha256sum /out/Image /out/virtio_snd.ko /out/virtio_snd_lifetime_observer.ko \
  /out/kernel.config /out/modules.tar.gz > /out/SHA256SUMS
