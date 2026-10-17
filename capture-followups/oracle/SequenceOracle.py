# SPDX-License-Identifier: GPL-2.0-only
# ruff: noqa: N999
"""Generate and verify deterministic interleaved S16_LE capture data."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


class SequenceOracle:
    """Reference implementation for the QEMU multichannel capture sequence."""

    FORMULA = "splitmix64-coordinate-s16-v1"
    DEFAULT_SEED = 0x243F6A8885A308D3
    UINT64_MASK = 0xFFFFFFFFFFFFFFFF
    UINT64_LIMIT = 1 << 64
    EPOCH_MULTIPLIER = 0x9E3779B185EBCA87
    FRAME_MULTIPLIER = 0xD6E8FEB86659FD93
    CHANNEL_MULTIPLIER = 0xA0761D6478BD642F
    SPLITMIX_MULTIPLIER_1 = 0xBF58476D1CE4E5B9
    SPLITMIX_MULTIPLIER_2 = 0x94D049BB133111EB
    SAMPLE_STRUCT = struct.Struct("<H")

    @classmethod
    def validate_parameters(
        cls,
        *,
        channels: int,
        epoch: int,
        start_frame: int,
        count: int,
        seed: int = DEFAULT_SEED,
    ) -> None:
        """Reject coordinates that cannot describe a supported capture."""
        if not 1 <= channels <= 16:
            raise ValueError("channels must be between 1 and 16")
        for name, value in (
            ("epoch", epoch),
            ("start_frame", start_frame),
            ("seed", seed),
        ):
            if not 0 <= value < cls.UINT64_LIMIT:
                raise ValueError(f"{name} must fit an unsigned 64-bit integer")
        if count <= 0:
            raise ValueError("count must be greater than zero")
        if start_frame + count > cls.UINT64_LIMIT:
            raise ValueError("start_frame + count exceeds the unsigned 64-bit range")

    @classmethod
    def sample_bits(
        cls,
        *,
        epoch: int,
        frame: int,
        channel: int,
        seed: int = DEFAULT_SEED,
    ) -> int:
        """Return the little-endian S16 sample bits for one coordinate."""
        x = (
            seed
            ^ ((epoch * cls.EPOCH_MULTIPLIER) & cls.UINT64_MASK)
            ^ ((frame * cls.FRAME_MULTIPLIER) & cls.UINT64_MASK)
            ^ ((channel * cls.CHANNEL_MULTIPLIER) & cls.UINT64_MASK)
        ) & cls.UINT64_MASK
        x ^= x >> 30
        x = (x * cls.SPLITMIX_MULTIPLIER_1) & cls.UINT64_MASK
        x ^= x >> 27
        x = (x * cls.SPLITMIX_MULTIPLIER_2) & cls.UINT64_MASK
        x ^= x >> 31
        return (x >> 48) & 0xFFFF

    @classmethod
    def iter_pcm_chunks(
        cls,
        *,
        channels: int,
        epoch: int,
        start_frame: int,
        count: int,
        seed: int = DEFAULT_SEED,
        chunk_frames: int = 4096,
    ) -> Iterator[bytes]:
        """Yield interleaved S16_LE chunks without resetting at boundaries."""
        cls.validate_parameters(
            channels=channels,
            epoch=epoch,
            start_frame=start_frame,
            count=count,
            seed=seed,
        )
        if chunk_frames <= 0:
            raise ValueError("chunk_frames must be greater than zero")

        emitted_frames = 0
        while emitted_frames < count:
            frames = min(chunk_frames, count - emitted_frames)
            chunk = bytearray(frames * channels * cls.SAMPLE_STRUCT.size)
            byte_offset = 0
            first_frame = start_frame + emitted_frames
            for frame_offset in range(frames):
                frame = first_frame + frame_offset
                for channel in range(channels):
                    cls.SAMPLE_STRUCT.pack_into(
                        chunk,
                        byte_offset,
                        cls.sample_bits(
                            epoch=epoch,
                            frame=frame,
                            channel=channel,
                            seed=seed,
                        ),
                    )
                    byte_offset += cls.SAMPLE_STRUCT.size
            yield bytes(chunk)
            emitted_frames += frames

    @classmethod
    def generate_bytes(
        cls,
        *,
        channels: int,
        epoch: int,
        start_frame: int,
        count: int,
        seed: int = DEFAULT_SEED,
    ) -> bytes:
        """Return a complete raw interleaved capture for a short fixture."""
        return b"".join(
            cls.iter_pcm_chunks(
                channels=channels,
                epoch=epoch,
                start_frame=start_frame,
                count=count,
                seed=seed,
            )
        )

    @classmethod
    def write_raw(
        cls,
        path: Path,
        *,
        channels: int,
        epoch: int,
        start_frame: int,
        count: int,
        seed: int = DEFAULT_SEED,
    ) -> int:
        """Write a headerless S16_LE fixture and return its byte length."""
        cls.validate_parameters(
            channels=channels,
            epoch=epoch,
            start_frame=start_frame,
            count=count,
            seed=seed,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with path.open("wb") as output:
            for chunk in cls.iter_pcm_chunks(
                channels=channels,
                epoch=epoch,
                start_frame=start_frame,
                count=count,
                seed=seed,
            ):
                output.write(chunk)
                size += len(chunk)
        return size

    @classmethod
    def check_raw(
        cls,
        path: Path,
        *,
        channels: int,
        epoch: int,
        start_frame: int,
        count: int,
        seed: int = DEFAULT_SEED,
    ) -> dict[str, int | str]:
        """Compare a raw capture byte-for-byte with the expected sequence.

        :raises ValueError: file size or any sample differs from expectation
        """
        cls.validate_parameters(
            channels=channels,
            epoch=epoch,
            start_frame=start_frame,
            count=count,
            seed=seed,
        )
        frame_bytes = channels * cls.SAMPLE_STRUCT.size
        expected_bytes = count * frame_bytes
        actual_bytes = path.stat().st_size
        if actual_bytes % frame_bytes:
            raise ValueError(
                f"capture ends mid-frame: bytes={actual_bytes} frame_bytes={frame_bytes}"
            )
        if actual_bytes != expected_bytes:
            actual_frames = actual_bytes // frame_bytes
            raise ValueError(
                f"frame count mismatch: expected={count} actual={actual_frames} "
                f"bytes={actual_bytes} expected_bytes={expected_bytes}"
            )

        frame_offset = 0
        with path.open("rb") as capture:
            for expected in cls.iter_pcm_chunks(
                channels=channels,
                epoch=epoch,
                start_frame=start_frame,
                count=count,
                seed=seed,
            ):
                actual = capture.read(len(expected))
                if actual != expected:
                    byte_offset = cls._first_difference(actual, expected)
                    sample_offset = byte_offset // cls.SAMPLE_STRUCT.size
                    frame_in_chunk, channel = divmod(sample_offset, channels)
                    frame = start_frame + frame_offset + frame_in_chunk
                    actual_sample = cls.SAMPLE_STRUCT.unpack_from(actual, byte_offset)[
                        0
                    ]
                    expected_sample = cls.SAMPLE_STRUCT.unpack_from(
                        expected, byte_offset
                    )[0]
                    raise ValueError(
                        "sample mismatch: "
                        f"frame={frame} channel={channel} "
                        f"expected=0x{expected_sample:04x} actual=0x{actual_sample:04x}"
                    )
                frame_offset += len(expected) // frame_bytes

        return {
            "status": "PASS",
            "formula": cls.FORMULA,
            "channels": channels,
            "epoch": epoch,
            "start_frame": start_frame,
            "count": count,
            "bytes": expected_bytes,
            "seed": f"0x{seed:016x}",
        }

    @classmethod
    def self_test(
        cls,
        *,
        channels: int = 2,
        epoch: int = 7,
        start_frame: int = 1000,
        count: int = 257,
        seed: int = DEFAULT_SEED,
    ) -> dict[str, Any]:
        """Prove the checker accepts clean data and rejects six corruptions."""
        if channels < 2:
            raise ValueError("self-test requires at least two channels")
        if count < 4:
            raise ValueError("self-test requires at least four frames")
        cls.validate_parameters(
            channels=channels,
            epoch=epoch,
            start_frame=start_frame,
            count=count,
            seed=seed,
        )

        baseline = cls.generate_bytes(
            channels=channels,
            epoch=epoch,
            start_frame=start_frame,
            count=count,
            seed=seed,
        )
        frame_bytes = channels * cls.SAMPLE_STRUCT.size
        middle = count // 2
        frame_start = middle * frame_bytes
        previous_start = (middle - 1) * frame_bytes
        next_start = (middle + 1) * frame_bytes

        dropped = baseline[:frame_start] + baseline[frame_start + frame_bytes :]
        duplicated = (
            baseline[:frame_start]
            + baseline[previous_start : previous_start + frame_bytes]
            + baseline[frame_start + frame_bytes :]
        )
        reordered = (
            baseline[:frame_start]
            + baseline[next_start : next_start + frame_bytes]
            + baseline[frame_start : frame_start + frame_bytes]
            + baseline[next_start + frame_bytes :]
        )
        stale_epoch = epoch - 1 if epoch else epoch + 1
        stale = cls.generate_bytes(
            channels=channels,
            epoch=stale_epoch,
            start_frame=start_frame,
            count=count,
            seed=seed,
        )
        partial_frame = baseline[:-1]

        controls: dict[str, bytes] = {
            "drop_frame": dropped,
            "duplicate_frame": duplicated,
            "reorder_adjacent_frames": reordered,
            "stale_epoch": stale,
            "truncate_mid_frame": partial_frame,
        }
        for channel in range(channels):
            controls[f"drop_channel_{channel}"] = cls._drop_channel(
                baseline, channels, channel
            )
            for other_channel in range(channel + 1, channels):
                controls[f"swap_channel_{channel}_{other_channel}"] = (
                    cls._swap_channels(
                        baseline, channels, left=channel, right=other_channel
                    )
                )
            for source_channel in range(channels):
                if source_channel != channel:
                    controls[f"duplicate_channel_{source_channel}_into_{channel}"] = (
                        cls._replace_channel(
                            baseline, channels, source=source_channel, target=channel
                        )
                    )
        results: dict[str, dict[str, bool | str]] = {}
        with TemporaryDirectory(prefix="virtio-snd-oracle-") as directory:
            root = Path(directory)
            positive_path = root / "positive.raw"
            positive_path.write_bytes(baseline)
            cls.check_raw(
                positive_path,
                channels=channels,
                epoch=epoch,
                start_frame=start_frame,
                count=count,
                seed=seed,
            )
            results["positive"] = {"accepted": True}

            for name, mutation in controls.items():
                candidate = root / f"{name}.raw"
                candidate.write_bytes(mutation)
                try:
                    cls.check_raw(
                        candidate,
                        channels=channels,
                        epoch=epoch,
                        start_frame=start_frame,
                        count=count,
                        seed=seed,
                    )
                except ValueError as error:
                    results[name] = {"rejected": True, "reason": str(error)}
                else:
                    raise AssertionError(f"negative control was accepted: {name}")

        return {
            "status": "PASS",
            "formula": cls.FORMULA,
            "channels": channels,
            "epoch": epoch,
            "start_frame": start_frame,
            "count": count,
            "seed": f"0x{seed:016x}",
            "controls": results,
        }

    @classmethod
    def _first_difference(cls, actual: bytes, expected: bytes) -> int:
        for offset, (actual_byte, expected_byte) in enumerate(zip(actual, expected)):
            if actual_byte != expected_byte:
                return offset - (offset % cls.SAMPLE_STRUCT.size)
        return min(len(actual), len(expected))

    @classmethod
    def _swap_channels(
        cls, data: bytes, channels: int, *, left: int, right: int
    ) -> bytes:
        """Swap two interleaved channel positions in every frame."""
        if not (0 <= left < channels and 0 <= right < channels):
            raise ValueError("channel index is outside the interleaved frame")
        frame_bytes = channels * cls.SAMPLE_STRUCT.size
        swapped = bytearray(data)
        for frame_start in range(0, len(data), frame_bytes):
            left_start = frame_start + left * cls.SAMPLE_STRUCT.size
            right_start = frame_start + right * cls.SAMPLE_STRUCT.size
            left_sample = data[left_start : left_start + cls.SAMPLE_STRUCT.size]
            right_sample = data[right_start : right_start + cls.SAMPLE_STRUCT.size]
            swapped[left_start : left_start + cls.SAMPLE_STRUCT.size] = right_sample
            swapped[right_start : right_start + cls.SAMPLE_STRUCT.size] = left_sample
        return bytes(swapped)

    @classmethod
    def _replace_channel(
        cls, data: bytes, channels: int, *, source: int, target: int
    ) -> bytes:
        """Copy one channel's samples over another channel in every frame."""
        if not (0 <= source < channels and 0 <= target < channels):
            raise ValueError("channel index is outside the interleaved frame")
        frame_bytes = channels * cls.SAMPLE_STRUCT.size
        replaced = bytearray(data)
        sample_bytes = cls.SAMPLE_STRUCT.size
        for frame_start in range(0, len(data), frame_bytes):
            source_start = frame_start + source * sample_bytes
            target_start = frame_start + target * sample_bytes
            replaced[target_start : target_start + sample_bytes] = data[
                source_start : source_start + sample_bytes
            ]
        return bytes(replaced)

    @classmethod
    def _drop_channel(cls, data: bytes, channels: int, target: int) -> bytes:
        """Remove one interleaved sample from every frame."""
        if not 0 <= target < channels:
            raise ValueError("channel index is outside the interleaved frame")
        frame_bytes = channels * cls.SAMPLE_STRUCT.size
        sample_bytes = cls.SAMPLE_STRUCT.size
        return b"".join(
            data[frame_start : frame_start + target * sample_bytes]
            + data[
                frame_start + (target + 1) * sample_bytes : frame_start + frame_bytes
            ]
            for frame_start in range(0, len(data), frame_bytes)
        )
