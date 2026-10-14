#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Run inside ARM64 Alpine with build-base and alsa-lib-dev installed.
set -eu

if [ "$#" -ne 2 ]; then
	echo 'usage: build-linked-probes.sh PUBLIC_REPO OUTPUT_DIR' >&2
	exit 2
fi

public_repo=$(cd "$1" && pwd -P)
output=$2
followup=$(cd "$(dirname "$0")/.." && pwd -P)
base="$public_repo/rfc/vz-capture-20261014/qemu"
[ -x "$base/scripts/build-probes.sh" ] || { echo "missing public probe builder: $base/scripts/build-probes.sh" >&2; exit 1; }
[ -f "$base/source/playback_drain_guard.c" ] || { echo 'public playback guard source is missing' >&2; exit 1; }
[ -f "$followup/source/linked_capture_drain_later_member_r1.c" ] || { echo 'linked drain probe source is missing' >&2; exit 1; }

mkdir -p "$output"
output=$(cd "$output" && pwd -P)
"$base/scripts/build-probes.sh" "$output"
cc -O2 -Wall -Wextra -Werror \
	"$followup/source/linked_capture_drain_later_member_r1.c" \
	-lasound -o "$output/linked_capture_drain_later_member_r1"
install -m 0755 "$followup/source/lab-init" "$output/lab-init"
printf 'LINKED_PROBE=%s\n' "$output/linked_capture_drain_later_member_r1"
printf 'PLAYBACK_GUARD=%s\n' "$output/playback-guard"
printf 'LAB_INIT=%s\n' "$output/lab-init"
