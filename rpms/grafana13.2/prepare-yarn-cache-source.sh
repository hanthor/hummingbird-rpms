#!/usr/bin/env bash
set -euo pipefail

source_dir=${1:?Usage: prepare-yarn-cache-source.sh SOURCE_DIR VERSION [--generate]}
version=${2:?Usage: prepare-yarn-cache-source.sh SOURCE_DIR VERSION [--generate]}
mode=${3:-}
package_dir=$(cd "$(dirname "$0")" && pwd)

if [[ "${mode}" == "--generate" ]]; then
    action=generate
elif [[ "${mode}" == "--activate-spec" ]]; then
    action=activate-spec
elif [[ -z "${mode}" ]]; then
    action=apply
else
    echo "Unknown mode: ${mode}" >&2
    exit 2
fi

python3 "${package_dir}/../../ci/grafana_frontend.py" \
    "${package_dir}" "${source_dir}" "${version}" "${action}"
