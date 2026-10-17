# Source and prerequisite contract

This bundle supports existing RFCs and records the historical tested stack.
It does not assert that pending changes are merged, and does not send mail.
All copied patches retain their existing attribution and signatures unchanged.
The kernel changes are subject to the upstream Linux source licensing; this bundle adds no new DCO certification.

## Linux stack

Start from sound.git commit `5618f6837ef3314e2c581d4c05ec13b7ed975cb4`.
That tree already contains core capture-drain commits `1f21537b11863e74d752cab3edbb78cc6efe92fc` and `d9f52113f263498f000cecce5c4a82b7d2abf9d2`.
Do not apply the older core RFC v3 patches again.

The authoritative application order and SHA-256/stable patch IDs are in `provenance/kernel-stack.json`.
Use a clean disposable checkout; apply the listed patches with `git apply --index` in numerical order.
The first six prerequisite files are preserved patch bodies rather than complete mail envelopes, so the uniform operation is `git apply`, not `git am`.
This operation creates no commits and adds no signatures.

| Order | Dependency | Public thread |
| --- | --- | --- |
| 1–5 | virtio-snd lifetime/error recovery v3 | [cover](https://lore.kernel.org/all/cover.1791881787.git.mynameisjune111@gmail.com/) |
| 6 | generic teardown/control wait RFC v2 | [cover](https://lore.kernel.org/all/rfc-v2-generic-control-wait-0.2eddd58a796e497f@gmail.com/) |
| 7 | VZ staged capture RFC v2 | [patch](https://lore.kernel.org/all/rfc-v2-vz-capture-compat-1.b9ab4c1540dc4691@gmail.com/) |
| 8 | staged ACK accounting follow-up | [patch](https://lore.kernel.org/all/rfc-virtio-snd-capture-followups-1.f2737c95c12e45b4@gmail.com/) |
| 9 | staged capture high-priority period work | [patch](https://lore.kernel.org/all/rfc-virtio-snd-capture-followups-2.f2737c95c12e45b4@gmail.com/) |

For the ACK comparison, compare orders 1–7 with orders 1–8.
For the scheduling comparison, compare orders 1–8 with orders 1–9.
The VZ staged-capture prerequisite is essential; these follow-ups are not standalone fixes for an unmodified upstream driver.
Control-trace instrumentation and input-route recovery are outside this stack.

## Submission status

The ACK/scheduling RFC cover and both patches have recorded Gmail SMTP 250 acceptance on 2026-10-17 at 06:00 UTC.
`provenance/send-receipts.json` records exact Message-IDs, subjects, message hashes and delivery outcomes.
`provenance/archive-status.json` records the same-day Lore check, which returned a challenge/non-mail response.
SMTP acceptance does not prove list delivery, public indexing or maintainer acceptance.
Those archive results are historical, not a fresh review check.
Earlier local manifests saying “submitted: false” predate the delivery receipts.
No duplicate submission is required merely to publish these tests.

## Test-to-stack mapping

| Test family | Source requirement | What it establishes |
| --- | --- | --- |
| ACK source fixture | Matched pre-ACK and ACK source functions, matching ALSA appl_ptr helper | Reproduced stale capacity and post-accounting rollback; fixture refill/work helpers are test glue |
| Kernel ACK fixture | Same extracted logic under a matching KASAN/lockdep kernel, two CPUs | Real locking and memory checks; not a full virtqueue/PCM integration test |
| STOP/RELEASE/delayed-control/reset | Matching Linux stack plus fault-enabled QEMU fixture | Full-driver request lifetime, cancellation and subsequent reopen |
| Sequence capture 1/2/4/8 channels | Sequence-enabled QEMU fixture, matching kernel/initrd | Per-frame/channel/epoch byte integrity and fresh capture after deliberate overrun |
| VZ high-load comparison | ACK-only versus ACK+highpri, matching guest and actual host input | Scheduling/recovery evidence for the measured physical route; not deterministic bit-exact source data |

## QEMU fixture dependency

The recorded QEMU fixture is version 11.1.2.
The historical incremental sequence and channel patches extend an earlier fault/capture backend.
For a clean build, use `qemu/patches/QEMU-SEQUENCE-CONSOLIDATED.patch` with the official QEMU 11.1.2 archive.
The archive hash, clean patch application and byte-for-byte equality of both resulting source files were verified.
Do not apply the incremental patches after the consolidated patch.
`qemu/build-sequence-qemu.sh` implements that clean build recipe and records the resulting binary provenance.
`provenance/qemu-fixture.json` pins the archive, patch, final source bytes and historical guest artifact hashes.
Source reconstruction verification does not itself constitute a new complete binary build.
The fixture advertises up to eight capture channels only in its opt-in sequence mode.
Playback remains limited to two channels, channel maps are absent, and data is synthetic over the paced none backend.
This is not evidence that Apple VZ, Docker Desktop or a physical MOTU interface exposes eight channels.

## Evidence boundaries

Historical multichannel regression used 1/2 vCPU, 512 MiB, 48 kHz S16_LE, 1024-frame periods and a 4096-frame buffer.
It did not inject memory pressure or enable KASAN/lockdep in that campaign.
Deliberate reader stalls produce XRUN/loss; acceptance requires the following fresh capture to match the independent oracle.
An arecord zero exit code alone is not a losslessness criterion.
The historical 70-second capture is not an endurance guarantee.
No raw microphone recordings or credentials are needed in this package.

## Reconstruct source references without private commits

Run from the package root with a source repository containing the pinned sound.git base:

```sh
python3 provenance/prepare_kernel_stack.py \
  --source https://git.kernel.org/pub/scm/linux/kernel/git/tiwai/sound.git \
  --destination /tmp/virtio-snd-source
```

A local Git repository may replace the URL for offline use.
The destination must not already exist.
The script verifies every patch SHA-256 before cloning, applies each patch to a private index, and writes three trees.
It creates `refs/reproducer/pre_ack`, `refs/reproducer/ack_only` and `refs/reproducer/ack_plus_highpri` pointing directly to tree objects.
No commits or DCO signatures are created, and the source repository is not modified.
The cloned working tree initially stays empty; the ACK generator reads `git show REF:path` directly.
For a kernel build, populate only this new unused repository with `git read-tree --reset -u refs/reproducer/ack_plus_highpri`, as described in [guest/README.md](guest/README.md).
That operation updates the index and working files without creating a commit; it must not run in a repository containing other work.
Use `/tmp/virtio-snd-source` as its source Git repository and `refs/reproducer/pre_ack` / `refs/reproducer/ack_only` as baseline/candidate references.
The exact CLI option names are documented with the ACK fixture.

`provenance/kernel-stack-verified.json` records a successful reconstruction from the base and all nine patches.
All three complete tree IDs matched the historical prerequisite, ACK and high-priority commits, not only the two files sampled in the report.
This confirms source reconstruction; it does not constitute a new kernel build or runtime test.
