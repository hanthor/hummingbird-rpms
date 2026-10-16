#!/bin/bash
# Fetch build artifacts before tests run and publish their paths to the plan environment.
set -euo pipefail
export PATH
[[ -n ${IMAGE_NAME:-} ]] || exit 0
: "${IMAGE_NAME:?}" "${TMT_PLAN_DATA:?}" "${TMT_PLAN_ENVIRONMENT_FILE:?}"
current_arch=$(uname -m)
directories=$(find rpms -mindepth 1 -maxdepth 1 -type d)
package_name=${IMAGE_NAME%-main}
while IFS= read -r directory; do
  name=$(basename "${directory}")
  normalized=${name,,}
  normalized=${normalized//[_.+]/-}
  if [[ ${normalized} == "${package_name}" ]]; then
    package_name=${name}
    break
  fi
done <<< "${directories}"

if [[ -n ${IMAGE_URL:-} ]]; then
  mkdir -p ~/.local/bin
  ARCH="$(arch)"
  case ${ARCH} in
      "x86_64")
          ARCH="amd64"
          ;;
      "aarch64")
          ARCH="arm64"
          ;;
      *) ;;
  esac
  OS="linux"
  oras_version="1.3.0"
  oras_archive=$(mktemp)
  curl \
    --location \
    --silent \
    --fail \
    --show-error \
    --retry 5 \
    --retry-delay 5 \
    --connect-timeout 30 \
    --globoff \
    --output "${oras_archive}" \
    "https://github.com/oras-project/oras/releases/download/v${oras_version}/oras_${oras_version}_${OS}_${ARCH}.tar.gz"
  tar \
    --extract \
    --gzip \
    --file "${oras_archive}" \
    --directory ~/.local/bin \
    oras
  rm -f "${oras_archive}"

  source_dir=$(mktemp -d "${TMT_PLAN_DATA}/rpm-download.XXXXXX")
  "${HOME}/.local/bin/oras" pull "${IMAGE_URL}" -o "${source_dir}"
else
  source_dir=${RPMTEST_LOCAL_RPMS:-}
fi
[[ -n ${source_dir} ]] || exit 0

rpms_dir=$(mktemp -d "${TMT_PLAN_DATA}/build-rpms.XXXXXX")
source_rpm=
for artifact in "${source_dir}"/*.rpm; do
  [[ -f ${artifact} ]] || continue
  if [[ ${artifact} == *.src.rpm ]]; then
    source_rpm=${artifact}
    continue
  fi
  architecture=$(rpm -qp --queryformat '%{ARCH}' "${artifact}" 2>/dev/null)
  if [[ ${architecture} == noarch || ${architecture} == "${current_arch}" ]]; then
    cp "${artifact}" "${rpms_dir}/"
  fi
done
if [[ -n ${IMAGE_URL:-} && -z ${source_rpm} ]] && compgen -G "${rpms_dir}/*.rpm" >/dev/null; then
  echo "The build artifact does not contain a source RPM." >&2
  exit 1
fi
if ! compgen -G "${rpms_dir}/*.rpm" >/dev/null; then
  echo "No native or noarch binary RPMs were found; skip legacy checks."
  source_rpm=
fi
{
  printf 'RPMTEST_RPMS=%q\n' "${rpms_dir}"
  printf 'RPMTEST_SOURCE_RPM=%q\n' "${source_rpm}"
  printf 'RPMTEST_PACKAGE=%q\n' "${package_name}"
} >> "${TMT_PLAN_ENVIRONMENT_FILE}"
