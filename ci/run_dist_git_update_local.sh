#!/bin/bash
# Run the scheduled dist-git update locally using the GitLab CI image.
#
# This wrapper always uses clone mode and never passes --create-mrs: it runs
# every package update in a disposable clone and uploads successful Gorget
# source artifacts to lookaside, but creates neither remote branches nor MRs.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./ci/run_dist_git_update_local.sh --profile PROFILE [--image IMAGE] [--log-file PATH]

Run all dist-git package updates in a disposable clone inside the GitLab CI
image. Successful Gorget source artifacts are uploaded to lookaside, but no
merge requests or remote branches are created.

For private Hummingbird source repositories, export
HUMMINGBIRD_SRC_GITLAB_TOKEN in the calling shell. The token is forwarded to
the container without appearing in this command's arguments or log output.

Options:
  --profile PROFILE  AWS profile with lookaside upload access (required)
  --image IMAGE      GitLab CI image (default: quay.io/hummingbird-ci/gitlab-ci:latest)
  --log-file PATH    Write combined output to PATH (default: /tmp/dist-git-update-local-<timestamp>.log)
  -h, --help         Show this help
EOF
}

AWS_PROFILE_NAME=""
GITLAB_CI_IMAGE="quay.io/hummingbird-ci/gitlab-ci:latest"
LOG_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)
            [[ $# -ge 2 ]] || { echo "Error: --profile requires a value" >&2; exit 1; }
            AWS_PROFILE_NAME="$2"
            shift 2
            ;;
        --image)
            [[ $# -ge 2 ]] || { echo "Error: --image requires a value" >&2; exit 1; }
            GITLAB_CI_IMAGE="$2"
            shift 2
            ;;
        --log-file)
            [[ $# -ge 2 ]] || { echo "Error: --log-file requires a value" >&2; exit 1; }
            LOG_FILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ -z "${AWS_PROFILE_NAME}" ]]; then
    echo "Error: --profile is required" >&2
    usage >&2
    exit 1
fi

AWS_CONFIG_DIR="${HOME}/.aws"
LOCAL_USER_ID=$(id -u)
LOCAL_GROUP_ID=$(id -g)
if [[ -z "${LOG_FILE}" ]]; then
    LOG_FILE="/tmp/dist-git-update-local-$(date +%Y%m%d-%H%M%S).log"
fi
LOG_DIRECTORY=$(dirname "${LOG_FILE}")
if [[ ! -d "${LOG_DIRECTORY}" || ! -w "${LOG_DIRECTORY}" ]]; then
    echo "Error: log directory is not writable: ${LOG_DIRECTORY}" >&2
    exit 1
fi

if [[ ! -r "${AWS_CONFIG_DIR}/credentials" ]]; then
    echo "Error: AWS credentials file is not readable: ${AWS_CONFIG_DIR}/credentials" >&2
    echo "Log in first with: cki_aws_login --profile ${AWS_PROFILE_NAME} ..." >&2
    exit 1
fi

if ! aws --profile "${AWS_PROFILE_NAME}" sts get-caller-identity >/dev/null; then
    echo "Error: AWS profile ${AWS_PROFILE_NAME} cannot obtain an identity" >&2
    exit 1
fi

echo "Using image: ${GITLAB_CI_IMAGE}"
echo "Using AWS profile: ${AWS_PROFILE_NAME}"
echo "Mode: local clone; lookaside uploads enabled; MR creation disabled"
echo "Log file: ${LOG_FILE}"

CONTAINER_ENV_ARGS=()
if [[ -n "${HUMMINGBIRD_SRC_GITLAB_TOKEN:-}" ]]; then
    CONTAINER_ENV_ARGS+=(--env HUMMINGBIRD_SRC_GITLAB_TOKEN)
    echo "Private source GitLab token: available"
else
    echo "Private source GitLab token: not set"
fi

# :Z lets the confined container read the host's 0600 AWS config file. The
# repository itself is cloned in the container, preserving the host checkout.
podman run --rm -it --pull=always \
    --userns=keep-id \
    --user "${LOCAL_USER_ID}:${LOCAL_GROUP_ID}" \
    --volume "${AWS_CONFIG_DIR}:/aws:ro,Z" \
    --env AWS_SHARED_CREDENTIALS_FILE=/aws/credentials \
    --env AWS_CONFIG_FILE=/aws/config \
    --env AWS_PROFILE="${AWS_PROFILE_NAME}" \
    "${CONTAINER_ENV_ARGS[@]}" \
    --workdir /tmp \
    "${GITLAB_CI_IMAGE}" \
    bash -lc '
        set -euo pipefail
        rpm -q gorget
        aws sts get-caller-identity
        git clone --quiet --depth 1 --single-branch --branch main \
            https://gitlab.com/redhat/hummingbird/rpms.git /tmp/rpms
        cd /tmp/rpms
        bash ./ci/dist_git_update_multi_mr.sh --clone
    ' 2>&1 | tee "${LOG_FILE}"
