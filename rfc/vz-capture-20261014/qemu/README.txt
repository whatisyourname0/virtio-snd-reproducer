# Synthetic ARM64 QEMU audio reproducers

This folder packages source and scripts for synthetic virtio-snd capture-drain
and PCM lifecycle tests. The current source-only runtime set has ten cases:
three short-drain cases, four delayed-control FSM cases, one pending-RX case,
and two direct STOP/RELEASE reset-cancellation cases. The short-drain lane
tests a concrete trigger: after capture START, the guest waits, calls
snd_pcm_drain() without an intervening state, status, pointer, or availability
query, then validates the returned sample count and hash. The lifecycle lane
covers delayed PREPARE, START, STOP, and RELEASE timeouts plus a pending-RX
pause and next-capture recovery.

This is guest/kernel/QEMU fixture evidence only. It does not exercise
Virtualization.framework, a physical microphone, the production audio stack,
or a real capture device.

## Files and boundaries

- patches/qemu-capture-faults.patch adds the base synthetic capture-fault
  controls to pristine QEMU 11.1.2.
- patches/qemu-capture-drain-r1.patch is an incremental patch on that base for
  delayed short-packed replies and pause-after injection. The build script
  applies both in order; do not apply the delta directly to pristine QEMU.
- patches/qemu-fsm-control-delay.patch adds test-only delayed PCM-control
  responses and command trace markers after the capture patches.
- lifecycle/README.txt documents the explicit-path lifecycle build, pack, and
  run commands. The pending-RX case also requires the ordered
  direct-reset/patches/qemu-direct-reset-cancel.patch extra patch.
- source/capture_drain_no_query_test.c and source/playback_drain_guard.c are
  test executables built for the guest ABI.
- source/lab-init is the test-only Alpine init script. It selects the capture
  action from kernel command-line parameters and performs recovery and teardown.
- configs/arm64-kasan-reference.config is a pinned historical ARM64 KASAN
  config reference. It is not represented as a configuration already validated
  against every prepared RFC source tree.
- Scripts take explicit source, build, kernel, module, rootfs, QEMU, and initrd
  paths. No QEMU executable, kernel image, module tree, or root filesystem is
  embedded here.

source/lab-init deliberately calls the capture probe without ALSA query calls
between START and drain or drop. The guest uses synthetic QEMU responses; it
does not read microphone samples.

## Prerequisites

- QEMU 11.1.2 source archive, ARM64 kernel source export, and a CPIO-capable
  ARM64 Linux guest.
- A host build toolchain supported by QEMU 11.1.2, Meson/Ninja, Python 3, and
  patch.
- ARM64 Linux kernel build dependencies and an ARM64 kernel config with KASAN,
  modules, virtio, and ALSA enabled.
- ARM64 Alpine 3.22 with apk for the guest rootfs and probe build. The
  bootstrap-alpine-rootfs.sh script installs alpine-baselayout, alpine-keys,
  alpine-release, apk-tools, busybox, kmod, alsa-utils, and alsa-lib. Build
  probes with build-probes.sh after installing build-base and alsa-lib-dev.
  Save apk info -vv output because the v3.22 repository advances.
- QEMU execution with TCG. hvf may be selected on supported ARM hosts.

## One QEMU build for the ten source-only cases

The following path builds one fresh candidate kernel, one fixture QEMU, and
three initrds, then runs all ten current cases in sequence. It records logs in
new evidence directories and stops at the first failed assertion.
The direct-reset lane supplies the final ordered QEMU patch required by both
its reset checks and the pending-RX hold controls.

Prepare the candidate source and build the ARM64 kernel with the bundled,
pinned Alpine kernel builder:

```sh
set -eu
PACKAGE=/absolute/path/reproducer-ready
BUNDLE="$PACKAGE/qemu"
WORK=/absolute/path/fresh-qemu-validation
KERNEL_GIT=/absolute/path/sound.git
mkdir -p "$WORK"
"$PACKAGE/prepare-source.sh" "$KERNEL_GIT" "$WORK/candidate-source" candidate
docker build --platform linux/arm64 \
  --file "$BUNDLE/Dockerfile.kernel-builder" \
  --tag industrial-virtio-module-builder:alpine322-gnu "$BUNDLE"
"$BUNDLE/scripts/build-kernel.sh" \
  --source "$WORK/candidate-source" --output "$WORK/kernel" --jobs 4
RELEASE=$(cat "$WORK/kernel/kernel-release.txt")
MODULES="$WORK/kernel/modules/lib/modules/$RELEASE"
IMAGE="$WORK/kernel/Image"
```

