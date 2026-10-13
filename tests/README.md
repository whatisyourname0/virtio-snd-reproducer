# Host tests

Run the source-extracted host suite from any current directory:

```sh
python3 /path/to/virtio-snd-reproducer/tests/run_host_tests.py \
  --kernel-tree /path/to/linux-with-v1-and-v2-committed-and-v3-applied \
  --generic-baseline-ref HEAD~6 \
  --lifetime-baseline-ref HEAD~3 \
  --ack-baseline-ref HEAD~1 \
  --nommu-baseline-ref HEAD~1 \
  --stopped-baseline-ref HEAD \
  --v1-control-baseline-ref HEAD~1 \
  --v2-control-baseline-ref HEAD \
  --output-dir /tmp/virtio-snd-reproducer-results
```

The source worktree is based on the pinned commit, has the five signed v1 patches applied, has the v2 overlay committed, and has the v3 overlay applied to the working tree.
The runner requires Python, Git, and GCC or Clang with ASan and UBSan support.
Set `CC` to select a compiler command.

`run_generic_tests.py` uses `HEAD~6` for the pre-series source by default.
The DMA and control-payload lifetime checks use `HEAD~3`, after patches 1 through 3 and before patches 4 and 5.
The stopped-lock check uses `HEAD` as its v2 baseline.
The control-payload alignment checks use `HEAD~1` for v1 and `HEAD` for v2.
The ACK and NOMMU lifetime runners use `HEAD~1` for their v1 baseline in this package workflow.
Pass `--generic-baseline-ref`, `--lifetime-baseline-ref`, `--stopped-baseline-ref`, `--v1-control-baseline-ref`, `--v2-control-baseline-ref`, `--ack-baseline-ref`, or `--nommu-baseline-ref` when a worktree uses a different commit layout.

The v3 candidate markers include the PCM, control lifetime, remove/freeze, DMA, control-payload, v1 layout, and NOMMU lifetime assertions.
The alignment runner also requires UBSan to reproduce misaligned typed request and response accesses on v2 before checking the v3 candidate.
The pre-patch baseline is expected to trigger the recorded generic assertion failures and lifetime ASan use-after-free reports.
Each runner validates those expected baseline outcomes and fails if the patched candidate does not pass.
The ACK baseline produces four expected assertion failures: an error returned by each ACK path and unchanged indirect state after each ignored send error.
The candidate checks playback and capture partial-send failures, state non-commit, fail-stop behavior, suppression of later sends, and a subsequent successful ACK after modeled cleanup and prepare.
The recovery sequence is a harness model; it does not execute production `sync_stop` or `prepare`, wait for reset callbacks, or exercise a virtio device.
Playback and capture 64-bit application-pointer rollover cases use the exact ALSA indirect-transfer helper bodies extracted from the selected source.

The pre-series baseline has no post-reset `virtsnd_ctl_msg_cleanup()` function.
The generic harness guards the candidate-only reset path and aborts if that path is accidentally requested while testing the baseline.
The guard supplies no cleanup implementation, and extracted production definitions remain byte-exact.

Generated C files and executables live in temporary storage.
Logs and optional JSON source-hash manifests live only in the requested output directory.
The runners do not edit kernel source files.
