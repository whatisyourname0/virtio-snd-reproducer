# Synthetic ARM64 QEMU lifecycle validation

This lane packages four delayed PCM-control timeout cases and one pending-RX pause/recovery case for maintainers validating a freshly built candidate kernel.
It requires no kernel test hooks.
All injected behavior lives in the test-only QEMU patch stack, and all audio data comes from synthetic `-audiodev none` responses.

The four FSM runs execute the selected PREPARE, START, STOP, or RELEASE state transition, then hold its response descriptor for 1500 ms with a QEMU virtual timer.
Each run checks the bounded timeout, zero frames delivered, an XRUN stream state, rejection before reprobe, module reprobe, and following capture recovery.
PREPARE and START require the first blocked read to return `-EPIPE`.
STOP and RELEASE accept a first `-EPIPE` or `-EIO`, then require the final read to return `-EPIPE`.
In the candidate `sound/core/pcm_lib.c`, `wait_for_avail()` checks `SNDRV_PCM_STATE_XRUN` and returns `-EPIPE` before checking whether `schedule_timeout()` expired and returning `-EIO` (around lines 2006–2028).
The stream-lock race therefore determines whether STOP or RELEASE first exposes `-EPIPE` or the transient timeout `-EIO`.
The probe retries only after `-EIO`, bounds reader wakeup to 2 seconds, and requires the stream to remain XRUN with no frames until reprobe.
Prepare without reprobe must return the driver's `-EIO` reset-required result.
The guest waits one additional second after the probe returns so the late response completes before module reprobe.

The pending-RX run holds one capture receive descriptor for 5000 ms.
It checks that STOP and RELEASE are acknowledged, the descriptor remains pending after the driver's RELEASE timeout, the descriptor completes once, and the next capture returns a full period without another RELEASE or a device reset.
The runner compares guest uptime around the held descriptor and requires completion 4 to 10 guest seconds after the hold is observed.
Both lifecycle runners compare the guest's reported `uname -r` with the supplied `--release` value.
The pending-RX runner enforces `--timeout` as the overall QEMU wall-time budget and terminates then kills its owned QEMU process if the budget expires.
The pending-RX runner starts a QEMU-owned QMP Unix socket server, disables shell echo and prompts, bounds connection attempts and reads, and requires complete newline-terminated pause-result, uptime, warning-count, and dmesg-end records.

## Source and license

`source/fsm_control_probe.c` is based on the historical `revision7-fsm-control-acceptance` probe, SHA-256 `9579aaadf80e972e83714336688cfcfd1d5f26d3eec68dcfda738b8c2329cbe6` before package changes.
The package adds SPDX metadata and an oracle for the candidate kernel's `wait_for_avail()` ordering in `sound/core/pcm_lib.c` (around lines 2006–2028).
Because the function checks XRUN before checking whether the wait expired, STOP and RELEASE accept a first `-EPIPE` or `-EIO`; all four cases require final `-EPIPE`, zero frames delivered, and state `XRUN`.
The existing timeout bound, 2-second reader wake bound, exact `-EIO` prepare rejection before reprobe, 1-second late-response grace, and following module-reprobe capture remain required.

`source/pause_rxflush_probe.c` follows the historical `snapshot3-release-rxflush` probe, original SHA-256 `1b9ddce6d92f9449aa557a2ff4f58cbded7a898ef28ece718a8d161fa538ddc1`.
The package adds SPDX metadata and closes a signal lost-wake race in the fixture by blocking SIGUSR1/SIGUSR2, waiting with `sigsuspend`, checking the return from the setup helper, then restoring the previous mask.

`source/trace.events` is copied from the historical pending-RX fixture with SHA-256 `753c4f462230166cb68081d44c04eed9dadcdd75304b96656c28741b7c62db29`.
`source/lab-init` is the test-only init script for these portable inputs.

The package and its source carry GPL-2.0-only identifiers.
The license text is in `../../GPL-2.0.txt`.
The QEMU patch stack adds test controls and reset cleanup to the synthetic backend; it is not a production QEMU interface and must not be installed in a production emulator.
The reset-cancellation result applies to that corrected fixture backend and does not prove stock-QEMU reset behavior.
For failed lifecycle setup or assertions, the test init prints `LIFECYCLE_INIT_FAIL` and powers off so the runner can preserve the failure promptly.
The pending-RX mode remains interactive after its boot-ready marker.

## Build probes and initrd

Build the guest probes in an ARM64 Alpine 3.22.6 container.
The rootfs can be an existing Alpine 3.22 ARM64 fixture or a new one made with `../scripts/bootstrap-alpine-rootfs.sh`.

```sh
BUNDLE=/absolute/path/reproducer-ready/qemu
OUT=/absolute/path/lifecycle-output
mkdir -p "$OUT/probes"
docker run --platform linux/arm64 --rm \
  -v "$BUNDLE:/bundle:ro" \
  -v "$OUT/probes:/out" \
  alpine:3.22.6 sh -c \
  'apk add --no-cache build-base alsa-lib-dev && /bundle/scripts/build-probes.sh /out'
```

