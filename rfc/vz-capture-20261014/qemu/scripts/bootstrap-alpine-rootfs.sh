#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu

usage() {
    echo "usage: $0 ROOTFS_DIR" >&2
    echo "Run inside Alpine 3.22 ARM64 with apk and network access." >&2
    exit 64
}
[ "$#" -eq 1 ] || usage
root=$1
if [ -e "$root" ] || [ -L "$root" ]; then
    echo "refusing to populate existing path: $root" >&2
    exit 1
fi
mkdir -p "$root"
root=$(cd "$root" && pwd -P)
repo=https://dl-cdn.alpinelinux.org/alpine/v3.22

apk --root "$root" --arch aarch64 --initdb --keys-dir /etc/apk/keys \
    --repository "$repo/main" --repository "$repo/community" \
    add alpine-baselayout alpine-keys alpine-release apk-tools busybox \
        kmod alsa-utils alsa-lib

mkdir -p "$root"/dev "$root"/proc "$root"/sys "$root"/run "$root"/etc
printf '%s\n' '::sysinit:/bin/sh /lab-init' > "$root/etc/inittab"
ln -sfn /bin/busybox "$root/init"
echo "ROOTFS=$root"
echo "PACKAGES=alpine-baselayout alpine-keys alpine-release apk-tools busybox kmod alsa-utils alsa-lib"
echo "INIT=/etc/inittab invokes /lab-init"