Build the test QEMU once from the pinned 11.1.2 archive, with the direct-reset
patch applied last:

```sh
curl -fL https://download.qemu.org/qemu-11.1.2.tar.xz -o "$WORK/qemu-11.1.2.tar.xz"
printf '%s  %s\n' \
  731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016 \
  "$WORK/qemu-11.1.2.tar.xz" | shasum -a 256 -c -
mkdir "$WORK/qemu-11.1.2"
tar -xf "$WORK/qemu-11.1.2.tar.xz" -C "$WORK/qemu-11.1.2" --strip-components=1
"$BUNDLE/scripts/build-qemu.sh" \
  "$WORK/qemu-11.1.2" "$WORK/qemu-build" 2 \
  --extra-patch "$BUNDLE/direct-reset/patches/qemu-direct-reset-cancel.patch"
QEMU="$WORK/qemu-build/qemu-system-aarch64"
```

Build the ARM64 guest probes and Alpine root filesystem, then pack one initrd
for short-drain, one for the lifecycle probes, and one for direct reset:

```sh
mkdir -p "$WORK/guest" "$WORK/evidence"
docker run --platform linux/arm64 --rm \
  -v "$BUNDLE:/bundle:ro" -v "$WORK/guest:/out" alpine:3.22.6 sh -c \
  'set -eu; apk add --no-cache build-base alsa-lib-dev; \
   /bundle/scripts/build-probes.sh /out/probes; \
   /bundle/scripts/bootstrap-alpine-rootfs.sh /out/rootfs; \
   apk --root /out/rootfs info -vv > /out/apk-info-vv.txt'
python3 "$BUNDLE/scripts/pack-initrd.py" --suite short-drain \
  --rootfs "$WORK/guest/rootfs" --modules "$MODULES" --release "$RELEASE" \
  --probe-dir "$WORK/guest/probes" --output "$WORK/short-drain.cpio.gz"
python3 "$BUNDLE/scripts/pack-initrd.py" --suite lifecycle \
  --rootfs "$WORK/guest/rootfs" --modules "$MODULES" --release "$RELEASE" \
  --probe-dir "$WORK/guest/probes" --output "$WORK/lifecycle.cpio.gz"
python3 "$BUNDLE/direct-reset/scripts/pack-initrd.py" \
  --rootfs "$WORK/guest/rootfs" --modules "$MODULES" --release "$RELEASE" \
  --output "$WORK/direct-reset.cpio.gz"
```

Run the ten cases sequentially under TCG, using the same QEMU binary and
candidate kernel for each lane:

```sh
for case in after0 after1 drop; do
  python3 "$BUNDLE/scripts/run-short-packed-drain.py" --case "$case" \
    --qemu "$QEMU" --kernel "$IMAGE" --initrd "$WORK/short-drain.cpio.gz" \
    --bios-dir "$WORK/qemu-11.1.2/pc-bios" --accel tcg \
    --output-dir "$WORK/evidence/short-$case"
done
for mode in prepare start stop release; do
  python3 "$BUNDLE/lifecycle/scripts/run-fsm-control.py" "$mode" \
    --qemu "$QEMU" --kernel "$IMAGE" --initrd "$WORK/lifecycle.cpio.gz" \
    --modules "$MODULES" --release "$RELEASE" \
    --bios-dir "$WORK/qemu-11.1.2/pc-bios" --accel tcg \
    --output-dir "$WORK/evidence/fsm-$mode"
done
python3 "$BUNDLE/lifecycle/scripts/run-pending-rx.py" \
  --qemu "$QEMU" --kernel "$IMAGE" --initrd "$WORK/lifecycle.cpio.gz" \
  --modules "$MODULES" --release "$RELEASE" \
  --bios-dir "$WORK/qemu-11.1.2/pc-bios" --accel tcg \
  --output-dir "$WORK/evidence/pending-rx"
python3 "$BUNDLE/direct-reset/scripts/build-runtime-manifest.py" \
  --qemu "$QEMU" --image "$IMAGE" --initrd "$WORK/direct-reset.cpio.gz" \
  --modules "$MODULES" --output "$WORK/direct-reset-runtime.sha256"
python3 "$BUNDLE/direct-reset/scripts/run.py" both \
  --qemu "$QEMU" --image "$IMAGE" --initrd "$WORK/direct-reset.cpio.gz" \
  --modules "$MODULES" --release "$RELEASE" \
  --manifest "$WORK/direct-reset-runtime.sha256" \
  --logs-dir "$WORK/evidence/direct-reset" --accelerator tcg
```

