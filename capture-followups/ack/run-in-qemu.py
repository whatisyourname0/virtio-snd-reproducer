#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Boot the ACK fixture module in a bounded two-vCPU ARM64 KASAN guest."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMMON_PACKER = HERE / "pack-initrd.py"
GUEST_INIT = HERE / "guest-init"

DIAGNOSTICS = re.compile(
    r"BUG:|kernel BUG at\b|KASAN:|WARNING:|Kernel panic|Oops:|"
    r"Internal error:\s+Oops -|possible circular locking dependency|"
    r"inconsistent lock state|scheduling while atomic|possible recursive locking|"
    r"DEBUG_LOCKS_WARN_ON|held lock",
    re.IGNORECASE,
)
FAILURES = re.compile(
    r"ACK_FIXTURE_(?:RACE|REFILL_FAILURE)_FAIL|ACK_FIXTURE_FAIL|ACK_FIXTURE_GUEST_FAIL"
)
REQUIRED_CASES = (
    "ACK_FIXTURE_RACE_PASS variant=baseline",
    "ACK_FIXTURE_RACE_PASS variant=candidate",
    "ACK_FIXTURE_REFILL_FAILURE_PASS variant=baseline",
    "ACK_FIXTURE_REFILL_FAILURE_PASS variant=candidate",
    "ACK_FIXTURE_PASS ",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_common_packer():
    """Load the already-used newc archive writer for the validated rootfs."""
    spec = importlib.util.spec_from_file_location("ack_initrd_packer", COMMON_PACKER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load initrd helper: {COMMON_PACKER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hash_tree(root: Path) -> str:
    """Hash regular files and symlinks using stable relative names."""
    digest = hashlib.sha256()
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        metadata = path.lstat()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        if path.is_symlink():
            payload = b"L" + str(path.readlink()).encode("utf-8")
        elif path.is_file():
            payload = b"F" + path.read_bytes()
        elif path.is_dir():
            payload = b"D"
        else:
            raise RuntimeError(f"unsupported special file in rootfs: {path}")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        digest.update((metadata.st_mode & 0o7777).to_bytes(4, "big"))
    return digest.hexdigest()


def validate_kernel_config(config: Path) -> None:
    """Refuse to run unless the input kernel was built with target checks."""
    values = {}
    for line in config.read_text(encoding="utf-8").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            name, value = line.split("=", 1)
            values[name] = value
    required = {
        "CONFIG_KASAN": "y",
        "CONFIG_KASAN_GENERIC": "y",
        "CONFIG_LOCKDEP": "y",
        "CONFIG_PROVE_LOCKING": "y",
        "CONFIG_DEBUG_SPINLOCK": "y",
        "CONFIG_SMP": "y",
        "CONFIG_MODULES": "y",
    }
    mismatches = [
        f"{name}={values.get(name, '<unset>')} (expected {value})"
        for name, value in required.items()
        if values.get(name) != value
    ]
    if mismatches:
        raise RuntimeError(
            "kernel config lacks required checks: " + ", ".join(mismatches)
        )


def make_initramfs(rootfs: Path, module: Path, output: Path) -> None:
    """Stage the known-good BusyBox rootfs with an inittab-driven fixture."""
    if not (rootfs / "bin/busybox").is_file():
        raise RuntimeError(f"rootfs lacks BusyBox: {rootfs}")
    init = rootfs / "init"
    if not init.is_symlink() or init.readlink().as_posix() != "/bin/busybox":
        raise RuntimeError("known-good rootfs must use /init -> /bin/busybox")
    if not GUEST_INIT.is_file():
        raise FileNotFoundError(GUEST_INIT)

    packer = load_common_packer()
    with tempfile.TemporaryDirectory(prefix="ack-fixture-rootfs-") as temporary:
        staging = Path(temporary) / "root"
        shutil.copytree(rootfs, staging, symlinks=True)
        (staging / "etc/inittab").write_text(
            "::sysinit:/bin/sh /ack-fixture-init\n", encoding="utf-8"
        )
        shutil.copy2(GUEST_INIT, staging / "ack-fixture-init")
        (staging / "ack-fixture-init").chmod(0o755)
        shutil.copy2(module, staging / "ack_fixture_module.ko")
        (staging / "ack_fixture_module.ko").chmod(0o644)
        with (
            output.open("wb") as raw,
            gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as packed,
        ):
            packer._archive_tree(staging, packed)


def collect_output(log: Path) -> str:
    try:
        return log.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def run(args: argparse.Namespace) -> int:
    input_paths = (args.qemu, args.kernel, args.kernel_config, args.module, args.rootfs)
    for path in input_paths:
        if not path.exists():
            raise FileNotFoundError(path)
    if (
        not args.qemu.is_file()
        or not args.kernel.is_file()
        or not args.module.is_file()
    ):
        raise RuntimeError("QEMU, kernel image, and module must be regular files")
    if args.timeout <= 0 or args.shutdown_grace < 0:
        raise ValueError("timeout must be positive and shutdown grace non-negative")
    if args.output_dir.exists():
        raise FileExistsError(
            f"refusing to overwrite evidence directory: {args.output_dir}"
        )
    validate_kernel_config(args.kernel_config)
    args.output_dir.mkdir(parents=True)
    initrd = args.output_dir / "ack-fixture-initramfs.cpio.gz"
    log = args.output_dir / "qemu.log"
    make_initramfs(args.rootfs, args.module, initrd)

    command = [
        str(args.qemu),
        "-machine",
        "virt",
        "-nic",
        "none",
        "-cpu",
        "cortex-a57",
        "-smp",
        "2",
        "-m",
        "2048",
        "-accel",
        "tcg",
        "-nographic",
        "-monitor",
        "none",
        "-serial",
        "stdio",
        "-no-reboot",
        "-kernel",
        str(args.kernel),
        "-initrd",
        str(initrd),
        "-append",
        "console=ttyAMA0,115200 loglevel=7 panic_on_warn=1 oops=panic panic=-1",
    ]
    (args.output_dir / "command.txt").write_text(
        " ".join(command) + "\n", encoding="utf-8"
    )
    (args.output_dir / "inputs.sha256").write_text(
        "".join(f"{sha256(path)}  {path}\n" for path in input_paths if path.is_file())
        + f"{hash_tree(args.rootfs)}  {args.rootfs} (tree)\n"
        + f"{sha256(initrd)}  {initrd}\n",
        encoding="utf-8",
    )

    print(f"QEMU timeout={args.timeout}s output={log}", flush=True)
    host_stopped_after_guest_pass = False
    timed_out = False
    return_code: int | None = None
    with log.open("w", encoding="utf-8") as serial:
        process = subprocess.Popen(
            command,
            stdout=serial,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + args.timeout
        try:
            while time.monotonic() < deadline:
                return_code = process.poll()
                if return_code is not None:
                    break
                current = collect_output(log)
                if "ACK_FIXTURE_GUEST_PASS " in current:
                    try:
                        return_code = process.wait(timeout=args.shutdown_grace)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            return_code = process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            return_code = process.wait()
                        host_stopped_after_guest_pass = True
                    break
                time.sleep(0.1)
            else:
                timed_out = True
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                return_code = process.returncode
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    output = collect_output(log)
    print(output[-10000:], end="" if output.endswith("\n") else "\n")

    failures = [line for line in output.splitlines() if FAILURES.search(line)]
    errors = [line for line in output.splitlines() if DIAGNOSTICS.search(line)]
    missing = [marker for marker in REQUIRED_CASES if marker not in output]
    if timed_out:
        raise RuntimeError(
            f"QEMU host timeout after {args.timeout}s; fixture may have deadlocked"
        )
    if "KernelAddressSanitizer initialized (generic)" not in output:
        raise RuntimeError("boot log does not confirm active generic KASAN")
    if "Lock dependency validator: Copyright" not in output:
        raise RuntimeError("boot log does not confirm active lockdep")
    if return_code not in (0, -15, -9):
        raise RuntimeError(f"QEMU exited with unexpected status {return_code}")
    if "ACK_FIXTURE_GUEST_PASS insmod=0 rmmod=0 dmesg=clean" not in output:
        raise RuntimeError(
            "guest did not prove module load, unload, and post-unload dmesg scan"
        )
    if failures:
        raise RuntimeError("fixture assertion failure:\n" + "\n".join(failures))
    if missing:
        raise RuntimeError(
            "guest omitted required success markers: " + ", ".join(missing)
        )
    if errors:
        raise RuntimeError("kernel diagnostics detected:\n" + "\n".join(errors))
    shutdown = "guest-clean" if return_code == 0 else "host-stopped-after-guest-pass"
    if (
        shutdown == "host-stopped-after-guest-pass"
        and not host_stopped_after_guest_pass
    ):
        raise RuntimeError(
            "QEMU was externally terminated without the expected guest-pass handoff"
        )
    print(f"ACK_FIXTURE_QEMU_PASS shutdown={shutdown} output={args.output_dir}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--kernel-config", type=Path, required=True)
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--shutdown-grace", type=float, default=3.0)
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
