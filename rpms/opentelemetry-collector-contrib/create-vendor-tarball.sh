#!/usr/bin/bash
# Generate source archives for opentelemetry-collector-contrib.
#
# The contrib repo does not commit cmd/otelcontribcol/{main.go,components.go,
# go.mod,go.sum}.  The OpenTelemetry Collector Builder (OCB) generates them.
#
# go-vendor-tools.toml declares:
#   go_mod_dir    — tells go_vendor_archive to vendor from cmd/otelcontribcol/
#   post_commands — fetches missing license files after go mod vendor
#
# NOTE: pre_commands cannot be used for OCB because go_vendor_archive runs
# pre_commands from go_mod_dir (cmd/otelcontribcol/), but OCB requires the
# source root as CWD. OCB is therefore run manually before go_vendor_archive.
#
# Usage: create-vendor-tarball.sh <version>   e.g. create-vendor-tarball.sh 0.153.0

set -euo pipefail

version="${1:?version argument required}"
pkg="opentelemetry-collector-contrib"
tarball="${pkg}-${version}.tar.gz"
ocb_url="https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/cmd%2Fbuilder%2Fv${version}/ocb_${version}_linux_amd64"

echo "Downloading ${tarball}..." >&2
curl -fSL -o "${tarball}" \
    "https://github.com/open-telemetry/opentelemetry-collector-contrib/archive/v${version}/${tarball}" >&2

echo "Downloading OCB v${version}..." >&2
curl -fSL -o ocb "${ocb_url}" >&2
chmod +x ocb

workdir=$(mktemp -d)
trap "rm -rf '${workdir}' ocb" EXIT

tar -xzf "${tarball}" -C "${workdir}"
srcdir="${workdir}/${pkg}-${version}"

echo "Running OCB (source root required as CWD)..." >&2
(cd "${srcdir}" && "${PWD}/ocb" --skip-compilation \
    --config cmd/otelcontribcol/builder-config.yaml) >&2

echo "Capturing OCB-generated sources..." >&2
generated_files=(go.mod go.sum)
while IFS= read -r f; do
    generated_files+=("${f}")
done < <(find "${srcdir}/cmd/otelcontribcol" -maxdepth 1 -name '*.go' -printf '%P\n' | sort)
tar -C "${srcdir}/cmd/otelcontribcol" \
    -cjf "${pkg}-${version}-generated.tar.bz2" \
    "${generated_files[@]}"

echo "Vendoring via go_vendor_archive (go_mod_dir=cmd/otelcontribcol, post_commands=license fetch)..." >&2
# Pass cmd/otelcontribcol/ directly — go_vendor_archive creates bare vendor/ prefix
# (no subdirectory prefix), which the spec extracts inside pushd cmd/otelcontribcol.
go_vendor_archive create \
    -c "$(dirname "${BASH_SOURCE[0]}")/go-vendor-tools.toml" \
    -O "${pkg}-${version}-vendor.tar.bz2" \
    "${srcdir}/cmd/otelcontribcol" >&2

echo "${tarball}"
echo "${pkg}-${version}-generated.tar.bz2"
echo "${pkg}-${version}-vendor.tar.bz2"
