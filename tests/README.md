# Host tests

Run both source-extracted suites from any current directory:

```sh
python3 /path/to/virtio-snd-reproducer/tests/run_host_tests.py \
  --kernel-tree /path/to/linux-with-the-five-patches-applied \
  --output-dir /tmp/virtio-snd-reproducer-results
```

The runner requires Python, Git, and GCC or Clang with ASan and UBSan support.
Set `CC` to select a compiler command.

`run_generic_tests.py` compares the patched worktree with `HEAD~5` by default.
`run_dma_control_lifetime_tests.py` compares it with `HEAD~2` by default.
Pass `--baseline-ref` to either runner when the commit ancestry differs.

The candidate must report 143 generic assertions, 68 DMA assertions, and 129 control-payload assertions.
The pre-patch baseline is expected to trigger the recorded generic assertion failures and lifetime ASan use-after-free reports.
Each runner validates those expected baseline outcomes and fails if the patched candidate does not pass.

The pre-series baseline has no post-reset `virtsnd_ctl_msg_cleanup()` function.
The generic harness guards the candidate-only reset path and aborts if that path is accidentally requested while testing the baseline.
The guard supplies no cleanup implementation, and extracted production definitions remain byte-exact.

Generated C files and executables live in temporary storage.
Logs and optional JSON source-hash manifests live only in the requested output directory.
The runners do not edit kernel source files.
