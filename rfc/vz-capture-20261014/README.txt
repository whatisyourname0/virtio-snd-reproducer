virtio-snd follow-up RFC reproducer bundle

LOCAL REVIEW CANDIDATE. Nothing in this bundle sends mail or uploads files.
The code under test is experimental. A passing extracted-source test is not
proof of kernel concurrency or physical microphone correctness.

Source lineage
Original kernel base: sound.git for-next
8d70e364e866bdfca81eee24d0688fac9472cfb9
Submitted generic v3 tip: d58a3ac9b1719fdf8ce9d286be606128aeebf1aa
Previous cover: https://lore.kernel.org/all/cover.1791881787.git.mynameisjune111@gmail.com/
The five already-submitted patches under prerequisites/ are included ONLY
to reproduce that baseline, not to resend as part of these RFCs.
The four new implementation patches under rfc/ follow the v3 base in this
order: generic-control-wait, core-capture-drain 1/2 and 2/2, vz-capture-compat.
Their cover letters state the behavioral changes, review questions and limits.
The generic and core RFCs are independent; VZ depends on both.

Prepare an exact source tree
Prerequisites: Git, POSIX shell, tar, and an existing kernel Git repository
containing the original base commit (e.g. sound.git cloned from
https://git.kernel.org/pub/scm/linux/kernel/git/tiwai/sound.git).
No script fetches a repository or installs packages implicitly.

  ./prepare-source.sh /path/to/sound.git /new/path/candidate candidate
  ./prepare-source.sh /path/to/sound.git /new/path/v3-baseline v3
  ./prepare-source.sh /path/to/sound.git /new/path/original-base base

Each output directory must not exist. The script exports a full source tree,
initializes an empty local Git repository for patch application, and applies
mail diffs without commits or signoffs. Do not expect local commit IDs such
as d58a3ac9b171 to resolve inside the exported output. Supply explicit paths
to test scripts. Your source Git checkout/index/branches remain untouched.
Full kernel export needs several GB of free space per variant.

Choose the lane
- host/README.txt: source-extracted ASan/UBSan regression runners and baseline
  comparisons. No VM, network, microphone or root permissions required.
- qemu/README.txt: complete fresh kernel/module build and ten synthetic ARM64
  scenarios: short drain, delayed control responses, pending-RX recovery, and
  direct STOP/RELEASE timeout plus reset cancellation. Per-case pass/failure
  evidence and accelerator limitations are in qemu/VALIDATION-RESULTS.txt.
  The QEMU fixture includes reset cleanup changes; it is test-only and must
  not be installed as a production backend.
- vz/README.txt: actual microphone prerequisites and acceptance recipe.
  This bundle does not redistribute the private Industrial Base recorder or
  promise a turnkey signed macOS VZ launcher. Generic arecord smoke is a
  distinct test, not a replay of the original application.

Before sharing this bundle
Implementation patches still use draft authorship and have no human DCO.
The prerequisites retain their existing signed metadata. Do not infer those
signoffs certify the four new RFC patches. Preserve Assisted-by disclosure.
The final local archive hash identifies the source-only bundle. Recheck it
before a later authorized publication. No public URL exists for this bundle
until upload is separately authorized; do not substitute the older v3
reproducer URL for these new tests.

Licensing
Kernel-derived material follows the upstream GPL licensing retained in its
source and patch context. Per-lane license/provenance notes describe test
sources. No prebuilt guest rootfs, module, QEMU executable or microphone
recording is included. Obtain dependencies from their upstream projects.
