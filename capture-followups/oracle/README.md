# S16_LE capture oracle

`SequenceOracle` generates the expected interleaved S16_LE sample stream from the agreed QEMU test formula.
The stream is keyed by a fixed seed, PCM epoch, global frame index, and zero-based channel index.
The frame index advances across descriptors and remains monotonic across reopen; the epoch changes on each successful PCM start.

```text
key = seed ^ (epoch * 0x9E3779B185EBCA87)
          ^ (frame * 0xD6E8FEB86659FD93)
          ^ (channel * 0xA0761D6478BD642F)  (modulo 2^64)
```

Apply the SplitMix64 finalizer with multipliers `0xBF58476D1CE4E5B9` and `0x94D049BB133111EB`.
Emit the high 16 bits as a little-endian S16 sample.
The default seed is `0x243F6A8885A308D3`.

From the validation directory, create a small known-good raw fixture:

```sh
python3 -m oracle.cli generate /tmp/capture.raw --channels 2 --epoch 1 --start-frame 0 --count 4096
```

Check a capture using the recorded start metadata:

```sh
python3 -m oracle.cli check /path/to/capture.raw --channels 2 --epoch 1 --start-frame 0 --count 4096
```

Run the positive and six negative controls:

```sh
python3 -m oracle.cli self-test
```

The checker accepts raw PCM only. A WAV header, incomplete frame, incorrect length, mismatched sample, channel swap, or wrong epoch fails.
Passing proves byte-level agreement with this synthetic stream for the supplied alignment and metadata.
It does not prove VZ/MOTU routing or field audio behavior.