The build uses `-Wall -Wextra -Werror` by default and writes four ARM64 executables.
The lifecycle packer selects `fsm_control_probe` and `pause-rxflush-probe` from that output directory.

After building the candidate kernel, provide its matching `Image`, installed module directory, release string, and the prepared Alpine rootfs:

```sh
BUNDLE=/absolute/path/reproducer-ready/qemu
ROOTFS=/absolute/path/alpine-rootfs
MODULES=/absolute/path/module-stage/lib/modules/KERNEL_RELEASE
RELEASE=KERNEL_RELEASE
PROBES=/absolute/path/lifecycle-output/probes
INITRD=/absolute/path/lifecycle-output/lifecycle-initrd.cpio.gz
python3 "$BUNDLE/scripts/pack-initrd.py" \
  --suite lifecycle \
  --rootfs "$ROOTFS" \
  --modules "$MODULES" \
  --release "$RELEASE" \
  --probe-dir "$PROBES" \
  --output "$INITRD"
```

The packer does not modify the supplied rootfs.
It records SHA-256 hashes for `snd.ko`, `snd-timer.ko`, `snd-pcm.ko`, `virtio_snd.ko`, and the initrd in the adjacent `.inputs.txt` file.
It also checks that `modules.dep` maps the full ALSA core dependency chain and refuses to overwrite existing output.

## Build the test QEMU

Extract a fresh QEMU 11.1.2 archive with SHA-256 `731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016`.
The helper verifies pristine QEMU source hashes, applies the base capture fixture patches in order, then applies the control-delay patch and the direct-reset delta needed for pending-RX QMP controls.

```sh
BUNDLE=/absolute/path/reproducer-ready/qemu
QEMU_SOURCE=/absolute/path/qemu-11.1.2
QEMU_BUILD=/absolute/path/qemu-lifecycle-build
"$BUNDLE/scripts/build-qemu.sh" "$QEMU_SOURCE" "$QEMU_BUILD" 2 \
  --extra-patch "$BUNDLE/direct-reset/patches/qemu-direct-reset-cancel.patch"
```

The helper builds `qemu-system-aarch64` and prints the ordered patch hashes.
It modifies the supplied QEMU source tree, so use a fresh extraction for each build.
The control-delay fixture executes command side effects immediately and delays only its response descriptor with `QEMU_CLOCK_VIRTUAL`.
TCG virtual-time scheduling can affect the observed timeout ordering; retain raw logs and diagnose the injection schedule if a strict assertion fails.

## Run the cases

Use the QEMU binary from the build above, the matching candidate kernel and modules, the lifecycle initrd, and the `pc-bios` directory from the same QEMU source extraction.
TCG is the default.
Each run requires a new evidence directory because scripts refuse to overwrite logs.

```sh
BUNDLE=/absolute/path/reproducer-ready/qemu
QEMU=/absolute/path/qemu-lifecycle-build/qemu-system-aarch64
QEMU_SOURCE=/absolute/path/qemu-11.1.2
IMAGE=/absolute/path/kernel-build/arch/arm64/boot/Image
MODULES=/absolute/path/module-stage/lib/modules/KERNEL_RELEASE
RELEASE=KERNEL_RELEASE
INITRD=/absolute/path/lifecycle-output/lifecycle-initrd.cpio.gz

python3 "$BUNDLE/lifecycle/scripts/run-fsm-control.py" stop \
  --qemu "$QEMU" --kernel "$IMAGE" --initrd "$INITRD" \
  --modules "$MODULES" --release "$RELEASE" \
  --bios-dir "$QEMU_SOURCE/pc-bios" \
  --output-dir /absolute/path/evidence-fsm-stop --accel tcg
```

Repeat the command for `release`, `prepare`, and `start`, using a distinct output directory for each invocation.
Run pending-RX separately:

```sh
python3 "$BUNDLE/lifecycle/scripts/run-pending-rx.py" \
  --qemu "$QEMU" --kernel "$IMAGE" --initrd "$INITRD" \
  --modules "$MODULES" --release "$RELEASE" \
  --bios-dir "$QEMU_SOURCE/pc-bios" \
  --output-dir /absolute/path/evidence-pending-rx --accel tcg
```

The FSM runner writes separate serial and QEMU host logs.
The pending-RX runner writes one combined serial/QEMU log and uses a temporary QMP socket.
Both runners clear inherited `QEMU_VIRTIO_SND_*` and `FSM_QEMU_BINARY` overrides, print QEMU, kernel, initrd, module, and firmware paths or hashes, check that the packaged module bytes match the explicit module directory, and fail on missing assertions or kernel warnings, KASAN reports, oopses, and panics.

## Evidence boundary

Passing results establish these scenarios for the exact candidate kernel, module set, QEMU fixture build, and synthetic guest.
They do not establish Virtualization.framework behavior, physical microphone capture, host audio correctness, or production acceptance.
Record the exact commands, full logs, input manifest, QEMU patch hashes, and kernel/config hashes with each result.
