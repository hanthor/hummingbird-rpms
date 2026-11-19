#!/usr/bin/env bash
# Apply frontend dependency patches in the same order as %prep.
#
# Both Gorget's Yarn-cache transform and the generated npm Provides consume
# this patched dependency graph. In generation mode, create the version-bound
# compression patch that scheduled updates cannot have in advance.
set -euo pipefail

source_dir=${1:?Usage: prepare-yarn-cache-source.sh SOURCE_DIR VERSION [--generate-compression]}
version=${2:?Usage: prepare-yarn-cache-source.sh SOURCE_DIR VERSION [--generate-compression]}
mode=${3:-}
package_dir=$(cd "$(dirname "$0")" && pwd)

cd "${source_dir}"

patch -p1 < "${package_dir}/grafana-yarn-cache.patch"

if [[ "${mode}" == "--generate-compression" ]]; then
    compression_workdir=$(mktemp -d)
    trap 'rm -rf "${compression_workdir}"' EXIT
    cp .yarnrc.yml "${compression_workdir}/yarnrc.before"
    cp yarn.lock "${compression_workdir}/yarn.lock.before"
    sed -i 's/^compressionLevel:.*/compressionLevel: 0/' .yarnrc.yml
    CYPRESS_INSTALL_BINARY=0 YARN_ENABLE_GLOBAL_CACHE=false YARN_CACHE_FOLDER=.yarn/cache \
        YARN_ENABLE_IMMUTABLE_INSTALLS=false YARN_NPM_MINIMAL_AGE_GATE=0 \
        node .yarn/releases/yarn-*.cjs install
    compression_patch="grafana-yarn-cache-compression-${version}.patch"
    diff -u --label a/.yarnrc.yml --label b/.yarnrc.yml \
        "${compression_workdir}/yarnrc.before" .yarnrc.yml \
        > "${compression_patch}" || [[ $? -eq 1 ]]
    diff -u --label a/yarn.lock --label b/yarn.lock \
        "${compression_workdir}/yarn.lock.before" yarn.lock \
        >> "${compression_patch}" || [[ $? -eq 1 ]]
elif [[ -z "${mode}" ]]; then
    patch -p1 < "${package_dir}/grafana-yarn-cache-compression-${version}.patch"
else
    echo "Unknown mode: ${mode}" >&2
    exit 2
fi

# 12.4.14's own lockfile already carries the older dependency CVE fixes; only
# the patches below apply, in the same order as the spec.
patch -p1 < "${package_dir}/grafana-dompurify-3.4.13-cve.patch"
patch -p1 < "${package_dir}/grafana-ip-address-cve.patch"
patch -p1 < "${package_dir}/grafana-body-parser-cve.patch"
patch -p1 < "${package_dir}/grafana-follow-redirects-serialize-javascript-cve.patch"
patch -p1 < "${package_dir}/grafana-joi-17.13.9-cve.patch"
patch -p1 < "${package_dir}/grafana-js-yaml-4.1.1-cve.patch"
patch -p1 < "${package_dir}/grafana-storybook-8.6.19-cve.patch"
patch -p1 < "${package_dir}/grafana-uuid-11.1.0-lockfile.patch"
patch -p1 < "${package_dir}/grafana-hono-undici-node-forge-papaparse-cve.patch"
