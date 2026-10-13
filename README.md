# Virtio-snd source-only reproducer

This package runs host-side ASan and UBSan regressions against source extracted from a Linux Git worktree.
It contains five signed v1 patches, separate v2 and v3 review overlays, and the host test harnesses, but no kernel or QEMU build, audio samples, or private lab logs.

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
git -C "$VIRTIO_SND_KERNEL" -c user.name='Reproducer' \
  -c user.email='reproducer@example.invalid' \
  am "$VIRTIO_SND_REPRO"/kernel-patches/*.patch
git -C "$VIRTIO_SND_KERNEL" apply "$VIRTIO_SND_REPRO"/kernel-patches/review-v2.diff
git -C "$VIRTIO_SND_KERNEL" add sound/virtio
git -C "$VIRTIO_SND_KERNEL" -c user.name='Reproducer' \
  -c user.email='reproducer@example.invalid' \
  commit -m 'virtio-snd: apply v2 review overlay'
git -C "$VIRTIO_SND_KERNEL" apply "$VIRTIO_SND_REPRO"/kernel-patches/review-v3.diff

python3 "$VIRTIO_SND_REPRO"/tests/run_host_tests.py \
  --kernel-tree "$VIRTIO_SND_KERNEL" \
  --generic-baseline-ref HEAD~6 \
  --lifetime-baseline-ref HEAD~3 \
  --ack-baseline-ref HEAD~1 \
  --nommu-baseline-ref HEAD~1 \
  --stopped-baseline-ref HEAD \
  --v1-control-baseline-ref HEAD~1 \
  --v2-control-baseline-ref HEAD \
  --output-dir /tmp/virtio-snd-reproducer-results
```

The local v2 commit lets the suite name both review baselines without relying on machine-specific commit IDs.
With that commit layout, `HEAD~6` is the pinned pre-series base, `HEAD~3` is after patches 1 through 3, and `HEAD~1` is the v1 source.
`HEAD` is the v2 source used by the stopped-lock and alignment regressions; the v3 overlay remains an uncommitted working-tree change.
The baseline refs are explicit in the command and can be changed for another commit layout.

Run the host suite from any current directory; generated C files and executables live in temporary storage.
Logs and optional JSON source-hash manifests go only to the supplied `--output-dir`.
The runners hash candidate source files before and after testing and fail if those files change.
Extracted kernel function and type definitions are copied byte-for-byte from the supplied worktree or selected Git baseline.

## Expected results

The pre-patch baseline is expected to fail selected generic assertions and report AddressSanitizer use-after-free errors.
The runners check these baseline failures explicitly; they are reproduced defects, not a failed run.
The patched candidate must pass every host harness, and the aggregate runner must print `HOST_REPRODUCER_PASS`.
The ACK baseline is expected to produce four failure assertions across playback and capture; the candidate checks that send errors fail-stop without committing indirect state, then accepts another ACK after modeled cleanup and prepare.

The v3 candidate must pass the PCM, control lifetime, remove/freeze, DMA, control-payload, v1 layout, and NOMMU lifetime suites.
The control-payload suite confirms the v1 cacheline-layout regression and reproduces the v2 typed-access alignment UBSan failures before requiring the v3 candidate to pass.
The generic suite reproduces the v2 stopped-lock audit failure before requiring the v3 candidate to pass.
The ACK candidate also passes its playback, capture, fail-stop, and recovery checks after the baseline reproduces four expected failures.
The ACK harness also checks callback suppression after the first error and 64-bit appl_ptr rollover using the exact extracted ALSA indirect-transfer helpers.
Its RELEASE/prepare recovery sequence is modeled by the harness; it does not execute the production ALSA sync_stop/prepare callbacks or a virtio device reset.

The v3 host suite passed on macOS arm64 with Apple Clang 21.0.0 and printed `HOST_REPRODUCER_PASS`.
This is a source-extracted host result; it does not build or boot a kernel or execute a virtio transport.
See [qemu/README.md](qemu/README.md) for the separate KASAN guest source fixture and its build/run recipe.

## Source and patch provenance

`kernel-patches/series` records patch order.
The five `.patch` files preserve their original mail headers, bodies, authorship, and `Signed-off-by` lines.
`kernel-patches/review-v2.diff` is a separate review overlay applied after those five patches; commit it in the disposable source tree before applying `review-v3.diff`.
`kernel-patches/review-v3.diff` is the candidate overlay tested by the host suite; neither review overlay is part of the signed v1 mail series.
`kernel-patches/SHA256SUMS` records the five v1 mail patch hashes.
`kernel-patches/review-v2.sha256` records the separate review overlay hash.
`kernel-patches/review-v3.sha256` records the v3 overlay hash.

Linux source is read from the caller-supplied worktree at run time and remains governed by its upstream license.
The host test harnesses and runner scripts use GPL-2.0-only; see [COPYING](COPYING).
Additional fixture provenance is documented in [LICENSES/README.md](LICENSES/README.md).
