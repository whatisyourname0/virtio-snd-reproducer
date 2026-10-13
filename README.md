# Virtio-snd source-only reproducer

This package runs host-side ASan and UBSan regressions against source extracted from a Linux Git worktree.
It contains five signed patches and the host test harnesses, but no kernel or QEMU build, audio samples, or private lab logs.

## Requirements

- Python 3.10 or newer
- Git
- GCC or Clang with AddressSanitizer and UndefinedBehaviorSanitizer support
- A Linux source worktree based on the pinned upstream commit below

## Quick start

The five patches target `8d70e364e866bdfca81eee24d0688fac9472cfb9` from the upstream ALSA sound tree.
Create a disposable checkout at that exact commit; the commands do not follow a moving branch:

```sh
VIRTIO_SND_REPRO=/path/to/virtio-snd-reproducer
VIRTIO_SND_KERNEL="$PWD/linux-virtio-snd-reproducer"
VIRTIO_SND_UPSTREAM=https://git.kernel.org/pub/scm/linux/kernel/git/tiwai/sound.git
VIRTIO_SND_BASE=8d70e364e866bdfca81eee24d0688fac9472cfb9

git clone --filter=blob:none --no-checkout "$VIRTIO_SND_UPSTREAM" "$VIRTIO_SND_KERNEL"
git -C "$VIRTIO_SND_KERNEL" fetch --no-tags origin "$VIRTIO_SND_BASE"
git -C "$VIRTIO_SND_KERNEL" checkout --detach FETCH_HEAD
test "$(git -C "$VIRTIO_SND_KERNEL" rev-parse HEAD)" = "$VIRTIO_SND_BASE"
git -C "$VIRTIO_SND_KERNEL" am "$VIRTIO_SND_REPRO"/kernel-patches/*.patch

python3 "$VIRTIO_SND_REPRO"/tests/run_host_tests.py \
  --kernel-tree "$VIRTIO_SND_KERNEL" \
  --output-dir /tmp/virtio-snd-reproducer-results
```

`git am` creates five commits above the pinned base.
The generic regressions use `HEAD~5` as the pre-series baseline.
The DMA and control-payload lifetime checks use `HEAD~2`, after patches 1 through 3 and before patches 4 and 5.
These ancestry-based defaults work even when `git am` creates different commit hashes.
Pass `--generic-baseline-ref` and `--lifetime-baseline-ref` to override them when the commit layout differs.

Run the host suite from any current directory; generated C files and executables live in temporary storage.
Logs and optional JSON source-hash manifests go only to the supplied `--output-dir`.
The runners hash candidate source files before and after testing and fail if those files change.
Extracted kernel function and type definitions are copied byte-for-byte from the supplied worktree or selected Git baseline.

## Expected results

The pre-patch baseline is expected to fail selected generic assertions and report AddressSanitizer use-after-free errors.
The runners check these baseline failures explicitly; they are reproduced defects, not a failed run.
The patched candidate must pass every host harness, and the aggregate runner must print `HOST_REPRODUCER_PASS`.

On the validated source state, the candidate harnesses report 143 generic assertions, 68 DMA lifetime assertions, and 129 control-payload assertions, for 340 passing assertions total.
The generic marker lines are `RESULT PASS assertions=90`, `RESULT PASS assertions=44`, and `RESULT PASS assertions=9`.
The lifetime markers are `RESULT PASS assertions=68` and `RESULT PASS assertions=129`.

The host package was run from an unrelated directory on macOS arm64 with Apple Clang 21.0.0 and on Debian Linux aarch64 with GCC 14.2.0.
These are host harness results; they do not build or boot a kernel or execute a virtio transport.
See [qemu/README.md](qemu/README.md) for the separate KASAN guest source fixture and its build/run recipe.

## Source and patch provenance

`kernel-patches/series` records patch order.
The five `.patch` files preserve their original mail headers, bodies, authorship, and `Signed-off-by` lines.
`kernel-patches/SHA256SUMS` records their package hashes.

Linux source is read from the caller-supplied worktree at run time and remains governed by its upstream license.
The host test harnesses and runner scripts use GPL-2.0-only; see [COPYING](COPYING).
Additional fixture provenance is documented in [LICENSES/README.md](LICENSES/README.md).
