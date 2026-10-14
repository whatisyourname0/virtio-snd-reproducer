#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu

usage() {
    echo "usage: $0 OUTPUT_DIR" >&2
    echo "Needs a target-architecture C compiler, ALSA development headers, and libasound." >&2
    exit 64
}
[ "$#" -eq 1 ] || usage
out=$1
mkdir -p "$out"
out=$(cd "$out" && pwd -P)
src=$(cd "$(dirname "$0")/.." && pwd -P)/source
lifecycle_src=$(cd "$(dirname "$0")/.." && pwd -P)/lifecycle/source
cc=${CC:-cc}
cflags=${CFLAGS:--O2 -Wall -Wextra -Werror}
ldflags=${LDFLAGS:-}

# The resulting executables must match the guest architecture and libc.
# For the documented Alpine ARM64 guest, run this on ARM64 Alpine.
$cc $cflags "$src/capture_drain_no_query_test.c" $ldflags -lasound -o "$out/no-query-probe"
$cc $cflags "$src/playback_drain_guard.c" $ldflags -lasound -o "$out/playback-guard"
$cc $cflags "$lifecycle_src/fsm_control_probe.c" $ldflags -pthread -lasound -o "$out/fsm_control_probe"
$cc $cflags "$lifecycle_src/pause_rxflush_probe.c" $ldflags -lasound -o "$out/pause-rxflush-probe"
echo "PROBES=$out/no-query-probe $out/playback-guard $out/fsm_control_probe $out/pause-rxflush-probe"
