# ACK source fixture

This external module extracts baseline and candidate functions from explicit Git
revisions (commits or trees), including the matching ALSA `pcm_lib_apply_appl_ptr()` helper. Kernel
spinlocks, two CPU-pinned kthreads and allocated buffers exercise the race under
KASAN/lockdep. The race setup and assertions remain those of the original
fixture. Test-only queue-lock hooks and refill-error injection remain explicit.

This is a source-extracted unit fixture. It does not instantiate a virtio device,
virtqueue, ALSA PCM or host audio backend, and cannot replace full-driver tests.
The baseline PASS marker means the expected baseline failure was reproduced;
it does not mean the baseline implementation is correct.

## Inputs

- Python 3, Git, GNU make, ARM64 compiler, QEMU system-aarch64.
- A Git kernel checkout containing both prerequisite base and ACK fix revisions.
  Bare mainline lacks the staged-capture functions and is rejected. See the
  package dependency manifest before choosing refs.
- A fully built matching ARM64 kernel and output tree, including Module.symvers;
  `modules_prepare` alone is insufficient. The build must enable SMP, MODULES,
  KASAN, KASAN_GENERIC, LOCKDEP, PROVE_LOCKING and DEBUG_SPINLOCK.
- An ARM64 BusyBox rootfs with `/init -> /bin/busybox`, `/etc`, `/tmp`, `/proc`,
  `/sys`, `/dev`, `/bin/sh`, mount, echo, cat and required dynamic libraries.
  BusyBox needs init, insmod, rmmod, dmesg, grep and poweroff applets.
  The kernel needs initramfs/gzip, devtmpfs mount and PL011 console support.
  A static BusyBox rootfs also works; no particular distribution is required.

The rootfs is supplied explicitly, copied to temporary staging and never edited
in place. The initramfs packer is bundled here. No Docker images, named volumes,
network service or neighbouring validation directories are implicitly used.

## Build and run from any checkout directory

Run these on a Linux build host. Set the paths and refs to your own inputs:

```sh
./ack/build-module.sh \
  --kernel-tree "$KERNEL_TREE" --build-dir "$KERNEL_BUILD" \
  --baseline-ref "$BASELINE_REF" --candidate-ref "$CANDIDATE_REF" \
  --output-dir "$PWD/out/ack-module" \
  --arch arm64 --cross-compile aarch64-linux-gnu- --jobs 2

python3 ./ack/run-in-qemu.py \
  --qemu "$QEMU" --kernel "$KERNEL_BUILD/arch/arm64/boot/Image" \
  --kernel-config "$KERNEL_BUILD/.config" --rootfs "$ROOTFS" \
  --module "$PWD/out/ack-module/ack_fixture_module.ko" \
  --output-dir "$PWD/out/ack-qemu"
```

When the prepared kernel tree is an unpacked source archive, add
`--source-git "$KERNEL_GIT"` to supply the Git objects separately. The builder
compares all extracted candidate source file hashes against the prepared kernel
source before compilation and rejects mismatches. `--kernel-build-dir` is an
alias for `--build-dir`. Neither option creates or rebuilds the kernel itself.

On native ARM64 Linux omit `--cross-compile`. A user-managed container can mount
these explicit inputs and run the same command. Kernel image, generated headers
and Module.symvers must come from the same build. Source extraction uses Git tree
objects, not uncommitted source edits. Generated source hashes and resolved
tree IDs and optional commit IDs are written beside the module in `source-provenance.json`.

The runner requires four case markers, successful module load/unload and clean
post-unload dmesg, plus active KASAN/lockdep boot markers. A host timeout fails.
If the fully checked guest pass arrives but shutdown stalls, the runner records
host-stopped-after-guest-pass rather than claiming normal shutdown.

For extraction-only validation (no compiler or VM needed):

```sh
python3 ack/generate_fixture.py --kernel-tree "$KERNEL_TREE" \
  --baseline-ref "$BASELINE_REF" --candidate-ref "$CANDIDATE_REF" \
  --output "$PWD/out/extraction/ack_fixture_module.c"
```
