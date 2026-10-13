# QEMU and ARM64 KASAN runtime reproduction

This component builds the test-only QEMU backend, ARM64 KASAN guests, and
runtime inputs from source. It does not include QEMU, kernel, module, initramfs,
or serial-log binaries.

## Prerequisites

- An ARM64 macOS host with Hypervisor.framework (HVF) support and an ARM64
  Docker engine.
- `curl`, `git`, `gzip`, `patch`, `python3`, and `tar` on the host.
- QEMU build tools and libraries. On macOS, install `meson`, `ninja`,
  `pkg-config`, `glib`, `pixman`, `gnutls`, `libslirp`, `libusb`, and
  `capstone` with the host package manager.
- A Linux kernel Git checkout containing the five patch commits, with the
  supplied series base `8d70e364e866bdfca81eee24d0688fac9472cfb9` at
  `HEAD~5`.

The KASAN builder creates disposable ARM64 Debian containers and compiles a
clean `git archive` of each selected commit. It does not use Docker volumes or
the caller's build tree. An ARM64 Docker engine avoids cross-architecture
emulation for this long kernel build.

## Build from source

Use separate work and output directories. Example paths are relative to the
reproducer package root:

```sh
out="$PWD/build"
mkdir -p "$out"

qemu/scripts/build-qemu.sh \
  --work-dir "$out/qemu-work" \
  --output-dir "$out/qemu" \
  --jobs 2

qemu/scripts/build-kasan-kernels.sh \
  --kernel-tree /path/to/linux-with-five-patch-series \
  --output-dir "$out/kernels" \
  --jobs 2

qemu/scripts/build-initramfs.sh \
  --kernel-output-dir "$out/kernels"

python3 qemu/runtime/build_runtime_manifest.py \
  --qemu-build "$out/qemu/qemu-build.json" \
  --baseline-build "$out/kernels/baseline/kernel-build.json" \
  --candidate-build "$out/kernels/candidate/kernel-build.json" \
  --baseline-initrd "$out/kernels/baseline/initramfs.cpio.gz" \
  --candidate-initrd "$out/kernels/candidate/initramfs.cpio.gz" \
  --output "$out/runtime-inputs.json"
```

The baseline defaults to `HEAD~2`, which includes the base and patches 1–3.
The candidate defaults to `HEAD`, which includes all five patches. The builder
checks the original base hash and the two-commit distance between those refs.
It records each resolved source commit and hashes the resulting image, driver,
observer, module tree, and initramfs. QEMU source and patch hashes are recorded
with the newly built QEMU binary; the binary hash is never pinned in this
source package.

`build-qemu.sh` downloads the official QEMU 11.1.2 source archive and verifies
its SHA-256 before applying `qemu-test-backend.patch`. It invokes QEMU's
`configure` wrapper with `--target-list=aarch64-softmmu -Dpvg=disabled`, then
builds `qemu-system-aarch64` with Ninja. The `pvg` option is disabled because
it is not used by this headless test and may fail to link on macOS.

## Run the runtime checks

The validators resolve inputs from the generated manifest. Optional path flags
can point to relocated copies, provided their hashes still match the manifest.
Each run writes its full serial log under `qemu-runtime-logs/` by default.

```sh
python3 qemu/runtime/run_pcm_lifetime.py baseline \
  --manifest "$out/runtime-inputs.json" --accel hvf
python3 qemu/runtime/run_pcm_lifetime.py candidate \
  --manifest "$out/runtime-inputs.json" --accel hvf
python3 qemu/runtime/run_pcm_info_lifetime.py baseline \
  --manifest "$out/runtime-inputs.json" --accel hvf
python3 qemu/runtime/run_pcm_info_lifetime.py candidate \
  --manifest "$out/runtime-inputs.json" --accel hvf
python3 qemu/runtime/run_reset_cancellation.py \
  --manifest "$out/runtime-inputs.json" --accel hvf
```

To override artifact locations, pass `--qemu`, `--image`, `--module`,
`--observer`, or `--initrd`. Pass `--log-dir` and `--qmp-dir` to select other
runtime output locations. The checks verify the source provenance, every boot
input's SHA-256, the exact driver and observer embedded in the initramfs, QMP
fault-injection properties, guest page/refcount/canary evidence, expected
baseline KASAN reports, absence of candidate diagnostics, and post-fault
playback/capture recovery.

Only `--accel hvf` is enabled because HVF was used for the observed host runtime.
KVM and TCG are not runtime-validated by this component. This guest uses
emulated audio with `-audiodev none`; it does not test audible output, a
physical microphone, CoreAudio interaction, or field hardware.

## Validation boundary

The candidate PCM, PCM_INFO control, and reset runtime validators were
smoke-tested using the manifest generator and existing local lab artifacts,
marked `reused_existing_artifacts`; those artifacts were not rebuilt from this
package. QEMU 11.1.2 patch application and its `configure` command were tested
against a fresh upstream source extraction. The initramfs recipe was also run
with existing candidate modules, and the resulting new initramfs passed the
PCM boot, fault-injection, and recovery checks. The complete QEMU, Linux kernel,
observer module, and initramfs source rebuild recipe has not been run end to
end from this package.

## License and source provenance

The guest observer, guest init script, Makefile, build scripts, and Python
validators are package fixtures marked `SPDX-License-Identifier: GPL-2.0-only`;
the full license text is in [COPYING](../COPYING).

`qemu-test-backend.patch` is a test-only change to QEMU 11.1.2's
`hw/audio/virtio-snd.c` and `include/hw/audio/virtio-snd.h`. The upstream files
state GNU GPL version 2 or (at the option of the user) any later version; their
license headers and existing Emmanouil Pitsidianakis and OpenSynergy GmbH
copyright notices remain unchanged in the patched source. The test backend
changes follow those existing GPL-2.0-or-later terms. The patch applies to the
QEMU source archive at <https://download.qemu.org/qemu-11.1.2.tar.xz>, whose
SHA-256 is `731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016`.
The bundled test patch SHA-256 is
`c35bfd4abf39e15cffdb42874fd6a036912f1a34924ef8f574c334a3362e7023`.
