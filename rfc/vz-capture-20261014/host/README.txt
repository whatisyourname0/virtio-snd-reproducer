Virtio-snd host-side reproducer
===============================

Purpose
-------
This package runs source-extracted host tests for the ALSA virtio-snd changes.
Each runner reads production C functions from an explicit Linux kernel source tree.
The host harness adds focused shims for allocation, queue, completion, and fault cases.

Scope
-----
A passing result supports the named source-level contracts covered by each harness.
The tests do not build or boot a kernel.
They do not model a real VZ or QEMU virtqueue, host microphone, or scheduler interleaving.
They do not establish LinuxKit, Ubuntu, Compose, production, or hardware acceptance.
Keep runtime and physical-device results as separate validation evidence.

Requirements
------------
- Python 3.10 or newer, using only the standard library.
- A Linux source directory for the candidate.
- GCC or Clang on PATH, with GNU C11, AddressSanitizer, and UndefinedBehaviorSanitizer support.
- Set CC to select a compiler; the default is cc.

Git is optional for candidate-only runs.
Baseline comparison mode accepts explicit source directories and does not require Git refs.
No Python packages or prebuilt executables are included.
The C test programs are generated in temporary directories, except the control-wait runner's requested output directory.

Candidate-only run
------------------
This lane needs only a prepared candidate source tree and checks the included host harnesses.
It does not require private baseline Git refs or separately exported baseline trees.

    KERNEL_TREE=/path/to/linux-candidate
    HOST_PACKAGE=/path/to/reproducer-ready/host
    RUN_DIR=$(mktemp -d)

    CC=clang python3 "$HOST_PACKAGE/tests/run_host_tests.py" \
      --kernel-tree "$KERNEL_TREE" \
      --candidate-only \
      --output-dir "$RUN_DIR/host"

    CC=clang python3 "$HOST_PACKAGE/tests/run_control_wait_tests.py" \
      --kernel-tree "$KERNEL_TREE" \
      --candidate-only \
      --out-dir "$RUN_DIR/control-wait"

The aggregate covers PCM fault paths, control-message teardown, DMA and external-SG lifetime,
NOMMU buffer handling, and ACK transfer/recovery.
The separate wait fixture checks STOP/RELEASE completion-wait behavior.
The aggregate prints `HOST_CANDIDATE_PASS`; the wait fixture prints `CONTROL_WAIT_CANDIDATE_PASS`.
These are candidate assertions only and do not show that an earlier revision failed.

Baseline comparison
-------------------
Run the generic STOP/RELEASE positive/negative comparison with the included pre-fix source:

    CC=clang python3 "$HOST_PACKAGE/tests/run_control_wait_tests.py" \
      --kernel-tree "$KERNEL_TREE" \
      --baseline-control-msg "$HOST_PACKAGE/source/virtio_ctl_msg.v3-base.c" \
      --out-dir "$RUN_DIR/control-wait"

The candidate must pass 108 assertions.
The bundled baseline must show the expected 14 wait-selection failures without a sanitizer error.
The baseline failures check which wait primitive the fixture selects; they do not establish scheduler behavior.

The aggregate runners also support per-suite baseline comparisons.
Each selected baseline must contain the exact pre-fix sources required by that suite at their repository-relative paths.
Use `--*-baseline-tree` with exported source trees or `--*-baseline-ref` when the candidate tree contains the relevant history.
The stopped-lock negative control and historical v1/v2 control-layout regressions are optional.
The v1/v2 checks require both historical source states.
Do not reuse one old source tree across suites unless it contains the expected pre-fix implementation for each test.

The output directory contains generated C, binaries, logs, hashes, and manifests.
Use a disposable output directory and remove it after review if generated artifacts are not needed.

Source and license
------------------
The runners and C harnesses retain their GPL-2.0-only SPDX identifiers.
The included baseline is extracted from Linux sound/virtio/virtio_ctl_msg.c and retains its GPL-2.0+ notice and OpenSynergy copyright.
The Linux kernel source tree remains the authoritative source for all candidate functions.
This package contains test scaffolding and one baseline source file, not a kernel source snapshot.
