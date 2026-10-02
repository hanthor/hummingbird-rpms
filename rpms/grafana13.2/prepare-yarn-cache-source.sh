#!/usr/bin/env bash
# Keep the Yarn cache and generated npm Provides on the same dependency graph.
# Apply frontend patches in the same order as the spec's %goprep.
set -euo pipefail

source_dir=${1:?Usage: prepare-yarn-cache-source.sh SOURCE_DIR}
package_dir=$(cd "$(dirname "$0")" && pwd)
cd "${source_dir}"

patch --batch -p1 < "${package_dir}/grafana-yarn-cache.patch"
patch --batch -p1 < "${package_dir}/grafana-yarn-cache-compression.patch"
patch --batch -p1 < "${package_dir}/grafana-fast-uri-3.1.6.patch"
patch --batch -p1 < "${package_dir}/grafana-qs-6.16.0.patch"
patch --batch -p1 < "${package_dir}/grafana-js-yaml-cve.patch"
patch --batch -p1 < "${package_dir}/grafana-fast-uri-3.1.7.patch"
# Axios's package.json hunk expects the Joi resolutions to be present first.
patch --batch -p1 < "${package_dir}/grafana-joi-cve.patch"
patch --batch -p1 < "${package_dir}/grafana-dompurify-cve.patch"
patch --batch -p1 < "${package_dir}/grafana-fast-uri-3.1.8-cve.patch"
patch --batch -p1 < "${package_dir}/grafana-hono-4.13.7-cve.patch"
patch --batch -p1 < "${package_dir}/grafana-axios-1.20.0-cve.patch"
