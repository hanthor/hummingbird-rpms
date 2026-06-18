#!/usr/bin/bash

set -euo pipefail

version="${1:?version argument required}"
tag="v${version}"
pkg="jaeger-ui"
repo="https://github.com/jaegertracing/${pkg}"
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

echo "Cloning ${repo} at ${tag}" >&2
git -c advice.detachedHead=false clone --branch "$tag" --depth 1 "${repo}.git" "$workdir/${pkg}-${version}" >&2

pushd "$workdir/${pkg}-${version}" >/dev/null
echo "Installing UI dependencies" >&2
npm ci >&2
echo "Building UI assets" >&2
(cd packages/jaeger-ui && npm run build) >&2
tar -czf "$OLDPWD/jaeger-ui-assets-${version}.tar.gz" packages/jaeger-ui/build
popd >/dev/null
