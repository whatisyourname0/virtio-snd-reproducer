# Signed Sashiko follow-up patches and linked-drain reproducer

This source-only package carries four DCO-signed follow-up patches and a reproducible synthetic linked-capture test. It contains no kernel image, module binary, QEMU executable, initrd, root filesystem, or runtime log.

The earlier v3 reproducer and parent RFC snapshot are historical baselines. The parent RFC snapshot has the three unchanged follow-up patches and the originally submitted core 2/2 patch; this directory contains the corrected, signed core 2/2 replacement. Use the four signed mail patches and patch IDs here for the follow-up stack. Do not substitute the superseded core 2/2 patch or its old ID.

## Patch order and dependencies

| Patch | Stable patch ID | Prerequisite |
| --- | --- | --- |
| Generic control wait | `cfe3e7000e695a2fda27993f4d9acf0a637cf24e` | v3 baseline |
| Core capture drain 1/2 | `0fc5fec2c6ac5d3043cf2210570472ed4ce32952` | v3 baseline |
| Core capture drain 2/2, corrected signed patch | `47ef5d4c76bf044a99192c8133e6ab9755776bde` | core capture drain 1/2 |
| VZ capture compatibility | `771e8815f2b874d98376f309dc60bc90244f378c` | v3 baseline, generic control wait, and corrected core capture drain 2/2 |

The generic and core series can be reviewed independently after v3. The VZ series depends on both. Do not substitute the superseded core 2/2 patch ID listed in `PATCH-IDS.txt`.

`SOURCE-HASHES.json` records the eight modified kernel source files. `PATCH-SHA256SUMS.txt` identifies the exact signed mail diffs, and `PACKAGE-SHA256SUMS.txt` lists every other package file. `RESULTS.txt` separates the full kernel and existing ten-scenario validation from the packaging smoke for this linked fixture. `kernel.config` is the exact ARM64 KASAN, lockdep, and debug-atomic-sleep config used for that fresh kernel build.

## Prepare the corrected source

The source preparer takes an existing kernel Git checkout containing the published base commit. It exports the base, applies the five already-submitted v3 prerequisites, then applies these four signed patches without committing or modifying the input checkout.

```sh
set -eu
PUBLIC_REPO=/path/to/virtio-snd-reproducer
FOLLOWUP="$PUBLIC_REPO/rfc/vz-capture-20261014/sashiko-followup"
KERNEL_GIT=/path/to/sound.git
WORK=/path/to/fresh-followup-validation

mkdir -p "$WORK"
"$FOLLOWUP/prepare-followup-source.sh" \
  "$PUBLIC_REPO" "$KERNEL_GIT" "$WORK/kernel-source"
```

Each output directory must be new. The script verifies each follow-up stable patch ID and checks every diff before applying it. It creates no local commits or signoffs.

## Build the fresh ARM64 kernel and linked fixture

Build the kernel with the exact config included here. The builder Dockerfile pins its Alpine base image by digest; build the image once for the local Docker engine.

```sh
QEMU_BUNDLE="$PUBLIC_REPO/rfc/vz-capture-20261014/qemu"
docker build --platform linux/arm64 \
  --file "$QEMU_BUNDLE/Dockerfile.kernel-builder" \
  --tag industrial-virtio-module-builder:alpine322-gnu "$QEMU_BUNDLE"
"$QEMU_BUNDLE/scripts/build-kernel.sh" \
  --source "$WORK/kernel-source" \
  --output "$WORK/kernel" \
  --config "$FOLLOWUP/kernel.config" \
  --jobs 4
RELEASE=$(cat "$WORK/kernel/kernel-release.txt")
```

Build the test-only ALSA module against that kernel's `Module.symvers`. The script uses the Docker engine's current context by default. Set `DOCKER_CONTEXT` to select a named context or `BUILDER_IMAGE` to select another compatible ARM64 builder image.

```sh
"$FOLLOWUP/linked-fixture/scripts/build-synthetic-module.sh" \
  "$WORK/kernel-source" "$WORK/kernel" \
  "$FOLLOWUP/linked-fixture/source" "$WORK/module"
```

Build the guest probes and a fresh Alpine 3.22 ARM64 root filesystem from the public bootstrap script. The bootstrap writes `/init -> /bin/busybox` and an `/etc/inittab` entry that invokes `/lab-init`; no private prebuilt rootfs is used. Record the resolved Alpine package versions with `apk info -vv` because the v3.22 package repository advances.

```sh
mkdir -p "$WORK/guest"
docker run --platform linux/arm64 --rm \
  --volume "$PUBLIC_REPO:/public:ro" \
  --volume "$WORK/guest:/out" \
  alpine:3.22.6 sh -ec '
    apk add --no-cache build-base alsa-lib-dev
    /public/rfc/vz-capture-20261014/sashiko-followup/linked-fixture/scripts/build-linked-probes.sh /public /out/probes
    /public/rfc/vz-capture-20261014/qemu/scripts/bootstrap-alpine-rootfs.sh /out/base-rootfs
    apk --root /out/base-rootfs info -vv > /out/apk-info-vv.txt
  '
python3 "$FOLLOWUP/linked-fixture/scripts/prepare-initrd.py" \
  --kernel-artifacts "$WORK/kernel" \
  --module-artifacts "$WORK/module" \
  --base-rootfs "$WORK/guest/base-rootfs" \
  --probe-dir "$WORK/guest/probes" \
  --output-dir "$WORK/linked-fixture"
```

The probe builder reuses the published playback guard source and compiles the linked-capture probe from this package. The initrd packer is Python-only and refuses root filesystems without the public bootstrap's busybox init, inittab, and mount points.

## Build and run the synthetic QEMU test

Use a fresh QEMU 11.1.2 source extraction. The public build script verifies the archive's pristine source hashes and applies its ordered synthetic-audio patch stack. Its output is a test backend and does not emulate a physical microphone or Virtualization.framework.

```sh
curl -fL https://download.qemu.org/qemu-11.1.2.tar.xz -o "$WORK/qemu-11.1.2.tar.xz"
printf '%s  %s\n' \
  731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016 \
  "$WORK/qemu-11.1.2.tar.xz" | shasum -a 256 -c -
mkdir "$WORK/qemu-11.1.2"
tar -xf "$WORK/qemu-11.1.2.tar.xz" -C "$WORK/qemu-11.1.2" --strip-components=1
"$QEMU_BUNDLE/scripts/build-qemu.sh" \
  "$WORK/qemu-11.1.2" "$WORK/qemu-build" 2 \
  --extra-patch "$QEMU_BUNDLE/direct-reset/patches/qemu-direct-reset-cancel.patch"

mkdir -p "$WORK/evidence"
python3 "$FOLLOWUP/linked-fixture/scripts/run-linked-regression.py" \
  "$WORK/qemu-build/qemu-system-aarch64" \
  "$WORK/kernel" "$WORK/linked-fixture" \
  "$WORK/qemu-11.1.2/pc-bios"
```

The runner requires the QEMU BIOS directory explicitly and refuses to overwrite prior serial or host logs. A pass requires unlinked and linked zero-frame drain, the later-member pointer XRUN/EPIPE path, capture recovery, playback recovery, normal teardown, and no KASAN, lockdep, warning, BUG, oops, or panic diagnostics. The tested command uses TCG.

## Scope

The linked fixture checks the guest ALSA kernel behavior against the repository's synthetic QEMU backend. It does not prove behavior on stock QEMU, Virtualization.framework, an Industrial Base deployment, or a physical microphone. The source hashes and results apply to the patch stack identified above; a new kernel or QEMU build should retain its own inputs and logs outside this source package.
