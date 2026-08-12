#!/usr/bin/env bash

set -euo pipefail

: "${UPDATE_PACKAGE_DIR:?}"
: "${UPDATE_SPEC_FILE:?}"

source_archive=$(
  find "${UPDATE_PACKAGE_DIR}" -maxdepth 1 -type f -name 'openjdk-*.tar.xz' \
    -printf '%T@ %p\n' |
    sort -nr |
    head -n 1 |
    cut -d ' ' -f 2-
)

if [[ -z "${source_archive}" ]]; then
  echo "OpenJDK source archive not found in ${UPDATE_PACKAGE_DIR}" >&2
  exit 1
fi

tmpdir=$(mktemp -d)
generated=$(mktemp)
trap 'rm -rf "${tmpdir}" "${generated}"' EXIT

tar -xf "${source_archive}" -C "${tmpdir}"
source_root=$(find "${tmpdir}" -mindepth 1 -maxdepth 1 -type d -name 'jdk-*' -print -quit)
if [[ -z "${source_root}" ]]; then
  echo "OpenJDK source root not found in ${source_archive}" >&2
  exit 1
fi

./get_bundle_versions.sh "${source_root}" |
  sed -n '/^# Version in /,$p' > "${generated}"

if [[ ! -s "${generated}" ]]; then
  echo "No bundled Provides generated from ${source_archive}" >&2
  exit 1
fi

awk -v generated="${generated}" '
BEGIN {
    while ((getline line < generated) > 0)
        replacement = replacement line "\n"
    close(generated)
}
/^# BEGIN generated bundled Provides$/ {
    print
    printf "%s", replacement
    in_block = 1
    found_begin = 1
    next
}
/^# END generated bundled Provides$/ {
    in_block = 0
    found_end = 1
}
!in_block { print }
END {
    if (!found_begin || !found_end)
        exit 1
}
' "${UPDATE_SPEC_FILE}" > "${UPDATE_SPEC_FILE}.tmp"
mv "${UPDATE_SPEC_FILE}.tmp" "${UPDATE_SPEC_FILE}"
