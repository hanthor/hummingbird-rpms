#!/usr/bin/env bash
set -euo pipefail

BRANCH="rawhide"
PKG=""

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch)
            if [[ $# -lt 2 ]]; then
                echo "Error: --branch requires a value"
                exit 1
            fi
            BRANCH="$2"
            shift 2
            ;;
        *)
            if [[ -z "${PKG}" ]]; then
                PKG="$1"
            else
                echo "Error: unexpected argument '$1'"
                exit 1
            fi
            shift
            ;;
    esac
done

if [[ -z "${PKG}" ]]; then
    echo "Usage: $0 [--branch <branch>] <package>"
    exit 1
fi

DEST="./rpms/${PKG}"
mkdir -p "${DEST}"

WORKDIR="/work"

# Build the container image
CONTAINER_IMAGE=$(podman build -q - <<EOF
FROM fedora:latest
RUN dnf install -y fedpkg git
EOF
)

# Extract command substitutions to avoid SC2312
RPMS_MOUNT="$(pwd)/rpms:${WORKDIR}:Z"
USER_ID=$(id -u)
GROUP_ID=$(id -g)

# Clone the package using the built image
podman run --rm \
    -v "${RPMS_MOUNT}" \
    -w "${WORKDIR}" \
    --userns=keep-id \
    --user "${USER_ID}:${GROUP_ID}" \
    "${CONTAINER_IMAGE}" \
    bash -c "
        fedpkg clone --anonymous --branch ${BRANCH} ${PKG} && \
        chown -R ${USER_ID}:${GROUP_ID} ${PKG}
    "

rm -rf "${DEST}/.git"
echo "Fedora dist-git for ${PKG} cloned into ${DEST}"
