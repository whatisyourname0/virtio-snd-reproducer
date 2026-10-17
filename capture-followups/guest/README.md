# Build a sequence-test guest

Run the kernel build in Linux, either natively or in a Linux container.
The optional Docker userspace-preparation step creates only an ARM64 rootfs.
This recipe does not run a microphone-enabled VM.
Source patches and build inputs are explicit; no private image or named volume
is needed. Native ARM64 Linux can omit `CROSS_COMPILE` in every make command.

## Kernel

First prepare the patch stack using the top-level README.
Use a new destination repository that contains no other work.
The preparation script creates the `refs/reproducer/ack_plus_highpri` tree used by this recipe.
Verify its provenance before building.

```sh
package=/absolute/path/to/virtio-snd-upstream-reproducer
python3 "$package/provenance/prepare_kernel_stack.py" \
  --source /absolute/path/to/linux-git \
  --destination /absolute/path/to/new-prepared-tree
```

The Linux source contains case-distinct paths.
Keep both the source tree and `O=` build directory on a case-sensitive Linux filesystem.
A checkout on a case-insensitive macOS volume can overwrite files with names that differ only by case.
Choose one of the following source layouts; do not run both.

For a native Linux build, or a Linux container with a case-sensitive filesystem, use the prepared repository directly.
Set `kernel_source` to its path, then populate its working tree with the selected source tree:

```sh
kernel_source=/absolute/path/to/new-prepared-tree
git -C "$kernel_source" read-tree --reset -u refs/reproducer/ack_plus_highpri
```

For a Docker build on macOS, mount the prepared Git repository read-only as `/source-git`.
Run the archive commands inside the Linux container.
The archive has no `.git` directory, so do not run `git read-tree` for this layout.
Keep the kernel build directory inside the container and mount only the artifacts directory back to the host.

```sh
mkdir -p /tmp/linux-source
git -C /source-git archive --format=tar refs/reproducer/ack_plus_highpri \
  | tar -xf - -C /tmp/linux-source
kernel_source=/tmp/linux-source
```

Keep the `kernel_source` value selected above for the rest of the recipe.
The common build commands do not reassign it.
A cross-build host needs an ARM64 compiler, binutils, make, flex, bison, OpenSSL and libelf development headers, and standard kernel build dependencies.

```sh
package=/absolute/path/to/virtio-snd-upstream-reproducer
kernel_build=/absolute/path/to/new-kernel-build
module_install=/absolute/path/to/new-module-install

make -C "$kernel_source" O="$kernel_build" ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- defconfig
"$kernel_source/scripts/kconfig/merge_config.sh" -m -O "$kernel_build" \
  "$kernel_build/.config" "$package/guest/kernel.fragment"
make -C "$kernel_source" O="$kernel_build" ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- olddefconfig
make -C "$kernel_source" O="$kernel_build" ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- -j2 Image modules
make -C "$kernel_source" O="$kernel_build" ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- INSTALL_MOD_PATH="$module_install" modules_install
release=$(make -s -C "$kernel_source" O="$kernel_build" ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- kernelrelease)
depmod -b "$module_install" "$release"
```

Keep the complete `.config`, source tree ID, compiler version and build log.
`kernel.fragment` enables the capture boot path. It deliberately does not claim
KASAN/lockdep coverage. Build the ACK fixture module and its kernel from the
`ack_only` candidate tree so the fixture's extracted functions match the kernel.
Build the final `ack_plus_highpri` kernel separately; do not pair that tree with
the ACK-only fixture module. Enable the debug options listed in `ack/README.md`
for the ACK-only kernel and run that fixture separately.

## Userspace and initramfs

An existing ARM64 BusyBox rootfs with ALSA utilities and kmod is acceptable.
Alternatively the following creates one from public Alpine 3.22. A Docker host
that can run ARM64 Linux containers is needed for this optional preparation.
The image tag and package repository are moving inputs; record the resolved image
digest and installed package list when retaining reproducibility evidence.

```sh
rootfs_archive=/absolute/path/to/new-alpine-rootfs.tar.gz
rootfs=/absolute/path/to/new-rootfs-directory
initrd_output=/absolute/path/to/new-initrd-output

"$package/guest/prepare_rootfs.sh" --output "$rootfs_archive"
mkdir "$rootfs"
tar -xzf "$rootfs_archive" -C "$rootfs"
python3 "$package/guest/build_initrd.py" \
  --rootfs "$rootfs" \
  --modules "$module_install/lib/modules/$release" \
  --release "$release" --kernel-config "$kernel_build/.config" \
  --output-dir "$initrd_output"
```

The packer stages a copy, supplies a finite BusyBox init setup and an `hvc0` root
shell, and writes `initramfs.cpio.gz` plus a build manifest.
BusyBox init invokes the setup script through `/bin/sh`, so this entrypoint also
works with older fixture kernels that disable `CONFIG_BINFMT_SCRIPT`. The config
fragment still enables normal shebang execution for newly built kernels. Module indexes must
already be generated for the exact selected release. The original rootfs and
installed modules are preserved. Device nodes are created by devtmpfs at boot;
the supplied rootfs should not contain pre-created special device files.

Use `$kernel_build/arch/arm64/boot/Image` and
`$initrd_output/initramfs.cpio.gz` with the top-level sequence runner commands.
The boot script emits the ready marker only after `virtio_snd` loads and a
capture PCM device is present. The root shell is intentional for this isolated
test guest, and the QEMU runners configure no network device.
