#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu

if [ "$#" -ne 4 ]; then
	echo 'usage: build-synthetic-module.sh KERNEL_SOURCE KERNEL_ARTIFACTS MODULE_SOURCE OUTPUT_DIR' >&2
	exit 2
fi

kernel_source=$(cd "$1" && pwd -P)
kernel_artifacts=$(cd "$2" && pwd -P)
module_source=$(cd "$3" && pwd -P)
mkdir -p "$4"
output_dir=$(cd "$4" && pwd -P)
builder_image=$(printenv BUILDER_IMAGE 2>/dev/null || true)
docker_context=$(printenv DOCKER_CONTEXT 2>/dev/null || true)
docker_cli=$(command -v docker)
[ -n "$builder_image" ] || builder_image=industrial-virtio-module-builder:alpine322-gnu

docker_cmd() {
	if [ -n "$docker_context" ]; then
		"$docker_cli" --context "$docker_context" "$@"
	else
		"$docker_cli" "$@"
	fi
}

[ -f "$kernel_artifacts/build-artifacts.sha256" ] || { echo 'kernel build completion marker is missing' >&2; exit 1; }
[ -s "$kernel_artifacts/Image" ] || { echo 'kernel Image is missing' >&2; exit 1; }
[ -s "$kernel_artifacts/Module.symvers" ] || { echo 'kernel Module.symvers is missing' >&2; exit 1; }
[ -s "$kernel_artifacts/kernel.config" ] || { echo 'kernel config is missing' >&2; exit 1; }
[ -f "$module_source/Makefile" ] && [ -f "$module_source/snd_pcm_drain_test.c" ] || {
	echo 'synthetic module source must contain Makefile and snd_pcm_drain_test.c' >&2
	exit 1
}
if [ -e "$output_dir/snd_pcm_drain_test.ko" ] || [ -e "$output_dir/build.log" ]; then
	echo "refuse to overwrite module build artifacts in $output_dir" >&2
	exit 1
fi

builder_id=$(docker_cmd image inspect --format '{{.Id}}' "$builder_image")
builder_arch=$(docker_cmd image inspect --format '{{.Architecture}}' "$builder_image")
builder_os=$(docker_cmd image inspect --format '{{.Os}}' "$builder_image")
[ "$builder_arch/$builder_os" = 'arm64/linux' ] || {
	echo "builder image must be linux/arm64, got $builder_os/$builder_arch" >&2
	exit 1
}
printf 'BUILDER_IMAGE=%s\nBUILDER_IMAGE_ID=%s\n' "$builder_image" "$builder_id"

if docker_cmd run --rm --pull=never --platform linux/arm64 \
	--mount "type=bind,source=$kernel_source,target=/source,readonly" \
	--mount "type=bind,source=$kernel_artifacts,target=/kernel-artifacts,readonly" \
	--mount "type=bind,source=$module_source,target=/module-source,readonly" \
	--mount "type=bind,source=$output_dir,target=/artifact" \
	"$builder_id" sh -ec '
mkdir -p /tmp/kernel-build /tmp/module
cp /kernel-artifacts/kernel.config /tmp/kernel-build/.config
make -C /source O=/tmp/kernel-build ARCH=arm64 olddefconfig
release=$(make -s -C /source O=/tmp/kernel-build ARCH=arm64 kernelrelease)
expected=$(cat /kernel-artifacts/kernel-release.txt)
test "$release" = "$expected"
make -C /source O=/tmp/kernel-build ARCH=arm64 modules_prepare
cp /kernel-artifacts/Module.symvers /tmp/kernel-build/Module.symvers
cp /module-source/Makefile /module-source/snd_pcm_drain_test.c /tmp/module/
make -C /source O=/tmp/kernel-build M=/tmp/module ARCH=arm64 W=1 modules
modinfo -F vermagic /tmp/module/snd_pcm_drain_test.ko > /artifact/module.vermagic
case "$(cat /artifact/module.vermagic)" in
  "$release"\ *) ;;
  *) echo "module vermagic does not match $release" >&2; exit 1 ;;
esac
cp /tmp/module/snd_pcm_drain_test.ko /artifact/snd_pcm_drain_test.ko
printf "%s\n" "$release" > /artifact/kernel.release
sha256sum /artifact/snd_pcm_drain_test.ko /artifact/kernel.release > /artifact/module.sha256
cat /artifact/module.sha256
cat /artifact/module.vermagic
' > "$output_dir/build.log" 2>&1; then
	cat "$output_dir/build.log"
else
	status=$?
	cat "$output_dir/build.log" >&2
	exit "$status"
fi
