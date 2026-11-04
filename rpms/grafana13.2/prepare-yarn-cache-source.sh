#!/usr/bin/env bash
# Apply frontend patches in the same order as the spec. Both gorget's cache
# transform and the bundled npm Provides generator consume this dependency graph.
set -euo pipefail

source_dir=${1:?Usage: prepare-yarn-cache-source.sh SOURCE_DIR}
package_dir=$(cd "$(dirname "$0")" && pwd)
cd "${source_dir}"

patch -p1 < "${package_dir}/grafana-yarn-cache.patch"
patch -p1 < "${package_dir}/grafana-yarn-cache-compression.patch"
patch -p1 < "${package_dir}/grafana-fast-uri-3.1.6.patch"
patch -p1 < "${package_dir}/grafana-qs-6.16.0.patch"
patch -p1 < "${package_dir}/grafana-js-yaml-cve.patch"
patch -p1 < "${package_dir}/grafana-fast-uri-3.1.7.patch"
patch -p1 < "${package_dir}/grafana-joi-cve.patch"
patch -p1 < "${package_dir}/grafana-dompurify-cve.patch"
patch -p1 < "${package_dir}/grafana-fast-uri-3.1.8-cve.patch"
patch -p1 < "${package_dir}/grafana-hono-4.13.7-cve.patch"
patch -p1 < "${package_dir}/grafana-axios-1.20.0-cve.patch"
patch -p1 < "${package_dir}/grafana-hono-4.13.11-cve.patch"
patch -p1 < "${package_dir}/grafana-qs-6.16.1.patch"
patch -p1 < "${package_dir}/grafana-storybook-follow-redirects-cve.patch"
patch -p1 < "${package_dir}/grafana-body-parser-fast-uri-cve.patch"
patch -p1 < "${package_dir}/grafana-qs-6.17.0-follow-redirects-1.16.2-cve.patch"
patch -p1 < "${package_dir}/grafana-joi-cve-2026-118772.patch"
