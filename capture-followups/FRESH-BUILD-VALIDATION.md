# Fresh source build and regression — 2026-10-17

QEMU, two matching kernel variants, virtio-snd modules, the ACK fixture module and the sequence initramfs were rebuilt for this run.
Existing kernel/QEMU object files or binaries were not reused.
The existing compiler/toolchain container was reused; rebuilding the compiler is outside this test.
No production kernel patch was changed, committed or submitted.

## Build identity

| Artifact | Source / configuration | SHA-256 |
| --- | --- | --- |
| QEMU 11.1.2 | Verified official archive plus bundled consolidated fixture patch; Apple Clang on ARM64 macOS | `8f75bd949e6c4746b85e067b98addf39b2555f17e37a4c24f839deaede7c7133` |
| Sequence kernel Image | `ack_plus_highpri` tree `5d4fb3094a9f702d5e06adb90ec9055556964107` | `500199c9031225d393e0830e4f57437c5692d6d8345cc3a4aa9592eb8224bc07` |
| ACK kernel Image | `ack_only` tree `8842d0279cc0ba2d615aa39b62a98e02178275d0` | `312409a2e508dd2c47c42d41dcb5be23e8374098c229e74ee0efa222fd31272d` |
| Effective kernel config | ARM64 GCC 14.2.0, KASAN generic, LOCKDEP, PROVE_LOCKING, DEBUG_SPINLOCK | `d8c80723ab7c4307abd862192bf189008d74973d04a561304cde62d10f0e589f` |
| ACK fixture module | Built against the ACK-only kernel, matching source extraction and vermagic | `982d70bc3282556903596de926f1e5094015a7e7228fc3a81b377510c70c7848` |
| Sequence initramfs | New public Alpine 3.22 rootfs plus the new final-tree modules | `856c4555c31ea451b9d9e730b06d55472836b02f98bee2f9e109d9c69cf7be9a` |

Both kernel variants use release `7.3.0-rc6-snd-lifetime-kasan`; identify them by tree and artifact hashes, not by uname alone.
The source was extracted inside a case-sensitive Linux container and verified by Git blob bytes and modes.
The final source contained 96,058 files / 1,641,232,755 bytes.
The ACK-only source was obtained by reversing only patch 9 and checking the resulting tree against its independent expected identity.
All artifact hash manifests and module vermagic checks passed.

## Runtime results

| Check | Configuration | Result |
| --- | --- | --- |
| Sequence matrix | 4/8 channels × 1/2 vCPU, 512 MiB, 48 kHz S16_LE; 6 captures per combination | 24/24 full-length and SHA-256 matches; no unexpected XRUN |
| ACK race and refill failure | Fresh ACK-only KASAN/lockdep kernel and module, two CPUs | Four baseline/candidate cases pass; load/unload succeeds; clean guest shutdown |
| Deliberate reader stall | 8 channels, 1 vCPU, SIGSTOP/SIGCONT for 0.25 s | XRUN observed, stopped recorder exits, subsequent capture matches the oracle |
| Loaded capture and reopen | 8 channels, 1 vCPU, 70 s under guest CPU load followed by 10 s fresh capture | 53,760,000 and 7,680,000 bytes respectively; complete SHA-256 matches; no XRUN |

The baseline ACK PASS markers mean that the known baseline failures were reproduced.
They do not certify the baseline implementation as correct.
The final capture tests use the real guest ALSA/virtio-snd path and a deterministic synthetic QEMU backend.
The ACK fixture uses source-extracted routines with test queue hooks, without a real virtqueue.

The final diagnostic rules also cover `kernel BUG at` and ARM64 `Internal error: Oops -`.
All six complete sequence transcripts were rescanned with that final detector and remained clean.
The ACK guest was rerun with its updated in-guest detector and again passed with clean shutdown.

## Problems found and resolved in preparation

- macOS case-insensitive checkout collisions: compiler input is now materialized and verified inside Linux; the public recipe documents this requirement.
- ACK fixture dependency mismatch: the final high-priority patch changes a shared function, so the fixture correctly rejects that tree; ACK-only and final kernels are built separately.
- Build-time release metadata: the local build recipe now reads kernelrelease after Kbuild synchronizes generated config, before depmod and artifact export.
- Existing ACK output directory: the local build wrapper no longer pre-creates the directory that the module builder requires to be new.
- Implicit QEMU NIC: all current sequence and ACK runners now use `-nic none`, avoiding an unused ROM dependency and matching the isolated test design.
- Kernel diagnostic acceptance: sequence runners now scan the complete serial transcript and reject sanitizer, lockdep, BUG/Oops and panic diagnostics in addition to data corruption.

Original failed attempts are retained alongside successful retries.
No recorder reconnect, test weakening or production driver change was used to produce the pass results.

## License review

Local test sources now carry explicit GPL-2.0-only SPDX notices.
The ACK template retains the copyright notices and source paths of extracted Linux definitions.
The file-level inventory in [LICENSES/README.md](LICENSES/README.md) distinguishes local test code from upstream Linux/QEMU terms.
All 14 preserved patch payloads remain byte-for-byte unchanged, including their existing attribution and sign-offs.
No DCO signature was added.

## Limits

These are synthetic ARM64 QEMU TCG regressions, not a new VZ/host microphone/MOTU or Docker Desktop qualification.
The 70-second run checks a bounded load scenario, not long-term endurance or reduced CPU usage.
Intentional XRUN loses audio; the acceptance criterion is explicit detection followed by an intact new capture.
The historical lifetime fixture remains a separate older stack and was not rerun in this campaign.
QEMU carries an ad-hoc local signature, not a distribution signing identity.
The artifacts and full logs stay outside the source package; no binaries or microphone recordings are proposed for publication.
