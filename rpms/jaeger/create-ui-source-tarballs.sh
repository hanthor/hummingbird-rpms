#!/usr/bin/bash

set -euo pipefail

version="${1:?version argument required}"
tag="v${version}"
pkg="jaeger"
repo="https://github.com/jaegertracing/${pkg}"
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

for tool in git node npm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "missing required tool: $tool" >&2
    exit 1
  fi
done

node_major=$(node -p 'process.versions.node.split(".")[0]')
if (( node_major < 24 )); then
  echo "node >= 24 is required to build jaeger UI sources (found $(node --version))" >&2
  exit 1
fi

echo "Cloning ${repo} at ${tag} with submodules" >&2
git -c advice.detachedHead=false clone --branch "$tag" --depth 1 --recurse-submodules \
  "${repo}.git" "$workdir/${pkg}-${version}" >&2

ui_dir="$workdir/${pkg}-${version}/jaeger-ui"
# Use the pinned jaeger-ui submodule commit timestamp as the canonical
# timestamp for the generated source artifacts.
source_epoch=$(git -C "$ui_dir" log -1 --format=%ct HEAD)

echo "Creating jaeger-ui source tarball from pinned submodule commit" >&2
git -C "$ui_dir" archive --format=tar --prefix="jaeger-ui-${version}/" HEAD | gzip -n > "jaeger-ui-${version}.tar.gz"

pushd "$ui_dir" >/dev/null
echo "Installing UI dependencies" >&2
npm ci --ignore-scripts >&2

echo "Creating reproducible node_modules tarball" >&2
mapfile -t dep_dirs < <(find . -type d -name node_modules -print | LC_ALL=C sort)
if (( ${#dep_dirs[@]} == 0 )); then
  echo "no node_modules directories were created by npm ci" >&2
  exit 1
fi
tar --sort=name --mtime="@${source_epoch}" --owner=0 --group=0 --numeric-owner \
  --transform "s,^\./,jaeger-ui-${version}/," \
  -cf - "${dep_dirs[@]}" | gzip -n > "$OLDPWD/jaeger-ui-node_modules-${version}.tar.gz"
popd >/dev/null
