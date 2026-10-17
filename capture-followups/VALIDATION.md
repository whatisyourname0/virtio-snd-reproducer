# Local portability validation — 2026-10-17

This report records the initial preparation steps 1–2, with no commit, upload or email.
The subsequent complete source builds and regression results are in [FRESH-BUILD-VALIDATION.md](FRESH-BUILD-VALIDATION.md).
The package was exported using `git checkout-index` to a new directory containing spaces and executed from an unrelated working directory.
This is a relocated source checkout test, not a clone from a newly published repository.

## Verified

- All nine recorded kernel patches applied to the pinned base using an isolated Git index.
- Complete pre-ACK, ACK-only and ACK-plus-highpri tree IDs match the historical tested source trees.
- ACK source extraction accepts both commits and reconstructed tree refs; generated C is byte-identical.
- The ACK kernel module was freshly built from the relocated package against explicitly supplied prepared kernel sources/build artifacts.
- Both baseline/candidate race and refill-failure checks passed in the KASAN/lockdep guest; module load/unload succeeded and kernel diagnostics remained clean.
- The relocated 8-channel matrix passed six captures: three quiet, one CPU-load, two reopen cases.
- Every clean capture contained 96,000 frames / 1,536,000 bytes at 48 kHz S16_LE, with full-file SHA-256 matching the independent oracle.
- The fault runner observed the intentionally induced XRUN and validated a subsequent clean capture against the oracle.
- A new rootfs was prepared from public Alpine 3.22 with alsa-utils and kmod; a new initramfs was built from that rootfs and explicitly supplied matching modules.
- The same six 8-channel matrix cases passed using the new public-rootfs initramfs.
- The consolidated QEMU patch applied to the verified official archive; both resulting source files match the previously tested fixture byte-for-byte.
- Original dirty reproducer files retained their before-test SHA-256 values; the kernel source checkout remained clean.

## Integration corrections

The ACK generator previously required commit objects, while the patch reconstruction utility emits tree objects.
It now records the tree identity and an optional commit identity accurately.
The fresh public-rootfs fault test also exposed interactive BusyBox job-control semantics: wait returned on SIGSTOP and recovery encountered EBUSY while the recorder still held the device.
The failed log is retained. Fault commands now run in a non-interactive shell and require explicit process-exit evidence before reopening.
The same public-rootfs initramfs passed the corrected fault/recovery test: XRUN observed, process exited, recovery file length and full SHA-256 matched.
The new guest invokes its init script through `/bin/sh`, allowing the previously tested kernel without CONFIG_BINFMT_SCRIPT to boot it.
The new-kernel config fragment still recommends normal script execution support.
Build instructions explicitly populate the reconstructed tree before compiling it.

## Inputs retained for review

- QEMU executable SHA-256: `033ec4d96ce296fdb2a0cf49790bf8847828b0d175aa41d3c8e52b132917f658`.
- Sequence kernel Image SHA-256: `da214742fd6128305ea53ea1693dd7c5f9c6cc26cb53e76e7a2da86f28b5198f`.
- New sequence initramfs SHA-256: `9fb4c945d8b683d0d54ddad3e01f0580c167b92d453195743465e1d19c68a1f7`.
- Alpine image digest: `alpine@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8`.
- Runtime: ARM64 QEMU TCG, 1 vCPU / 512 MiB for sequence tests; 2 CPUs for the ACK race fixture.

## Remaining boundaries

The existing QEMU executable, kernel Images and matching kernel build/module artifacts were reused as explicit inputs.
A completely new QEMU and kernel toolchain build was not performed in this preparation.
The ACK fixture extracts real routines but supplies mock queue/backend hooks; it is not full ALSA/virtqueue integration.
Synthetic QEMU tests do not establish VZ microphone, MOTU routing or Docker Desktop support.
Deliberate overrun loses data; only the fresh recovery capture is required to be lossless.
These short tests validate relocation and recovery, not endurance or a performance improvement.
The historical lifetime fixture is preserved separately and was not rerun here.
Local source notices and upstream patch terms are inventoried file by file in LICENSES/README.md.
Whitespace checking of new source/documents passes; preserved patch payloads contain unified-diff context whitespace and remain byte-for-byte unchanged.
