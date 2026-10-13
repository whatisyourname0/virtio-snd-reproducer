#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu

apk add --no-cache alsa-utils kmod cpio gzip
root=/tmp/guest-root
mkdir -p "$root"
cp -a /bin /etc /lib /sbin /usr "$root/"
mkdir -p "$root/dev" "$root/proc" "$root/sys/kernel/debug" "$root/tmp" "$root/run"
chmod 1777 "$root/tmp"
tar -xzf /modules.tar.gz -C "$root"
release=$(cat /kernel-release.txt)
mkdir -p "$root/lib/modules/$release/extra"
cp /observer.ko "$root/lib/modules/$release/extra/virtio_snd_lifetime_observer.ko"
depmod -b "$root" "$release"
cp /guest-init "$root/lab-init"
chmod 0755 "$root/lab-init"
cat > "$root/etc/inittab" <<'EOF'
::sysinit:/bin/sh /lab-init
EOF
rm -f "$root/init"
ln -s /bin/busybox "$root/init"
mknod -m 0600 "$root/dev/console" c 5 1
mknod -m 0666 "$root/dev/null" c 1 3
cd "$root"
find . -print0 | cpio --null -o -H newc | gzip -1 > /out/initramfs.cpio.gz
sha256sum /out/initramfs.cpio.gz > /out/initramfs.sha256
