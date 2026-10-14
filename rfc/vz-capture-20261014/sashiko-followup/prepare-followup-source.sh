#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Export public base + submitted v3 prerequisites, then apply the signed follow-up stack.
set -eu

if [ "$#" -ne 3 ]; then
	echo 'usage: prepare-followup-source.sh PUBLIC_REPO EXISTING_KERNEL_GIT NEW_OUTPUT_DIR' >&2
	exit 2
fi

public_repo=$(cd "$1" && pwd -P)
kernel_git=$(cd "$2" && pwd -P)
output=$3
followup=$(cd "$(dirname "$0")" && pwd -P)
prepare="$public_repo/rfc/vz-capture-20261014/prepare-source.sh"

[ -x "$prepare" ] || { echo "missing public source preparer: $prepare" >&2; exit 1; }
[ -f "$kernel_git/Makefile" ] || { echo "kernel Git checkout is not a source tree: $kernel_git" >&2; exit 1; }
[ ! -e "$output" ] || { echo "output must not already exist: $output" >&2; exit 1; }

"$prepare" "$kernel_git" "$output" v3
source_tree=$(cd "$output" && pwd -P)

apply_patch() {
	patch=$1
	expected_id=$2
	actual_id=$(git patch-id --stable < "$patch" | awk 'NR == 1 { print $1 }')
	[ "$actual_id" = "$expected_id" ] || {
		echo "patch-id mismatch for $patch: expected $expected_id, got $actual_id" >&2
		exit 1
	}
	git -C "$source_tree" apply --check "$patch"
	git -C "$source_tree" apply "$patch"
	printf 'APPLIED %s %s\n' "$actual_id" "$(basename "$patch")"
}

apply_patch "$followup/patches/generic-control-wait/0001-ALSA-virtio-wait-through-signals-for-PCM-teardown.patch" cfe3e7000e695a2fda27993f4d9acf0a637cf24e
apply_patch "$followup/patches/core-capture-drain/0001-ALSA-pcm-allow-capture-reads-while-draining.patch" 0fc5fec2c6ac5d3043cf2210570472ed4ce32952
apply_patch "$followup/patches/core-capture-drain/0002-ALSA-pcm-refresh-capture-pointers-before-drain.patch" 47ef5d4c76bf044a99192c8133e6ab9755776bde
apply_patch "$followup/patches/vz-capture-compat/0001-ALSA-virtio-stage-VZ-capture-and-restart-paused-stre.patch" 771e8815f2b874d98376f309dc60bc90244f378c

printf 'SOURCE_PREPARED=%s\n' "$source_tree"
printf '%s\n' 'The input Git checkout and its refs/index are unchanged; no commits or signoffs were created.'
