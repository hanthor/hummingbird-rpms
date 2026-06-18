#!/usr/bin/bash

set -euo pipefail

version="${1:?version argument required}"
tag="v${version}"
pkg="jaeger"
repo="https://github.com/jaegertracing/${pkg}"
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
source_tarball="${pkg}-${version}.tar.gz"

echo "Creating vendor tarball" >&2
if command -v go_vendor_archive >/dev/null 2>&1; then
  tar -xzf "$source_tarball" -C "$workdir"
  go_vendor_archive create \
    -c "$(dirname "${BASH_SOURCE[0]}")/go-vendor-tools.toml" \
    -O "${pkg}-${version}-vendor.tar.bz2" \
    "$workdir/${pkg}-${version}" >&2
else
  echo "go_vendor_archive not found, falling back to 'go mod vendor'" >&2
  echo "Cloning ${repo} at ${tag}" >&2
  git -c advice.detachedHead=false clone --branch "$tag" --depth 1 --recurse-submodules \
    "${repo}.git" "$workdir/${pkg}-${version}" >&2
  pushd "$workdir/${pkg}-${version}" >/dev/null
  GOPROXY='https://proxy.golang.org,direct' go mod vendor >&2
  popd >/dev/null
  tar -C "$workdir/${pkg}-${version}" -cjf "${pkg}-${version}-vendor.tar.bz2" vendor
fi