All ten commands above use ordinary TCG. The final local runs passed.
Earlier after0 runs against the pre-async QEMU build saturated the capture
ring in the follow-on arecord stage while the exact short-tail check passed.
Those failures remain in VALIDATION-RESULTS.txt; later passing runs do not
establish a permanent fix for host-load or clock/scheduling sensitivity.

For an explicit diagnostic comparison, the short-drain runner also supports
`--icount-shift 3` with TCG. This uses `-icount shift=3,sleep=off`, coupling the
synthetic none-backend audio clock to guest instruction execution and using
single-thread TCG, including with two virtual CPUs. It preserves the audio
geometry, payload hashes and fatal-overrun assertions, but does not measure
real-time performance. A passing icount run must not be reported as a passing
ordinary TCG run. Omit the option for ordinary TCG, or use `--accel hvf` on a
supported Apple Silicon host. Keep separate logs for each clock mode.

The direct-reset `both` mode runs STOP and RELEASE in separate QEMU processes,
so the sequence covers ten fresh-VM cases in total. The lane-specific sections
below retain the individual build and run details.

The source archive used for the fixture was QEMU 11.1.2
(qemu-11.1.2.tar.xz, SHA-256
731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016).
The build script checks the QEMU version and pristine hashes for both source
files before applying the patch stack.

## Build QEMU

Extract a fresh QEMU 11.1.2 source archive, verify its archive hash above, then
run from this directory:

    scripts/build-qemu.sh /absolute/path/qemu-11.1.2 /absolute/path/qemu-build 2

The script verifies the ordered capture and lifecycle patches against pristine
source, configures aarch64-softmmu, and builds qemu-system-aarch64. It modifies
the supplied QEMU source directory by applying the local test patches. Use a
fresh extraction for each build. To run pending-RX, add
`--extra-patch /absolute/path/reproducer-ready/qemu/direct-reset/patches/qemu-direct-reset-cancel.patch`
after the job count. To supply firmware explicitly when running, pass the QEMU
source pc-bios directory to `--bios-dir`.

The capture patch stack introduces test-only QEMU environment controls. The
short-drain runner sets:

    QEMU_VIRTIO_SND_CAPTURE_TEST=short-packed,after=1,count=1
    QEMU_VIRTIO_SND_CAPTURE_TEST_DELAY_MS=40
    QEMU_VIRTIO_SND_CAPTURE_TEST_PAUSE_AFTER=1

The lifecycle control runner sets `QEMU_VIRTIO_SND_CTRL_TEST` to one command,
its one-based occurrence, and a delay in milliseconds. Its current fixture
uses a 1500 ms delay. QEMU executes the command's state transition and delays
only the response descriptor with `QEMU_CLOCK_VIRTUAL`, allowing the main loop
to keep servicing queues. The guest holds reprobe for a one-second grace after
the probe returns so the late response completes first. TCG virtual-time
scheduling can affect timeout ordering, so preserve failed logs and diagnose
the fixture schedule without weakening guest assertions.

Use after=0 to inject only a short packed reply; after=1 injects a full reply,
then one delayed short reply; drop tests the no-query drop path. The timer uses
QEMU virtual time. This injection does not carry real audio.

## Build guest kernel and modules

Use reproducer-ready/prepare-source.sh to export the intended base, v3, or
candidate kernel source from the existing kernel repository. The helper and RFC
patches live in the parent reproducer-ready folder.

For a prepared source export:

    KERNEL_SRC=/absolute/path/to/prepared-kernel
    KERNEL_OUT=/absolute/path/to/kernel-build
    MODULE_STAGE=/absolute/path/to/module-stage
    mkdir -p "$KERNEL_OUT"
    cp configs/arm64-kasan-reference.config "$KERNEL_OUT/.config"
    make -C "$KERNEL_SRC" ARCH=arm64 O="$KERNEL_OUT" olddefconfig
    make -C "$KERNEL_SRC" ARCH=arm64 O="$KERNEL_OUT" -j4 Image modules
    RELEASE=$(make -s -C "$KERNEL_SRC" ARCH=arm64 O="$KERNEL_OUT" kernelrelease)
    make -C "$KERNEL_SRC" ARCH=arm64 O="$KERNEL_OUT" \
        INSTALL_MOD_PATH="$MODULE_STAGE" modules_install
    depmod -b "$MODULE_STAGE" "$RELEASE"

