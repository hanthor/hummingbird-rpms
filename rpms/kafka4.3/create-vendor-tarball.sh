#!/usr/bin/bash
# Generate vendored Gradle home tarball for offline Kafka RPM builds.
#
# Downloads the Kafka source, runs a full build with network access to
# populate the Gradle dependency cache, then packages GRADLE_USER_HOME
# as a tarball for use during rpmbuild (--offline mode).
#
# A full build is needed (not just dependency resolution) because Gradle
# plugins, annotation processors, and build tool deps are only fetched
# during compilation.
#
# This is a MANUAL step — the automated update hooks cannot run this
# because it requires a JDK.
#
# Prerequisites:
#   - java-25-openjdk-devel (or any JDK >= 21)
#   - curl, tar, xz
#
# Steps to update Kafka to a new version:
#   1. Run this script:
#      ./rpms/kafka4.3/create-vendor-tarball.sh <new-version>
#   2. Upload tarballs to lookaside cache (see output)
#   3. Update rpms/kafka4.3/sources with sha512sum
#   4. Update Version in rpms/kafka4.3/kafka4.3.spec
#   5. Update version in metadata/kafka4.3.json
#   6. Run: make generate-host
#
#   The script automatically updates bundled provides in the spec
#   from the release tarball produced during the build.
#
# Usage: ./create-vendor-tarball.sh <version>

set -euo pipefail

version="${1:?Usage: $0 <version> (e.g. 4.3.0)}"

source_url="https://downloads.apache.org/kafka/${version}/kafka-${version}-src.tgz"
source_tarball="kafka-${version}-src.tgz"
output_tarball="kafka-${version}-gradle-home.tar.xz"

if [[ ! -f "${source_tarball}" ]]; then
    echo "Downloading Kafka ${version} source..." >&2
    curl -fSL -o "${source_tarball}" "${source_url}"
fi

workdir=$(mktemp -d)
trap 'rm -rf "${workdir}"' EXIT

echo "Extracting source..." >&2
tar -xzf "${source_tarball}" -C "${workdir}"
srcdir="${workdir}/kafka-${version}-src"

export GRADLE_USER_HOME="${workdir}/gradle-home"
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk}"
mkdir -p "${GRADLE_USER_HOME}"

echo "Building Kafka to populate Gradle cache (this takes a while)..." >&2
(cd "${srcdir}" && ./gradlew --no-daemon clean releaseTarGz)

# Update bundled provides in spec from the release tarball
release_tarball="${srcdir}/core/build/distributions/kafka_2.13-${version}.tgz"
script_dir="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "${release_tarball}" && -x "${script_dir}/generate-bundled-provides.sh" ]]; then
    echo "Updating bundled provides in spec..." >&2
    "${script_dir}/generate-bundled-provides.sh" "${release_tarball}"
fi

echo "Pruning unnecessary files from Gradle home..." >&2
find "${GRADLE_USER_HOME}" -name '*.lock' -delete
rm -rf "${GRADLE_USER_HOME}/daemon"
rm -rf "${GRADLE_USER_HOME}/native"
rm -rf "${GRADLE_USER_HOME}/build-scan-data"
rm -rf "${GRADLE_USER_HOME}/notifications"
rm -rf "${GRADLE_USER_HOME}/workers"

echo "Packaging Gradle home..." >&2
tar -cJf "${output_tarball}" -C "${workdir}" gradle-home

echo "" >&2
echo "Created: ${output_tarball}" >&2
echo "" >&2
echo "Next steps:" >&2
echo "  1. Upload tarballs to lookaside cache:" >&2
echo "     ci/upload-to-lookaside-cache.sh -f ${source_tarball} -p kafka" >&2
echo "     ci/upload-to-lookaside-cache.sh -f ${output_tarball} -p kafka" >&2
echo "  2. Generate sources file:" >&2
echo "     sha512sum --tag ${source_tarball} ${output_tarball} > rpms/kafka/sources" >&2
