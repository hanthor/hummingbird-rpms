#!/usr/bin/env bash
set -euo pipefail

source_dir=${1:?Usage: prepare-yarn-cache-source.sh SOURCE_DIR VERSION}
package_dir=$(cd "$(dirname "$0")" && pwd)

cd "${source_dir}"
patch -p1 < "${package_dir}/grafana-yarn-cache.patch"
patch -p1 < "${package_dir}/grafana-fast-uri-cve.patch"
patch -p1 < "${package_dir}/grafana-npm-cve-brace-postcss.patch"
patch -p1 < "${package_dir}/grafana-ip-address-cve.patch"
patch -p1 < "${package_dir}/grafana-browserslist-nanoid-cve.patch"
patch -p1 < "${package_dir}/grafana-dompurify-cve.patch"
patch -p1 < "${package_dir}/grafana-yarn-cache-compression.patch"
patch -p1 < "${package_dir}/grafana-js-yaml-cve.patch"
patch -p1 < "${package_dir}/grafana-fast-uri-3.1.7-cve.patch"
patch -p1 < "${package_dir}/grafana-undici-cve.patch"
patch -p1 < "${package_dir}/grafana-joi-cve.patch"
patch -p1 < "${package_dir}/grafana-fast-uri-3.1.8-cve.patch"
patch -p1 < "${package_dir}/grafana-dompurify-3.4.13-cve.patch"
patch -p1 < "${package_dir}/grafana-hono-4.13.7-cve.patch"
patch -p1 < "${package_dir}/grafana-axios-1.20.0-cve.patch"
