#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu

: "${BUILD_JOBS:?BUILD_JOBS is required}"
: "${BUILDER_IMAGE_REF:?BUILDER_IMAGE_REF is required}"
: "${BUILDER_IMAGE_ID:?BUILDER_IMAGE_ID is required}"
case "$BUILD_JOBS" in 1|2|3|4) ;; *) echo "invalid BUILD_JOBS=$BUILD_JOBS" >&2; exit 2 ;; esac
[ "$(uname -m)" = aarch64 ] || { echo "expected native aarch64 builder, got $(uname -m)" >&2; exit 1; }
[ -r /source/Makefile ] || { echo 'source is not a full kernel tree' >&2; exit 1; }
[ -r /kernel.config ] || { echo 'kernel config is unreadable' >&2; exit 1; }

mkdir -p /tmp/kernel-build /tmp/module-stage /out/modules
rm -rf /tmp/kernel-build/* /tmp/kernel-build/.[!.]* /tmp/module-stage/* /tmp/module-stage/.[!.]*
python3 - /source /source-tree-manifest.jsonl <<'PY'
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
            entry = {"path": relative, "type": "file", "mode": stat.S_IMODE(info.st_mode), "size": info.st_size, "sha256": digest.hexdigest()}
        else:
            entry = {"path": relative, "type": "other", "mode": stat.S_IMODE(info.st_mode)}
        entries.append(entry)
entries.sort(key=lambda entry: entry["path"])
lines = [json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n" for entry in entries]
actual = "".join(lines)
expected = manifest.read_text(encoding="utf-8")
if actual != expected:
    raise SystemExit("source tree changed between export verification and container build")
print(f"verified_source_entries={len(entries)}")
PY

{
  printf 'builder_image_ref=%s\n' "$BUILDER_IMAGE_REF"
  printf 'builder_image_id=%s\n' "$BUILDER_IMAGE_ID"
  printf 'platform=%s\n' "$(uname -m)"
  printf 'kernel=%s\n' "$(uname -a)"
  printf 'build_jobs=%s\n' "$BUILD_JOBS"
  printf 'build_user=%s\n' 'rfc-kasan-builder'
  printf 'build_host=%s\n' 'arm64-builder'
  printf 'build_timestamp=%s\n' '2026-10-14 00:00:00 UTC'
  printf 'source_date_epoch=%s\n' '1791936000'
} > /out/build-inputs.txt
apk info -vv > /out/builder-packages.txt
gcc -v > /out/toolchain.txt 2>&1
{
  printf '\n--- gcc target ---\n'
  gcc -dumpmachine
  printf '\n--- make ---\n'
  make --version | head -n 1
  printf '\n--- binutils ---\n'
  ld --version | head -n 1
} >> /out/toolchain.txt

cp /kernel.config /tmp/kernel-build/.config
export ARCH=arm64
export KBUILD_BUILD_USER=rfc-kasan-builder
export KBUILD_BUILD_HOST=arm64-builder
export KBUILD_BUILD_TIMESTAMP='2026-10-14 00:00:00 UTC'
export SOURCE_DATE_EPOCH=1791936000

make -C /source O=/tmp/kernel-build ARCH=arm64 olddefconfig
assert_config() {
  key=$1
  value=$2
  grep -qx "${key}=${value}" /tmp/kernel-build/.config || {
    echo "required kernel config was not enabled: ${key}=${value}" >&2
    exit 1
  }
}
assert_config CONFIG_ARM64 y
assert_config CONFIG_KASAN y
assert_config CONFIG_KASAN_GENERIC y
assert_config CONFIG_MODULES y
assert_config CONFIG_DEVTMPFS y
assert_config CONFIG_SND m
assert_config CONFIG_SND_TIMER m
assert_config CONFIG_SND_PCM m
assert_config CONFIG_SND_VIRTIO m
assert_config CONFIG_VIRTIO y
release=$(make -s -C /source O=/tmp/kernel-build ARCH=arm64 kernelrelease)
printf '%s\n' "$release" > /out/kernel-release.txt
cp /tmp/kernel-build/.config /out/kernel.config

make -C /source O=/tmp/kernel-build ARCH=arm64 W=1 -j"$BUILD_JOBS" Image modules
make -C /source O=/tmp/kernel-build ARCH=arm64 INSTALL_MOD_PATH=/tmp/module-stage modules_install
depmod -b /tmp/module-stage -F /tmp/kernel-build/System.map "$release" > /out/depmod.log 2>&1
module_root="/tmp/module-stage/lib/modules/$release"
[ -d "$module_root" ] || { echo "modules_install output missing: $module_root" >&2; exit 1; }
for module in \
  "$module_root/kernel/sound/core/snd.ko" \
  "$module_root/kernel/sound/core/snd-timer.ko" \
  "$module_root/kernel/sound/core/snd-pcm.ko" \
  "$module_root/kernel/sound/virtio/virtio_snd.ko"; do
  [ -f "$module" ] || { echo "required sound module missing: $module" >&2; exit 1; }
  vermagic=$(modinfo -F vermagic "$module")
  case "$vermagic" in "$release"*) ;; *) echo "module release mismatch: $module: $vermagic" >&2; exit 1 ;; esac
done
for index in modules.dep modules.alias modules.builtin modules.softdep; do
  [ -f "$module_root/$index" ] || { echo "depmod index missing: $module_root/$index" >&2; exit 1; }
done
grep -q 'kernel/sound/core/snd-pcm.ko:' "$module_root/modules.dep"
grep -q 'kernel/sound/virtio/virtio_snd.ko:' "$module_root/modules.dep"
# These are build-environment convenience links, not runtime module dependencies.
rm -f "$module_root/build" "$module_root/source"

cp /tmp/kernel-build/arch/arm64/boot/Image /out/Image
cp /tmp/kernel-build/System.map /out/System.map
cp /tmp/kernel-build/Module.symvers /out/Module.symvers
mkdir -p "/out/modules/lib/modules"
cp -a "$module_root" "/out/modules/lib/modules/$release"
sha256sum /out/Image /out/kernel.config /out/System.map /out/Module.symvers > /out/build-artifacts.sha256
printf 'kernel_release=%s\n' "$release"
printf 'image_bytes=%s\n' "$(wc -c < /out/Image | tr -d ' ')"
printf 'sound_modules=\n'
find "$module_root/kernel/sound" -type f -name '*.ko' -print | sort
printf 'modules_dep_entries=%s\n' "$(wc -l < "$module_root/modules.dep" | tr -d ' ')"
printf 'depmod_status=success\n'
