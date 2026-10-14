#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Export a pinned kernel and apply local prerequisites without changing its repo.
set -eu
if [ "$#" -ne 3 ]; then
    echo 'usage: prepare-source.sh EXISTING_KERNEL_GIT NEW_OUTPUT_DIR base|v3|candidate' >&2
    exit 2
fi
src=$(cd "$1" && pwd)
out=$2
mode=$3
case "$mode" in base|v3|candidate) ;; *) echo 'invalid mode' >&2; exit 2;; esac
base=8d70e364e866bdfca81eee24d0688fac9472cfb9
bundle=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
git -C "$src" cat-file -e "$base^{commit}"
if [ -e "$out" ]; then echo 'output must not already exist' >&2; exit 2; fi
mkdir -p "$out"
out=$(cd "$out" && pwd)
# Separate archive creation from extraction so either failure is observed.
archive=$(mktemp "${TMPDIR:-/tmp}/virtio-rfc-source.XXXXXX")
trap 'rm -f "$archive"' EXIT HUP INT TERM
git -C "$src" archive "$base" > "$archive"
tar -xf "$archive" -C "$out"
git -C "$out" init -q
if [ "$mode" != base ]; then
    for patch in "$bundle"/prerequisites/000[1-5]-*.patch; do
        git -C "$out" apply --check "$patch"
        git -C "$out" apply "$patch"
    done
fi
if [ "$mode" = candidate ]; then
    for patch in "$bundle"/rfc/generic-control-wait/0001-*.patch \
                 "$bundle"/rfc/core-capture-drain/000[12]-*.patch \
                 "$bundle"/rfc/vz-capture-compat/0001-*.patch; do
        git -C "$out" apply --check "$patch"
        git -C "$out" apply "$patch"
    done
fi
printf '%s\n' "Source prepared: $out ($mode on $base). No source repository edits or commits."