The config selects KASAN, loadable sound modules, devtmpfs, and an ARM64 local
version. Build in Linux: native ARM64 Linux can build directly; x86_64 Linux
needs an ARM64 cross-toolchain; macOS requires a Linux build container. For the
x86_64 cross-build, export CROSS_COMPILE=aarch64-linux-gnu- before running make.
olddefconfig may resolve options differently across source snapshots. Save the
resulting .config, kernelrelease, build logs, and hashes alongside test
evidence. Use the matching Image and modules from the same build. Do not mix
modules from an older snapshot with a newly built kernel.

## Build guest rootfs and initrd

Run these commands inside an ARM64 Alpine 3.22 environment (for example, an
ARM64 Alpine container). Bind-mount this reproducer-ready/qemu directory as
/bundle and an output directory as /out:

    apk add --no-cache build-base alsa-lib-dev
    /bundle/scripts/build-probes.sh /out/probes
    /bundle/scripts/bootstrap-alpine-rootfs.sh /out/rootfs
    apk --root /out/rootfs info -vv > /out/apk-info-vv.txt

On an ARM64 Mac with Docker, use an ARM64 Alpine 3.22 container and mount the
bundle and output paths. On another host, set CC/CFLAGS/LDFLAGS to a working
ARM64 cross-toolchain and provide an ARM64-compatible rootfs. Do not run
host-architecture probes inside an ARM64 guest.

Then, on a host with Python 3, use the module stage from the matching kernel
build:

    python3 scripts/pack-initrd.py \
        --rootfs /out/rootfs \
        --modules /absolute/path/module-stage/lib/modules/$RELEASE \
        --release "$RELEASE" \
        --probe-dir /out/probes \
        --output /out/no-query-initrd.cpio.gz

The packer refuses an existing output file and rejects module release strings
containing path separators. It copies the explicit rootfs and exact installed
module tree into a temporary staging area, overlays both probes and lab-init, checks required
sound modules, init configuration, and module dependency indexes, then emits a
gzip-compressed newc archive. It does not modify the supplied rootfs. Archive mtimes are
normalized to zero; the output is reproducible for the same rootfs and module
contents. The init script mounts devtmpfs on the empty /dev directory, so the
guest config must include CONFIG_DEVTMPFS=y.

## Run one case

    mkdir -p /absolute/path/evidence-after1
    if python3 scripts/run-short-packed-drain.py \
        --case after1 \
        --qemu /absolute/path/qemu-system-aarch64 \
        --kernel /absolute/path/kernel-build/arch/arm64/boot/Image \
        --initrd /absolute/path/no-query-initrd.cpio.gz \
        --bios-dir /absolute/path/qemu-11.1.2/pc-bios \
        --accel tcg \
        --output-dir /absolute/path/evidence-after1 \
        > /absolute/path/evidence-after1/runner.log 2>&1; then
        cat /absolute/path/evidence-after1/runner.log
    else
        result=$?
        cat /absolute/path/evidence-after1/runner.log
        exit "$result"
    fi

The runner refuses to overwrite logs. It writes a guest serial log and QEMU
host log, prints artifact hashes (the example saves them in runner.log), uses a
90-second default timeout, and fails
on missing pass markers or KASAN/BUG/WARNING/oops/panic strings.

Expected probe results:

| Case | Expected drained frames | FNV-1a64 |
| --- | ---: | --- |
| after0 | 1023 | 20d61b4edef6d535 |
| after1 | 2047 | af2de207e4d27535 |
| drop | 0 | cbf29ce484222325 |

Run after0 and drop as separate invocations with fresh output directories.
A successful case prints SHORT_PACKED_NO_QUERY_DRAIN_PASS.

## Evidence boundary

The current package defines ten source-only runtime cases: after0, after1, and
drop short-drain; PREPARE, START, STOP, and RELEASE FSM timeouts; one pending-RX
pause/recovery run; and direct STOP and RELEASE reset-cancellation runs.
Historical snapshot3 integration results remain separate evidence and do not
substitute for running these commands with freshly built inputs.
The direct-reset QEMU patch changes the synthetic backend's reset cleanup so
the runner can test cancellation and rebind recovery; it is not evidence about
stock-QEMU reset behavior.
All guest audio is synthetic and none of these cases establish
Virtualization.framework behavior, a physical microphone, or production audio
acceptance.
