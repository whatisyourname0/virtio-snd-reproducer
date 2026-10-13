# Host tests

Run the source-extracted host suite from any current directory:

```sh
python3 /path/to/virtio-snd-reproducer/tests/run_host_tests.py \
  --kernel-tree /path/to/linux-with-the-five-patches-and-v2-overlay-applied \
  --ack-baseline-ref HEAD \
  --nommu-baseline-ref HEAD \
  --output-dir /tmp/virtio-snd-reproducer-results
```

The runner requires Python, Git, and GCC or Clang with ASan and UBSan support.
Set `CC` to select a compiler command.

`run_generic_tests.py` compares the patched worktree with `HEAD~5` by default.
`run_dma_control_lifetime_tests.py` compares it with `HEAD~2` by default.
Pass `--baseline-ref` to either runner when the commit ancestry differs.
`run_ack_tests.py` defaults to pinned v1 commit `9aac47803de024cd66c56cc594560c69b5a8a526` for local worktrees.
For the packaged workflow, pass `--ack-baseline-ref HEAD` after applying the five signed patches and `kernel-patches/review-v2.diff`; the worktree diff is the candidate and `HEAD` remains the v1 ACK baseline.
The aggregate runner accepts `--ack-baseline-ref` to select the pre-fix source ref.
The NOMMU lifetime runner accepts `--baseline-ref`; the aggregate runner forwards `--nommu-baseline-ref`.
For the packaged workflow, set `--nommu-baseline-ref HEAD` to compare the working-tree change against v1 after the five signed patches.

The v2 candidate markers are 95 PCM assertions, 44 control lifetime assertions, 129 remove/freeze assertions, 80 DMA assertions, 232 control-payload assertions, 6 v1 layout assertions, and 525 NOMMU lifetime assertions.
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
