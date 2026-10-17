# virtio-snd stability reproducer preparation

This local review package separates the current ACK reproducer, deterministic
capture integration checks, and the historical lifetime-series fixture.
It contains no new kernel optimization and is not an upstream test submission.
See [DEPENDENCIES.md](DEPENDENCIES.md) for the kernel patch order and provenance.
See [VALIDATION.md](VALIDATION.md) for executed checks and remaining boundaries.

## What each component proves

| Component | Scope | Boundary |
| --- | --- | --- |
| `ack/` | Source-extracted ACK routines exercised in a KASAN/lockdep kernel module | Test queue hooks do not instantiate a real virtqueue, ALSA PCM or host backend |
| `qemu/` + `oracle/` | Channel/frame/epoch data integrity and fresh-open recovery through a Linux guest | Test-only paced QEMU source, ARM64 TCG, synthetic S16_LE at 48 kHz |
| `lifetime/` | Earlier five-patch PCM/control lifetime and reset reproduction | Separate older stack, strict build manifests, ARM64 macOS HVF only |

The capture tests do not access a host microphone, Docker Desktop, Apple VZ or
MOTU hardware. A successful recovery capture does not make the deliberately
overrun capture lossless. These checks do not claim lower CPU usage.

## Requirements and input preparation

- Python 3.10 or newer. The runners and oracle use the standard library.
- For ACK checks, a matching ARM64 kernel build with KASAN/lockdep, build tools
  and the exact candidate Linux sources. See [ack/README.md](ack/README.md).
- For sequence checks, an executable `qemu-system-aarch64` containing the
  sequence fixture, an ARM64 `Image`, and a gzip/newc initramfs.
- The guest must provide virtio PCI sound, virtio console, proc/sys/dev mounts,
  a root command shell on `hvc0`, `arecord`, `sha256sum`, `wc`, `cut`, `cat`,
  `head`, `yes`, `sleep`, `kill`, and writable `/tmp`.
- Load `virtio_snd` before printing the ready marker. The default marker is
  `VZ_HIGHLOAD_GUEST_READY=1`; it can be changed with `--ready-marker`.
  A shell that echoes input is supported. The marker itself must occupy a line.

`qemu/patches/QEMU-SEQUENCE-CONSOLIDATED.patch` applies to the official QEMU
11.1.2 archive. Its clean application was checked and both resulting source
files matched the previously tested fixture byte for byte. The incremental
patches are retained for review history; do not apply them after the consolidated
patch. The build recipe verifies the archive and both resulting source hashes:

```sh
/absolute/path/to/package/qemu/build-sequence-qemu.sh \
  --work-dir /absolute/path/to/new-qemu-work \
  --output-dir /absolute/path/to/qemu-output --jobs 2
```

The builder needs `curl`, `patch`, `tar`, a C compiler, Ninja, Meson, pkg-config
and QEMU's build dependencies (including GLib and Pixman). It uses QEMU's
`configure` wrapper and disables the optional PVG backend, which this headless
fixture does not use. Build artifacts and their manifest remain outside the
package. A complete fresh QEMU and kernel build was subsequently checked; see
[FRESH-BUILD-VALIDATION.md](FRESH-BUILD-VALIDATION.md) for exact inputs and runtime results.
No binaries are distributed here. The `lifetime/` backend has a
different interface and cannot substitute for the sequence fixture binary.

Build the guest from explicit inputs using [guest/README.md](guest/README.md).
The recipe uses the prepared Linux source tree, a small config fragment, standard
`modules_install`, and a public Alpine rootfs. It does not require historical
private Docker volumes or prebuilt binary hashes. A newly built kernel is a new
candidate; rerun the checks and retain its source/config/module provenance.

## Prepare the current kernel source stack

```sh
package=/absolute/path/to/virtio-snd-upstream-reproducer
python3 "$package/provenance/prepare_kernel_stack.py" \
  --source /absolute/path/to/linux-git \
  --destination /absolute/path/to/new-prepared-tree
```

The script applies the recorded patch order and exposes
`refs/reproducer/pre_ack` and `refs/reproducer/ack_only` for ACK source extraction.
These identify Git trees, without creating author commits or requiring private
historical commit objects. Source files are not checked out by this step.
For a kernel build, populate the new repository using the explicit `read-tree`
step in `guest/README.md`. Read `DEPENDENCIES.md` before using the prepared tree.
Follow `ack/README.md` for the matching kernel build/module and guest inputs.

