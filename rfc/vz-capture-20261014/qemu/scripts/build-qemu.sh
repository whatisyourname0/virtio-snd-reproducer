#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu

usage() {
    echo "usage: $0 QEMU_11_1_2_SOURCE_DIR BUILD_DIR [JOBS] [--extra-patch PATCH]..." >&2
    exit 64
}

[ "$#" -ge 2 ] || usage
src=$(cd "$1" && pwd -P)
build=$2
shift 2
jobs=2
if [ "$#" -gt 0 ] && [ "$1" != "--extra-patch" ]; then
    jobs=$1
    shift
fi
patch_dir=$(cd "$(dirname "$0")/.." && pwd -P)/patches
base_patch="$patch_dir/qemu-capture-faults.patch"
delta_patch="$patch_dir/qemu-capture-drain-r1.patch"
lifecycle_patch="$patch_dir/qemu-fsm-control-delay.patch"

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

[ -f "$src/VERSION" ] || { echo "missing QEMU VERSION file: $src" >&2; exit 1; }
version=$(tr -d '[:space:]' < "$src/VERSION")
[ "$version" = "11.1.2" ] || {
    echo "expected QEMU 11.1.2 source, found $version" >&2
    exit 1
}
[ -f "$base_patch" ] || { echo "missing patch: $base_patch" >&2; exit 1; }
[ -f "$delta_patch" ] || { echo "missing patch: $delta_patch" >&2; exit 1; }
[ -f "$lifecycle_patch" ] || { echo "missing patch: $lifecycle_patch" >&2; exit 1; }

snd="$src/hw/audio/virtio-snd.c"
header="$src/include/hw/audio/virtio-snd.h"
expected_snd=fe33f3fdf897c98cb1cceae49121f0d28ea3ae783523c41e8a9b9a3af37ec821
expected_header=23ed716769ced2f13b54a38f32d56172183ddc858dd476c98217b2823502d53e
actual_snd=$(sha256 "$snd")
actual_header=$(sha256 "$header")
[ "$actual_snd" = "$expected_snd" ] || {
    echo "QEMU source is not pristine at hw/audio/virtio-snd.c: $actual_snd" >&2
    exit 1
}
[ "$actual_header" = "$expected_header" ] || {
    echo "QEMU source is not pristine at include/hw/audio/virtio-snd.h: $actual_header" >&2
    exit 1
}

echo "Checking capture-fault patch against pristine QEMU $version"
patch --dry-run -p1 -d "$src" < "$base_patch"
patch -p1 -d "$src" < "$base_patch"
echo "Checking delayed short-reply patch against the capture-fault base"
patch --dry-run -p1 -d "$src" < "$delta_patch"
patch -p1 -d "$src" < "$delta_patch"
echo "Checking FSM control-delay patch against the capture-drain base"
patch --dry-run -p1 -d "$src" < "$lifecycle_patch"
patch -p1 -d "$src" < "$lifecycle_patch"

while [ "$#" -gt 0 ]; do
    [ "$1" = "--extra-patch" ] && [ "$#" -ge 2 ] || usage
    extra_patch=$2
    [ -f "$extra_patch" ] || { echo "missing extra patch: $extra_patch" >&2; exit 1; }
    extra_patch=$(cd "$(dirname "$extra_patch")" && pwd -P)/$(basename "$extra_patch")
    echo "Checking optional patch: $extra_patch"
    patch --dry-run -p1 -d "$src" < "$extra_patch"
    patch -p1 -d "$src" < "$extra_patch"
    echo "QEMU_EXTRA_PATCH=$(sha256 "$extra_patch")  $extra_patch"
    shift 2
done

mkdir -p "$build"
build=$(cd "$build" && pwd -P)
(
    cd "$build"
    "$src/configure" \
        --target-list=aarch64-softmmu \
        --disable-docs \
        --disable-gtk \
        --disable-sdl \
        --disable-opengl \
        --disable-vnc \
        --disable-curses \
        --disable-xen
)
if [ -x "$build/pyvenv/bin/meson" ]; then
    (cd "$build" && ./pyvenv/bin/meson configure -Dpvg=disabled)
fi
ninja -C "$build" -j "$jobs" qemu-system-aarch64
echo "QEMU_BINARY=$build/qemu-system-aarch64"
echo "QEMU_SOURCE=$src"
echo "QEMU_CAPTURE_FAULT_PATCH=$(sha256 "$base_patch")"
echo "QEMU_CAPTURE_DRAIN_DELTA=$(sha256 "$delta_patch")"
echo "QEMU_FSM_CONTROL_DELAY=$(sha256 "$lifecycle_patch")"
