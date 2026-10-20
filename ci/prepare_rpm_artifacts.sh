#!/bin/bash
# Fetch build artifacts before tests run and publish their paths to the plan environment.
set -euo pipefail
export PATH
oci_manifest=
source tools/tmt-rpmtest/src/tmt_rpmtest/environment/download.sh
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
  rpmtest_download curl \
    --location \
    --silent \
    --fail \
    --show-error \
    --connect-timeout 30 \
    --max-time 60 \
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
  oci_manifest="${TMT_PLAN_DATA}/rpm-artifact-manifest.json"
  # Resolve a mutable build tag once so the manifest and files come from the same artifact.
  artifact_reference=$(rpmtest_download timeout --signal=TERM --kill-after=10s 60s \
    "${HOME}/.local/bin/oras" resolve --full-reference "${IMAGE_URL}")
  fetch_manifest() {
    timeout --signal=TERM --kill-after=10s 60s \
      "${HOME}/.local/bin/oras" manifest fetch "${artifact_reference}" > "${oci_manifest}"
  }
  rpmtest_download fetch_manifest
  rpmtest_download timeout --verbose --signal=TERM --kill-after=10s 120s \
    "${HOME}/.local/bin/oras" pull "${artifact_reference}" -o "${source_dir}"
else
  source_dir=${RPMTEST_LOCAL_RPMS:-}
fi
[[ -n ${source_dir} ]] || exit 0

rpms_dir=$(mktemp -d "${TMT_PLAN_DATA}/build-rpms.XXXXXX")
source_rpm=
foreign_artifacts=0
for artifact in "${source_dir}"/*.rpm; do
  [[ -f ${artifact} ]] || continue
  if [[ ${artifact} == *.src.rpm ]]; then
    if [[ -n ${source_rpm} ]]; then
      echo "The build artifact contains multiple source RPMs." >&2
      exit 1
    fi
    source_rpm=${artifact}
    continue
  fi
  architecture=$(rpm -qp --queryformat '%{ARCH}' "${artifact}" 2>/dev/null)
  if [[ ${architecture} == noarch || ${architecture} == "${current_arch}" ]]; then
    expected_digest=$(sha256sum "${artifact}")
    expected_digest=${expected_digest%% *}
    cp "${artifact}" "${rpms_dir}/"
    copied_digest=$(sha256sum "${rpms_dir}/$(basename "${artifact}")")
    if [[ ${copied_digest%% *} != "${expected_digest}" ]]; then
      echo "An RPM artifact changed during transfer: ${artifact}" >&2
      exit 1
    fi
  else
    foreign_artifacts=$((foreign_artifacts + 1))
  fi
done
if [[ -n ${IMAGE_URL:-} && -z ${source_rpm} ]] && compgen -G "${rpms_dir}/*.rpm" >/dev/null; then
  echo "The build artifact does not contain a source RPM." >&2
  exit 1
fi
no_native=false
if ! compgen -G "${rpms_dir}/*.rpm" >/dev/null; then
  if (( foreign_artifacts > 0 )) && [[ -n ${source_rpm} ]]; then
    no_native=true
  fi
  echo "No native or noarch binary RPMs were found; skip legacy checks."
  source_rpm=
fi
catalog="${TMT_PLAN_DATA}/rpm-artifacts.json"
catalog_arguments=()
if [[ -n ${oci_manifest} ]]; then
  catalog_arguments+=(--oci-manifest "${oci_manifest}")
fi
PYTHONPATH="${PWD}/tools/tmt-rpmtest/src${PYTHONPATH:+:${PYTHONPATH}}" \
  "${RPMTEST_BOOTSTRAP_PYTHON:-python3.11}" -m tmt_rpmtest.environment.artifacts \
  --source "${source_dir}" --binaries "${rpms_dir}" --architecture "${current_arch}" \
  --output "${catalog}" "${catalog_arguments[@]}"
{
  printf 'RPMTEST_RPMS=%q\n' "${rpms_dir}"
  printf 'RPMTEST_SOURCE_RPM=%q\n' "${source_rpm}"
  printf 'RPMTEST_PACKAGE=%q\n' "${package_name}"
  printf 'RPMTEST_NO_NATIVE=%q\n' "${no_native}"
  printf 'RPMTEST_ARTIFACT_MANIFEST=%q\n' "${catalog}"
} >> "${TMT_PLAN_ENVIRONMENT_FILE}"