## Run from any working directory

Set these paths to your own checkout and artifacts. No author machine path is
required. Output directories must be new; existing results are never replaced.

```sh
package=/absolute/path/to/virtio-snd-upstream-reproducer
qemu_binary=/absolute/path/to/sequence-qemu-system-aarch64
kernel_image=/absolute/path/to/Image
guest_initrd=/absolute/path/to/initramfs.cpio.gz
results=/absolute/path/to/new-results

python3 "$package/qemu/run-sequence-hash-matrix.py" \
  --qemu "$qemu_binary" --kernel "$kernel_image" --initrd "$guest_initrd" \
  --out "$results/4ch-1cpu" --channels 4 --cpus 1 --memory-mib 512 --duration 10

python3 "$package/qemu/run-sequence-hash-matrix.py" \
  --qemu "$qemu_binary" --kernel "$kernel_image" --initrd "$guest_initrd" \
  --out "$results/8ch-1cpu" --channels 8 --cpus 1 --memory-mib 512 --duration 10

python3 "$package/qemu/run-sequence-fault-recovery.py" \
  --qemu "$qemu_binary" --kernel "$kernel_image" --initrd "$guest_initrd" \
  --out "$results/8ch-recovery" --channels 8 --cpus 1 --memory-mib 512 \
  --stall-seconds 0.25 --duration 10

python3 "$package/qemu/run-sequence-long-loaded.py" \
  --qemu "$qemu_binary" --kernel "$kernel_image" --initrd "$guest_initrd" \
  --out "$results/8ch-long" --channels 8 --cpus 1 --memory-mib 512 \
  --long-duration 70 --reopen-duration 10
```

Repeat the matrix with `--cpus 2`, and with `--channels 1` or `2` for controls.
Each matrix run includes three quiet captures, one guest CPU-load capture and
two consecutive opens. Successful runs require the full guest file length and
SHA-256 to match the independent coordinate oracle, with no sequence error,
unexpected XRUN or kernel diagnostic in the clean capture phases.
The complete serial transcript is scanned for KASAN, lockdep, BUG, WARNING,
panic and Oops diagnostics; matching lines are recorded in `kernel_diagnostics`
and fail the run.
The fault runner requires evidence of the injected stall and validates a fresh
capture after it. Inspect the complete JSON and serial log, not `arecord` exit
status alone.

All commands record the selected QEMU, kernel and initrd SHA-256 in their output.
An optional `--manifest /path/to/pins.json` rejects changed artifacts before boot:

```json
{
  "schema_version": 1,
  "sha256": {
    "qemu": "REPLACE_WITH_64_LOWERCASE_HEX_CHARACTERS",
    "kernel": "REPLACE_WITH_64_LOWERCASE_HEX_CHARACTERS",
    "initrd": "REPLACE_WITH_64_LOWERCASE_HEX_CHARACTERS"
  }
}
```

These pins authenticate selected files against your supplied values. They do
not establish source provenance on their own. Keep build manifests with results.

## Oracle controls

The oracle validates channel swaps, duplicated channels, dropped/reordered frames,
truncation and stale epochs against a known stream. Run the existing self-checks:

```sh
PYTHONPATH="$package" python3 -m oracle.cli self-test --channels 4
PYTHONPATH="$package" python3 -m oracle.cli self-test --channels 8
```

Separate opens use different epochs. The test does not claim uninterrupted
audio continuity across a close/open operation.

## Historical lifetime reproduction

Follow [lifetime/README.md](lifetime/README.md) only when reproducing that earlier
five-patch series. Its baseline commit checks, observer module and backend pins
are retained. They are unrelated to the optional sequence-runner pin format.
The newer ACK followups should use `ack/` and the current dependency manifest.

## License and publication boundary

Local test tooling uses `GPL-2.0-only`; [LICENSES/README.md](LICENSES/README.md)
lists each source file and its provenance. `COPYING` contains the full license
text. The ACK generator extracts selected definitions from caller-supplied
Linux sources and retains their source paths and copyright notices in the
generated C file.

The Linux and QEMU patch payloads remain byte-for-byte unchanged. They modify
upstream sources under their existing GPL-2.0-or-later terms and preserve the
source files' original headers and attribution. The kernel stack and QEMU
fixture manifests record source hashes; complete upstream sources and binaries
remain external. This preparation adds no DCO signature or upstream submission.
