#! /bin/bash -e

# Build RPMs for a given package using mock in a containerized environment.
#
# This script:
# - Builds the RPM using mock in the upstream RPM Build Pipeline container
# - Outputs binary RPMs to .out/PACKAGE_NAME/RPMS/
# - Outputs source RPMs to .out/PACKAGE_NAME/SRPMS/
#
# Usage: ./ci/build_rpms.sh [OPTIONS] PACKAGE_NAME
#   PACKAGE_NAME - Name of the package directory in rpms/
#   --arch ARCH  - Target architecture (default: auto-detected via uname -m)
#
# The built RPMs can be found in: .out/PACKAGE_NAME/RPMS/ and .out/PACKAGE_NAME/SRPMS/
#
# Note: This performs a non-hermetic package build in the same container
# environment as used by Konflux, but is not a drop-in replacement for the
# full Tekton pipeline.
#
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
RPM_DIR=${SCRIPT_DIR}/../rpms
OUT_DIR=${SCRIPT_DIR}/../.out

image=localhost/rpm-build-with-spectool:latest
arch=x86_64

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --arch)
            arch="$2"
            shift 2
            ;;
        *)
            package_name="$1"
            shift
            ;;
    esac
done

if [[ -z "${package_name}" ]]; then
    echo "Error: PACKAGE_NAME is required"
    exit 1
fi

workdir=$(mktemp -d "/tmp/konflux-build-${package_name}-XXXXXXXX")

cd "${workdir}"
mkdir results
mkdir config
mkdir sources

# Build the container image
podman build -t "${image}" -f "${SCRIPT_DIR}/Containerfile" "${SCRIPT_DIR}"

# Allow mockbuilder (group mock) to read config and write results
podman unshare setfacl -m g:135:rwx -m default:g:135:rwx "results"
podman unshare setfacl -m g:135:rwx -m default:g:135:rwx "config"
podman unshare setfacl -m g:135:rwx -m default:g:135:rwx "sources"

# Copy local source files to sources directory
cp -f "${RPM_DIR}/${package_name}"/* "${workdir}/sources/" 2>/dev/null || true

# prepare mock config
sed "s|@ARCH@|${arch}|" "${SCRIPT_DIR}/../mock/mock.cfg" > "${workdir}/config/mock.cfg"

podman run --rm -ti --privileged --init \
    --pids-limit=16384 \
    -u root \
    -v "${workdir}/results:/results:z" \
    -v "${workdir}/config:/config:z" \
    -v "${workdir}/sources:/sources:z" \
    -v "${RPM_DIR}/${package_name}:/spec:z" \
    "${image}" \
bash -c "
# Download sources using spectool
cd /sources
echo 'Downloading sources via spectool...'
spectool -g --sources '/spec/${package_name}.spec'

# Run mock as mockbuilder
su - mockbuilder << 'EOF'
mock -r /config/mock.cfg \
     --spec '/spec/${package_name}.spec' \
     --sources /sources \
     --resultdir /results
EOF
"

# Create output directory structure
mkdir -p "${OUT_DIR}/${package_name}/RPMS"
mkdir -p "${OUT_DIR}/${package_name}/SRPMS"
mkdir -p "${OUT_DIR}/${package_name}/SOURCES"
mkdir -p "${OUT_DIR}/${package_name}/LOGS"

# Copy source RPMs to SRPMS directory
cp -f "${workdir}"/results/*.src.rpm "${OUT_DIR}/${package_name}/SRPMS/" 2>/dev/null || true

# Copy binary RPMs to RPMS directory (all RPMs except source RPMs)
find "${workdir}"/results -name "*.rpm" ! -name "*.src.rpm" -exec cp -f {} "${OUT_DIR}/${package_name}/RPMS/" \; 2>/dev/null || true

# Copy sources to output directory
cp -f "${workdir}"/sources/* "${OUT_DIR}/${package_name}/SOURCES/" 2>/dev/null || true

# Copy build logs to output directory
cp -f "${workdir}"/results/*.log "${OUT_DIR}/${package_name}/LOGS/" 2>/dev/null || true

echo ""
echo "Binary RPMs:"
ls "${OUT_DIR}/${package_name}/RPMS"/*.rpm 2>/dev/null || echo "No binary RPMs found"

echo ""
echo "Source RPMs:"
ls "${OUT_DIR}/${package_name}/SRPMS"/*.src.rpm 2>/dev/null || echo "No source RPMs found"

echo ""
echo "Build outputs saved to: ${OUT_DIR}/${package_name}/"
