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
tar -xzf "$source_tarball" -C "$workdir"
go_vendor_archive create \
  -c "$(dirname "${BASH_SOURCE[0]}")/go-vendor-tools.toml" \
  -O "${pkg}-${version}-vendor.tar.bz2" \
  "$workdir/${pkg}-${version}" >&2
