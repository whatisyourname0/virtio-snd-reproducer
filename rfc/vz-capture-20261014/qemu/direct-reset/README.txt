# Direct virtio-snd control timeout and reset-cancellation lane

This source-only fixture reproduces PCM STOP and RELEASE control timeouts with an actual ALSA playback process.
It then proves that virtio-snd reset cancels the held request, permits rebind, and supports normal playback, capture, and replay.

The guest starts `speaker-test` and confirms active playback before sending SIGKILL to its process.
QEMU holds the actual STOP (`0x0105`) or RELEASE (`0x0103`) control descriptor on a 15-second `QEMU_CLOCK_VIRTUAL` timer.
The runner requires the killed process to exit with status 137 and checks guest-local `/proc/uptime` immediately before SIGKILL and after the blocked PCM call returns.
That guest-time interval must remain between 0.75 and 5 seconds.
The host clock provides hang guards and a separately reported diagnostic interval.

After the timeout, the runner checks that QEMU still owns one pending request and has completed none.
It unbinds and rebinds the guest virtio-snd driver, then requires the pending count to clear without a completion.
Recovery requires a fresh playback period, a one-second capture, successful replay, and a WAV file larger than 180,000 bytes.
The runner advances guest uptime beyond the held request's virtual timer deadline and verifies that no late completion occurred.
It scans the completed serial/QEMU log for KASAN and kernel failure diagnostics.

This lane uses an uninstrumented guest driver from the matching KASAN kernel build and a synthetic QEMU backend.
The final QEMU patch adds test-only hold properties and changes the reset callback.
Reset cancels held control and capture timers, detaches their elements, drains queued PCM descriptors, deactivates streams, and detaches queued control descriptors before VirtIO resets its queues.
Evidence applies to the kernel running against this corrected synthetic backend.
Stock-QEMU reset behavior and physical audio devices are outside this lane.

## Inputs and build

No QEMU executable, kernel image, modules, root filesystem, or initrd is stored in this directory.
The runner requires explicit paths and a runtime SHA-256 manifest.
Use QEMU 11.1.2 from the archive and verify its SHA-256 before extracting a fresh source tree.

    curl -fLO https://download.qemu.org/qemu-11.1.2.tar.xz
    printf '%s  %s\n' \
      731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016 \
      qemu-11.1.2.tar.xz | shasum -a 256 -c -
    mkdir qemu-11.1.2
    tar -xf qemu-11.1.2.tar.xz -C qemu-11.1.2 --strip-components=1

From this directory, apply the common QEMU fixture stack followed by the direct reset delta.
The shared build script verifies pristine source hashes, applies patches in order, configures `aarch64-softmmu`, and builds `qemu-system-aarch64`.
The script modifies the supplied source directory, so use a fresh extraction for each build.
See `../README.txt` for common QEMU host-toolchain prerequisites.

    DIRECT_RESET_QEMU_SOURCE=/absolute/path/qemu-11.1.2
    DIRECT_RESET_QEMU_BUILD=/absolute/path/qemu-direct-reset-build
    ../scripts/build-qemu.sh \
      "$DIRECT_RESET_QEMU_SOURCE" "$DIRECT_RESET_QEMU_BUILD" 2 \
      --extra-patch "$PWD/patches/qemu-direct-reset-cancel.patch"

The patch stack and its hashes are recorded in `provenance/SOURCE-PROVENANCE.txt`.
The extra patch follows the built-in capture-fault, capture-drain, and FSM control-delay patches.

Build a fresh ARM64 Alpine 3.22 root filesystem inside an ARM64 Alpine 3.22 environment with network access and `apk`.
The common helper installs `busybox`, `kmod`, ALSA utilities, and the libraries needed by the guest tools.
It also creates an `/etc/inittab` that invokes `/lab-init`.

    ../scripts/bootstrap-alpine-rootfs.sh /absolute/path/direct-reset-rootfs
    apk --root /absolute/path/direct-reset-rootfs info -vv \
      > /absolute/path/direct-reset-rootfs-apk-info.txt

Use the `Image`, `kernel-release.txt`, and `modules/lib/modules/<release>` directory from one fresh KASAN kernel build.
Keep the Image and modules from the same build.
Pack the initrd from those explicit inputs and the rootfs; the packer stages a copy and leaves both inputs unchanged.

    DIRECT_RESET_RELEASE=$(cat /absolute/path/kernel-build/kernel-release.txt)
    python3 scripts/pack-initrd.py \
      --rootfs /absolute/path/direct-reset-rootfs \
      --modules "/absolute/path/kernel-build/modules/lib/modules/$DIRECT_RESET_RELEASE" \
      --release "$DIRECT_RESET_RELEASE" \
      --output /absolute/path/direct-reset.cpio.gz

The packer requires `snd.ko`, `snd-timer.ko`, `snd-pcm.ko`, `virtio_snd.ko`, dependency indexes, and sound-module links in `modules.dep`.
The manifest builder records hashes for the QEMU binary, kernel Image, initrd, and all four sound modules.

    python3 scripts/build-runtime-manifest.py \
      --qemu "$DIRECT_RESET_QEMU_BUILD/qemu-system-aarch64" \
      --image /absolute/path/kernel-build/Image \
      --initrd /absolute/path/direct-reset.cpio.gz \
      --modules "/absolute/path/kernel-build/modules/lib/modules/$DIRECT_RESET_RELEASE" \
      --output /absolute/path/direct-reset-runtime.sha256

## Run

TCG is the default accelerator.
Each mode launches a fresh QEMU process; `both` runs STOP and RELEASE sequentially.
The serial logger refuses to overwrite an existing log.

    python3 scripts/run.py stop \
      --qemu "$DIRECT_RESET_QEMU_BUILD/qemu-system-aarch64" \
      --image /absolute/path/kernel-build/Image \
      --initrd /absolute/path/direct-reset.cpio.gz \
      --modules "/absolute/path/kernel-build/modules/lib/modules/$DIRECT_RESET_RELEASE" \
      --release "$DIRECT_RESET_RELEASE" \
      --manifest /absolute/path/direct-reset-runtime.sha256 \
      --logs-dir /absolute/path/direct-reset-stop-logs

Run RELEASE with the same arguments and mode `release`, or run both sequentially with mode `both`.
On macOS, `--accelerator hvf` is optional.
The runner enforces the same guest-time timeout window under either accelerator.

Run the package checks from this directory:

    uvx ruff check .
    uvx ruff format --check .
    uvx --with pytest ty check --extra-search-path . --extra-search-path support scripts/*.py support/*.py tests/*.py
    PYTHONPATH=. uvx pytest -q tests

This directory contains source and tests only.
The 2026-10-14 TCG validation run passed both STOP and RELEASE against manifested inputs.
The generated initrd, runtime manifest, and serial logs remain in the separate validation evidence directory.
This result covers the synthetic QEMU backend and guest kernel only; it does not establish stock-QEMU or physical-device behavior.
