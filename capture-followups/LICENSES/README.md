# License and file provenance

`COPYING` contains the full GNU GPL version 2 text. Locally maintained test
sources listed below carry `SPDX-License-Identifier: GPL-2.0-only`. No new
person or organization copyright line was added where authorship was not
recorded.

The sequence oracle and runners descend from earlier local validation fixtures.
The bodies of `support/qmp.py` and `support/serial_log.py` match local control-trace and fastpath helpers byte-for-byte after removing the added SPDX headers.
They are small fixture-specific clients, not copied QEMU source modules.
Their notices state the package's authorized GPL-2.0-only terms.

## Local source files

| File | Provenance | License |
| --- | --- | --- |
| `ack/Makefile` | ACK kernel-module fixture | `GPL-2.0-only` |
| `ack/ack_fixture_module.c.in` | ACK fixture template; runtime-extracted Linux definitions and their upstream attribution are described below | `GPL-2.0-only` |
| `ack/build-module.sh` | ACK module build helper | `GPL-2.0-only` |
| `ack/generate_fixture.py` | ACK source extractor | `GPL-2.0-only` |
| `ack/guest-init` | ACK guest init script | `GPL-2.0-only` |
| `ack/pack-initrd.py` | ACK initramfs packer | `GPL-2.0-only` |
| `ack/run-in-qemu.py` | ACK fixture runner | `GPL-2.0-only` |
| `ack/verify_ref_equivalence.py` | ACK source-equivalence checker | `GPL-2.0-only` |
| `guest/build_initrd.py` | Sequence guest initramfs builder | `GPL-2.0-only` |
| `guest/init-sequence` | Sequence guest init script | `GPL-2.0-only` |
| `guest/kernel.fragment` | Local kernel configuration fragment | `GPL-2.0-only` |
| `guest/newc_pack.py` | Sequence guest archive packer | `GPL-2.0-only` |
| `guest/prepare_rootfs.sh` | Sequence guest rootfs preparation helper | `GPL-2.0-only` |
| `lifetime/guest/Makefile` | Lifetime observer module build file | `GPL-2.0-only` |
| `lifetime/guest/init` | Lifetime test guest init script | `GPL-2.0-only` |
| `lifetime/guest/virtio_snd_lifetime_observer.c` | Lifetime observer fixture | `GPL-2.0-only` |
| `lifetime/runtime/build_runtime_manifest.py` | Lifetime artifact manifest builder | `GPL-2.0-only` |
| `lifetime/runtime/run_pcm_info_lifetime.py` | Lifetime PCM_INFO runner | `GPL-2.0-only` |
| `lifetime/runtime/run_pcm_lifetime.py` | Lifetime PCM runner | `GPL-2.0-only` |
| `lifetime/runtime/run_reset_cancellation.py` | Lifetime reset runner | `GPL-2.0-only` |
| `lifetime/runtime/runtime_common.py` | Lifetime runtime support | `GPL-2.0-only` |
| `lifetime/scripts/build-initramfs-in-container.sh` | Lifetime initramfs build helper | `GPL-2.0-only` |
| `lifetime/scripts/build-initramfs.sh` | Lifetime initramfs builder | `GPL-2.0-only` |
| `lifetime/scripts/build-kasan-kernels.sh` | Lifetime kernel builder | `GPL-2.0-only` |
| `lifetime/scripts/build-qemu.sh` | Lifetime QEMU builder | `GPL-2.0-only` |
| `lifetime/scripts/configure-kasan.sh` | Lifetime kernel configuration helper | `GPL-2.0-only` |
| `lifetime/scripts/kernel-build-in-container.sh` | Lifetime kernel container build helper | `GPL-2.0-only` |
| `oracle/SequenceOracle.py` | Local deterministic capture oracle; derived from earlier local multichannel fixtures | `GPL-2.0-only` |
| `oracle/__init__.py` | Local oracle package initializer | `GPL-2.0-only` |
| `oracle/cli.py` | Local oracle command line | `GPL-2.0-only` |
| `provenance/prepare_kernel_stack.py` | Local patch-stack reconstruction tool | `GPL-2.0-only` |
| `qemu/build-sequence-qemu.sh` | Local QEMU fixture build recipe | `GPL-2.0-only` |
| `qemu/run-sequence-fault-recovery.py` | Local sequence fault/recovery runner, based on earlier local fixture work | `GPL-2.0-only` |
| `qemu/run-sequence-hash-matrix.py` | Local sequence capture matrix runner, based on earlier local fixture work | `GPL-2.0-only` |
| `qemu/run-sequence-long-loaded.py` | Local sequence long-capture runner, based on earlier local fixture work | `GPL-2.0-only` |
| `support/ArtifactInputs.py` | Local artifact-pin and resource validation helper | `GPL-2.0-only` |
| `support/KernelDiagnostics.py` | Shared QEMU guest kernel-diagnostic scanner | `GPL-2.0-only` |
| `support/qmp.py` | Local fixture QMP client; matches earlier local copies except for the added SPDX header | `GPL-2.0-only` |
| `support/serial_log.py` | Local fixture serial reader; matches earlier local copies except for the added SPDX header | `GPL-2.0-only` |
| `support/test_kernel_diagnostics.py` | Focused synthetic checks for kernel-diagnostic detection | `GPL-2.0-only` |

