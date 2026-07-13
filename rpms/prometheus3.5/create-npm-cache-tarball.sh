#!/usr/bin/bash

set -euo pipefail

version="${1:?version argument required}"
archive="prometheus-${version}.tar.gz"
output="prometheus-${version}-npm-cache.tar.xz"
workdir=$(mktemp -d)
trap 'chmod -R u+w "${workdir}"; rm -rf "${workdir}"' EXIT

tar -xzf "${archive}" -C "${workdir}"
source_dir="${workdir}/prometheus-${version}"
cache_dir="${workdir}/.npm-cache"

for architecture in x64 arm64; do
    (
        cd "${source_dir}/web/ui"
        npm_config_cpu="${architecture}" npm ci --ignore-scripts --cache "${cache_dir}"
        rm -rf node_modules
    )
    (
        cd "${source_dir}/web/ui/react-app"
        npm_config_cpu="${architecture}" npm ci --ignore-scripts --cache "${cache_dir}"
        rm -rf node_modules
    )
done

tar -C "${workdir}" -cJf "${output}" .npm-cache
