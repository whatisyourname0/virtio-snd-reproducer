# License and fixture provenance

The C host harnesses and Python runner scripts use `SPDX-License-Identifier: GPL-2.0-only` and are covered by the full GPL-2.0 text in [COPYING](../COPYING).
They exercise selected Linux kernel functions through test shims.

Production function and type definitions are not bundled in this package.
Each run extracts selected definitions from the caller-supplied Linux Git worktree or one of its commits without changing those definitions.
The generated shim translation units do not copy the complete upstream files or their headers.
The original virtio-snd files retain their `GPL-2.0+` identifiers and OpenSynergy GmbH copyright notices in that checkout.

The patch files under `kernel-patches/` preserve their original mail messages, author metadata, patch bodies, and `Signed-off-by` lines.
The changed virtio-snd files carry upstream `GPL-2.0+` identifiers (GPL version 2 or later); the patch series does not alter their licenses.

The QEMU fixture license and source revision are recorded in [qemu/README.md](../qemu/README.md).