The ACK generator extracts selected functions and a structure from caller-supplied
Linux sources at build time. The checked fixture source identifies
`sound/virtio/virtio_pcm_ops.c`, `sound/virtio/virtio_pcm_msg.c` and
`sound/virtio/virtio_pcm.h` as `GPL-2.0+` sources with
`Copyright (C) 2021 OpenSynergy GmbH`. It also extracts
`pcm_lib_apply_appl_ptr()` from `sound/core/pcm_lib.c`, marked
`GPL-2.0-or-later` with the Jaroslav Kysela and Abramo Bagnara notices. The
template now carries those source paths and notices into generated C. The
source tree remains external; generated source hashes and tree IDs are recorded
beside the module.

## Preserved upstream patch payloads

These patch files are retained byte-for-byte.
The license terms in the table describe their upstream target files, not SPDX declarations attached to the patch payloads.
Applying each patch preserves the upstream source headers and existing attribution.
Kernel patch hashes are checked by `provenance/prepare_kernel_stack.py`.
QEMU archive and source hashes are recorded in `provenance/qemu-fixture.json`.

| Patch file | Target provenance and license |
| --- | --- |
| `provenance/patches/01-01-39fb327a004c966ab90f66a34a64bbdbb3ffb475.patch` | Linux `sound/virtio/virtio_pcm_msg.c` and `virtio_pcm_ops.c`, both `GPL-2.0+` |
| `provenance/patches/02-02-7c6bc344656fd839ae0d697ba18d66ac0b47827a.patch` | Linux `sound/virtio/virtio_pcm.h` and `virtio_pcm_ops.c`, both `GPL-2.0+` |
| `provenance/patches/03-03-9dd290ac3147fa7b49a475d7df8a6172778f2860.patch` | Linux `sound/virtio/virtio_card.c`, `virtio_card.h`, `virtio_ctl_msg.c`, `virtio_ctl_msg.h`, `virtio_pcm.h` and `virtio_pcm_msg.c`, all `GPL-2.0+` |
| `provenance/patches/04-04-f4c24a5e441343a690f4ac2437f181c815bc23d0.patch` | Linux `sound/virtio/virtio_pcm.h`, `virtio_pcm_msg.c` and `virtio_pcm_ops.c`, all `GPL-2.0+` |
| `provenance/patches/05-05-ab4a5b770247a65272ab7af18a75a147daf81287.patch` | Linux `sound/virtio/virtio_ctl_msg.c`, `virtio_ctl_msg.h`, `virtio_kctl.c` and `virtio_pcm_msg.c`, all `GPL-2.0+` |
| `provenance/patches/06-06-cfe3e7000e695a2fda27993f4d9acf0a637cf24e.patch` | Linux `sound/virtio/virtio_ctl_msg.c` and `virtio_ctl_msg.h`, both `GPL-2.0+` |
| `provenance/patches/07-0001-vz-capture.patch` | Linux `sound/virtio/virtio_pcm.c`, `virtio_pcm.h`, `virtio_pcm_msg.c` and `virtio_pcm_ops.c`, all `GPL-2.0+` |
| `provenance/patches/08-0001-ALSA-virtio-account-staged-capture-reads-before-RX-r.patch` | Linux `sound/virtio/virtio_pcm_ops.c`, `GPL-2.0+` |
| `provenance/patches/09-0002-ALSA-virtio-prioritize-staged-capture-period-work.patch` | Linux `sound/virtio/virtio_pcm.c`, `virtio_pcm.h`, `virtio_pcm_msg.c` and `virtio_pcm_ops.c`, all `GPL-2.0+` |
| `lifetime/kernel-patches/review-v2.diff` | Linux `sound/virtio/virtio_card.c`, `virtio_ctl_msg.c`, `virtio_ctl_msg.h`, `virtio_kctl.c`, `virtio_pcm.h`, `virtio_pcm_msg.c` and `virtio_pcm_ops.c`, all `GPL-2.0+` |
| `qemu/patches/QEMU-CHANNELS-4-8-EXTENSION.patch` | QEMU 11.1.2 `hw/audio/virtio-snd.c`; upstream file states GPL version 2 or any later version |
| `qemu/patches/QEMU-SEQUENCE-CONSOLIDATED.patch` | QEMU 11.1.2 `hw/audio/virtio-snd.c` and `include/hw/audio/virtio-snd.h`; upstream files state GPL version 2 or any later version |
| `qemu/patches/QEMU-SEQUENCE-FIXTURE.patch` | QEMU `hw/audio/virtio-snd.c` and `include/hw/audio/virtio-snd.h`; upstream files state GPL version 2 or any later version |
| `lifetime/qemu-test-backend.patch` | Earlier QEMU 11.1.2 `hw/audio/virtio-snd.c` and header; GPL-2.0-or-later with original OpenSynergy and Emmanouil Pitsidianakis notices |

The QEMU 11.1.2 sound source header carries the OpenSynergy GmbH and Emmanouil
Pitsidianakis copyright notices. Applying these patches leaves that header in
place.

The Linux and QEMU source trees and binaries are not included in this package.
Patch payload bytes remain unchanged, including any existing mail metadata,
attribution, sign-offs or DCO lines. This preparation adds no DCO signature.
No binary dependencies are redistributed.
