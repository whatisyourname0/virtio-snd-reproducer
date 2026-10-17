#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail
if [[ $# -ne 2 || $1 != --output ]]; then
  echo 'Usage: prepare_rootfs.sh --output NEW_TAR_GZ' >&2
  exit 2
fi
[[ ! -e $2 ]] || { echo 'Output already exists' >&2; exit 1; }
mkdir -p "$(dirname "$2")"
temporary=$(mktemp "${2}.tmp.XXXXXX")
trap 'rm -f "$temporary"' EXIT
# apk's progress goes to stderr so the archive stream remains intact.
docker run --rm --platform linux/arm64 alpine:3.22 sh -ec \
  'apk add --no-cache alsa-utils kmod >&2; tar -czf - bin etc lib sbin usr var' \
  > "$temporary"
mv "$temporary" "$2"
trap - EXIT
