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
REPO_ROOT=${SCRIPT_DIR}/..

image=localhost/rpm-builder:latest
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

# Use .out directory as workdir for easier debugging
workdir="${OUT_DIR}/${package_name}"
mkdir -p "${workdir}"

cd "${workdir}"
mkdir -p results
mkdir -p config
mkdir -p sources

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

# Detect git directory location (handle worktrees)
if [[ -f "${REPO_ROOT}/.git" ]]; then
    # Git worktree - read the gitdir location
    gitdir=$(grep 'gitdir:' "${REPO_ROOT}/.git" | cut -d' ' -f2) || gitdir=""
    bare_repo=$(echo "${gitdir}" | sed 's|/worktrees/.*||')
else
    # Regular git repo
    bare_repo="${REPO_ROOT}/.git"
fi

podman run --rm -ti --privileged --init \
    --pids-limit=16384 \
    -u mockbuilder \
    -v "${workdir}/results:/results:z" \
    -v "${workdir}/config:/config:z" \
    -v "${workdir}/sources:/sources:z" \
    -v "${REPO_ROOT}:/repo:z" \
    -v "${bare_repo}:/bare:z" \
    "${image}" \
bash -c "
# Download sources using dist-git-client
# Copy package to writable location and set up .git for dist-git-client
cp -r /repo/rpms/${package_name} /tmp/package
cd /tmp/package

# dist-git-client needs a .git directory even with --forked-from (it runs git commands)
cp -r /bare .git

echo 'Downloading sources via dist-git-client...'
# Use --forked-from to tell dist-git-client to use Fedora's lookaside cache
dist-git-client --forked-from https://src.fedoraproject.org/rpms/${package_name}.git sources

# Copy all downloaded sources to /sources directory
echo 'Copying sources to /sources directory...'
cp -v * /sources/ 2>/dev/null || true

mock -r /config/mock.cfg \
     --spec '/repo/rpms/${package_name}/${package_name}.spec' \
     --sources /sources \
     --resultdir /results
"

# Organize RPMs into separate directories
mkdir -p "${workdir}/RPMS" "${workdir}/SRPMS"
mv -f "${workdir}"/results/*.src.rpm "${workdir}/SRPMS/" 2>/dev/null || true
mv -f "${workdir}"/results/*.rpm "${workdir}/RPMS/" 2>/dev/null || true

echo ""
echo "Binary RPMs:"
ls "${workdir}/RPMS"/*.rpm 2>/dev/null || echo "No binary RPMs found"

echo ""
echo "Source RPMs:"
ls "${workdir}/SRPMS"/*.src.rpm 2>/dev/null || echo "No source RPMs found"

echo ""
echo "Build outputs saved to: ${workdir}/"
