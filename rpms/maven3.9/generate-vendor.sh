#!/usr/bin/bash
set -euo pipefail

version=${1:-3.9.16}
package_dir=$(cd "$(dirname "$0")" && pwd)
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

central=https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/${version}
source_name=apache-maven-${version}-src.tar.gz
bootstrap_name=apache-maven-${version}-bin.tar.gz
repository_name=maven-${version}-repository.tar.zst

curl --fail --location --silent --show-error \
    "${central}/${source_name}" --output "${work_dir}/${source_name}"
curl --fail --location --silent --show-error \
    "${central}/${bootstrap_name}" --output "${work_dir}/${bootstrap_name}"

mkdir "${work_dir}/source" "${work_dir}/bootstrap"
tar -xzf "${work_dir}/${source_name}" -C "${work_dir}/source"
tar -xzf "${work_dir}/${bootstrap_name}" -C "${work_dir}/bootstrap"

podman run --rm \
    --volume "${work_dir}:/work:Z" \
    --workdir "/work/source/apache-maven-${version}" \
    fedora:latest \
    bash -lc \
    "dnf -y -q --setopt=install_weak_deps=False install java-25-openjdk-devel >/dev/null && \
     /work/bootstrap/apache-maven-${version}/bin/mvn \
       --batch-mode --no-transfer-progress \
       -Dmaven.repo.local=/work/repository clean package"

# Maven writes repository bookkeeping files that are not build inputs.
find "${work_dir}/repository" \
    \( -name '*.lastUpdated' -o -name '_remote.repositories' -o -name 'resolver-status.properties' \) \
    -delete

tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
    --zstd -cf "${work_dir}/${repository_name}" \
    -C "${work_dir}/repository" .

install -m 0644 "${work_dir}/${source_name}" "${package_dir}/${source_name}"
install -m 0644 "${work_dir}/${bootstrap_name}" "${package_dir}/${bootstrap_name}"
install -m 0644 "${work_dir}/${repository_name}" "${package_dir}/${repository_name}"

cd "${package_dir}"
sha512sum --tag "${source_name}" "${bootstrap_name}" "${repository_name}" > sources
