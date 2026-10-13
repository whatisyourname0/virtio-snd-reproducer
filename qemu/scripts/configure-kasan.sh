#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

src=${1:?kernel source directory required}
out=${2:?kernel output directory required}

make -C "$src" O="$out" ARCH=arm64 allnoconfig
for symbol in SMP EXPERT MULTIUSER PRINTK BUG ELF_CORE BINFMT_ELF BLK_DEV_INITRD RD_GZIP \
  MODULES MODULE_UNLOAD DEVTMPFS DEVTMPFS_MOUNT PROC_FS SYSFS TMPFS TMPFS_POSIX_ACL \
  TTY SERIAL_AMBA_PL011 SERIAL_AMBA_PL011_CONSOLE PCI PCI_HOST_GENERIC VIRTIO_MENU \
  VIRTIO VIRTIO_PCI VIRTIO_PCI_LEGACY SOUND SYSVIPC POSIX_TIMERS FUTEX EPOLL EVENTFD \
  SIGNALFD TIMERFD UNIX NET INET UNIX98_PTYS DEBUG_KERNEL DEBUG_FS KALLSYMS KALLSYMS_ALL \
  KPROBES KRETPROBES KASAN KASAN_GENERIC KASAN_OUTLINE KASAN_VMALLOC STACKTRACE \
  VMAP_STACK PM SUSPEND; do
  "$src/scripts/config" --file "$out/.config" --enable "$symbol"
done
for symbol in SND SND_PCM SND_TIMER SND_VIRTIO; do
  "$src/scripts/config" --file "$out/.config" --module "$symbol"
done
"$src/scripts/config" --file "$out/.config" --set-val NR_CPUS 4 \
  --set-str LOCALVERSION '-snd-lifetime-kasan' --disable LOCALVERSION_AUTO \
  --enable DEBUG_INFO_NONE --disable DEBUG_INFO_BTF --disable MODULE_SIG
make -C "$src" O="$out" ARCH=arm64 olddefconfig

for entry in CONFIG_KASAN=y CONFIG_KASAN_GENERIC=y CONFIG_KASAN_VMALLOC=y \
  CONFIG_KPROBES=y CONFIG_KRETPROBES=y CONFIG_SND_VIRTIO=m CONFIG_PCI_HOST_GENERIC=y \
  CONFIG_SERIAL_AMBA_PL011_CONSOLE=y CONFIG_BLK_DEV_INITRD=y CONFIG_BINFMT_ELF=y; do
  grep -qx "$entry" "$out/.config" || { echo "Missing $entry" >&2; exit 1; }
done
