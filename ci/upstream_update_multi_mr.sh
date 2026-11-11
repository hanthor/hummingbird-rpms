#!/bin/bash
# Creates individual MRs for each package that needs an upstream version update
# Uses check_upstream_versions.py to detect newer upstream releases and update
# spec files, then creates one MR per package for independent review.
#
# Without --create-mrs, this is always a dry-run: updates are applied locally
# but no MRs are created. Pass --create-mrs to create MRs. The method is
# auto-detected: when WORKQUEUE_SERVICE_URL and WORKQUEUE_OIDC_TOKEN are set,
# submissions go through the workqueue service; otherwise MRs are created via
# git push.
#
# Usage:
#   ./ci/upstream_update_multi_mr.sh                                          # Dry-run: check and apply updates locally
#   ./ci/upstream_update_multi_mr.sh --create-mrs                             # Create MRs (auto-detect method)
#   ./ci/upstream_update_multi_mr.sh --clone                                  # Clone to /tmp first, then dry-run
#   ./ci/upstream_update_multi_mr.sh --clone --create-mrs                     # Clone to /tmp, create MRs
#   ./ci/upstream_update_multi_mr.sh --max-updates=3 --create-mrs             # Create up to 3 MRs
#   ./ci/upstream_update_multi_mr.sh --only-package=curl                      # Check only curl
#
# Environment variables:
#   CHORE_MR_GITLAB_TOKEN    - GitLab token with api and write_repository scopes
#   GITLAB_REMOTE_URL        - GitLab repo URL (default: https://gitlab.com/redhat/hummingbird/rpms.git)
#   GITLAB_PROJECT_ID        - Numeric GitLab project ID fallback outside GitLab CI (optional)
#   WORKQUEUE_SERVICE_URL    - Workqueue service URL (enables workqueue method when set)
#   WORKQUEUE_OIDC_TOKEN     - GitLab CI OIDC token for workqueue auth
#
# Failed update processing creates a draft diagnostic MR. The checker rolls
# back partial package changes before the draft is created.
#
# When creating MRs via git push, packages whose chore/upstream-update-<pkg>
# branch already exists on the remote are checked but not rebuilt: create_mr.sh
# refuses to push an existing branch, so rebuilding would only spend the job's
# time budget to discard the result. Diagnostic chore/upstream-update-failure-*
# branches are not update branches, so those packages are still retried.

set -euo pipefail

# Parse arguments
CLONE_MODE=false
CREATE_MRS=false
MAX_UPDATES=0   # 0 means unlimited
ONLY_PACKAGE=""  # Empty means check all tracked packages
TEMP_DIR=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --clone)
            CLONE_MODE=true
            shift
            ;;
        --max-updates=*)
            MAX_UPDATES="${1#*=}"
            if ! [[ "${MAX_UPDATES}" =~ ^[0-9]+$ ]]; then
                echo "Error: --max-updates requires a number (e.g., --max-updates=10)"
                exit 1
            fi
            shift
            ;;
        --create-mrs)
            CREATE_MRS=true
            shift
            ;;
        --only-package=*)
            ONLY_PACKAGE="${1#*=}"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--clone] [--max-updates=N] [--create-mrs] [--only-package=NAME]"
            exit 1
            ;;
    esac
done

# Auto-detect MR creation method
if [[ "${CREATE_MRS}" == true ]]; then
    if [[ -n "${WORKQUEUE_SERVICE_URL:-}" && -n "${WORKQUEUE_OIDC_TOKEN:-}" ]]; then
        CREATE_METHOD=workqueue
    else
        CREATE_METHOD=git-push
    fi
else
    CREATE_METHOD=dry-run
fi

# Configuration
TARGET_BRANCH=${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME:-${CI_COMMIT_BRANCH:-${CI_DEFAULT_BRANCH:-main}}}

cleanup_resolved_failure_mr() {
    local package=$1

    if [[ "${CREATE_METHOD}" == "dry-run" ]]; then
        return
    fi

    local token=${CHORE_MR_GITLAB_TOKEN:-}
    local project_id=${CI_PROJECT_ID:-${GITLAB_PROJECT_ID:-}}
    local api_url=${CI_API_V4_URL:-https://${GITLAB_HOST:-gitlab.com}/api/v4}
    local branch_name="chore/upstream-update-failure-${package}"
    local expected_title="Draft: investigate failed upstream update for ${package}"

    if [[ -z "${token}" || -z "${project_id}" ]]; then
        echo "WARNING: Cannot clean up resolved diagnostic MR for ${package}: " \
            "CHORE_MR_GITLAB_TOKEN and CI_PROJECT_ID (or GITLAB_PROJECT_ID) are required" >&2
        return
    fi

    local open_mrs
    if ! open_mrs=$(curl --silent --show-error --fail-with-body \
        --header "PRIVATE-TOKEN: ${token}" \
        --get \
        --data-urlencode "source_branch=${branch_name}" \
        --data-urlencode "state=opened" \
        "${api_url}/projects/${project_id}/merge_requests"); then
        echo "WARNING: Failed to query resolved diagnostic MR for ${package}; leaving it open" >&2
        return
    fi

    local mr_iid
    if ! mr_iid=$(jq -r --arg title "${expected_title}" --arg project_id "${project_id}" \
        '[.[] | select(
            (.source_project_id | tostring) == $project_id
            and .draft == true
            and .title == $title
        )][0].iid // empty' \
        <<<"${open_mrs}"); then
        echo "WARNING: Failed to parse diagnostic MR query for ${package}; leaving it open" >&2
        return
    fi
    if [[ -z "${mr_iid}" ]]; then
        return
    fi

    # Never close a diagnostic branch that a maintainer has started turning
    # into a real fix. Bot-created diagnostic MRs have an empty diff.
    local mr_diffs
    if ! mr_diffs=$(curl --silent --show-error --fail-with-body \
        --header "PRIVATE-TOKEN: ${token}" \
        "${api_url}/projects/${project_id}/merge_requests/${mr_iid}/diffs"); then
        echo "WARNING: Failed to inspect diagnostic MR !${mr_iid} for ${package}; leaving it open" >&2
        return
    fi

    local diff_count
    if ! diff_count=$(jq -er \
        'if type == "array" then length else error("expected diff array") end' \
        <<<"${mr_diffs}"); then
        echo "WARNING: Failed to parse diagnostic MR !${mr_iid} diffs for ${package}; leaving it open" >&2
        return
    fi
    if [[ ${diff_count} -ne 0 ]]; then
        echo "WARNING: Resolved diagnostic MR !${mr_iid} for ${package} has changes; leaving it open" >&2
        return
    fi

    if ! curl --silent --show-error --fail-with-body --output /dev/null \
        --request PUT \
        --header "PRIVATE-TOKEN: ${token}" \
        --data-urlencode "state_event=close" \
        "${api_url}/projects/${project_id}/merge_requests/${mr_iid}"; then
        echo "WARNING: Failed to close resolved diagnostic MR !${mr_iid} for ${package}" >&2
        return
    fi

    local encoded_branch
    encoded_branch=$(jq -rn --arg branch "${branch_name}" '$branch | @uri')
    if ! curl --silent --show-error --fail-with-body --output /dev/null \
        --request DELETE \
        --header "PRIVATE-TOKEN: ${token}" \
        "${api_url}/projects/${project_id}/repository/branches/${encoded_branch}"; then
        echo "WARNING: Closed resolved diagnostic MR !${mr_iid} for ${package}, " \
            "but failed to delete ${branch_name}" >&2
        return
    fi

    echo "Closed resolved diagnostic MR !${mr_iid} and deleted ${branch_name}"
}

# Cleanup function for clone mode
# shellcheck disable=SC2329  # Function is invoked via EXIT trap
cleanup() {
    if [[ -n "${TEMP_DIR}" && -d "${TEMP_DIR}" ]]; then
        echo ""
        echo "Temporary directory preserved at: ${TEMP_DIR}"
        echo "You can inspect it or delete it manually when done."
    fi
}

# ── Phase 1: Setup ──────────────────────────────────────────────────

if [[ "${CLONE_MODE}" == true ]]; then
    trap cleanup EXIT

    TEMP_DIR=$(mktemp -d /tmp/upstream-update-clone.XXXXXX)
    echo "========================================"
    echo "CLONE MODE - Working in temporary directory"
    echo "========================================"
    echo "Temp dir: ${TEMP_DIR}"
    echo ""

    # Use configurable remote URL, defaulting to the upstream canonical repo
    GITLAB_REMOTE_URL="${GITLAB_REMOTE_URL:-https://gitlab.com/redhat/hummingbird/rpms.git}"

    # Extract host and project path for constructing MR URLs later
    if [[ "${GITLAB_REMOTE_URL}" =~ ^https://([^/]+)/(.+)\.git$ ]]; then
        GITLAB_HOST="${BASH_REMATCH[1]}"
        GITLAB_PROJECT="${BASH_REMATCH[2]}"
    elif [[ "${GITLAB_REMOTE_URL}" =~ ^https://([^/]+)/(.+)$ ]]; then
        GITLAB_HOST="${BASH_REMATCH[1]}"
        GITLAB_PROJECT="${BASH_REMATCH[2]%.git}"
    fi

    echo "Cloning repository from GitLab..."
    git clone --quiet --depth 1 --single-branch --branch "${TARGET_BRANCH}" "${GITLAB_REMOTE_URL}" "${TEMP_DIR}"
    cd "${TEMP_DIR}"

    # Configure git for the cloned repo
    git config user.name "Upstream Update (Clone Mode)"
    git config user.email "noreply@example.com"

    # In clone mode with --create-mrs via git-push, configure credentials for pushing
    if [[ "${CREATE_METHOD}" == "git-push" ]]; then
        if [[ -z "${CHORE_MR_GITLAB_TOKEN:-}" ]]; then
            echo "ERROR: CHORE_MR_GITLAB_TOKEN must be set when using --create-mrs in clone mode"
            echo "       Token must have write_repository scope"
            exit 1
        fi

        git config credential.username ignored
        git config credential.helper "!echo \"password=\$CHORE_MR_GITLAB_TOKEN\"; :"

        echo "Configured GitLab authentication for pushing"
    fi

    echo "Repository cloned from: ${GITLAB_REMOTE_URL}"
    echo "Working directory: ${TEMP_DIR}"
    echo ""
else
    cd "$(dirname "${BASH_SOURCE[0]}")/.."

    # Extract GitLab info for MR URL tracking
    GITLAB_REMOTE_URL="${GITLAB_REMOTE_URL:-https://gitlab.com/redhat/hummingbird/rpms.git}"
    if [[ "${GITLAB_REMOTE_URL}" =~ ^https://([^/]+)/(.+)\.git$ ]]; then
        GITLAB_HOST="${BASH_REMATCH[1]}"
        GITLAB_PROJECT="${BASH_REMATCH[2]}"
    elif [[ "${GITLAB_REMOTE_URL}" =~ ^https://([^/]+)/(.+)$ ]]; then
        GITLAB_HOST="${BASH_REMATCH[1]}"
        GITLAB_PROJECT="${BASH_REMATCH[2]%.git}"
    fi
fi

# Ensure git is configured (for both clone and production mode)
if [[ -z "$(git config user.name 2>/dev/null || true)" ]]; then
    if [[ -n "${GITLAB_USER_NAME:-}" ]]; then
        git config user.name "${GITLAB_USER_NAME}"
    elif [[ "${CLONE_MODE}" == false ]]; then
        echo "Warning: git user.name not configured. Commits may fail."
    fi
fi

if [[ -z "$(git config user.email 2>/dev/null || true)" ]]; then
    if [[ -n "${GITLAB_USER_EMAIL:-}" ]]; then
        git config user.email "${GITLAB_USER_EMAIL}"
    elif [[ "${CLONE_MODE}" == false ]]; then
        echo "Warning: git user.email not configured. Commits may fail."
    fi
fi

# Statistics tracking
PACKAGES_UPDATED=0
MR_FAILURES=0
EXISTING_UPDATES=0
CHECK_FAILURES=0
CHECK_WARNINGS=0
CHECK_WARNING_PACKAGES=0
FAILED_PACKAGES=()
CREATED_MR_URLS=()
DRAFT_FAILURE_MR_URLS=()
# Package-level checker failures are written here for the shared scheduled-job
# Slack notification.  Keep this separate from .ci_conflict_mrs.txt: upstream
# updates do not create a conflict MR when Gorget or source processing fails.
UPSTREAM_FAILURES_FILE=".ci_upstream_update_failures.txt"
rm -f "${UPSTREAM_FAILURES_FILE}"
# Unpublished sources and missing tracked release lines are warnings, not
# checker failures. Detailed warnings remain in the checker output; this
# file carries only aggregate counts for Slack.
UPSTREAM_WARNINGS_FILE=".ci_upstream_update_warnings.txt"
rm -f "${UPSTREAM_WARNINGS_FILE}"
# Keep draft-MR links separately so the scheduled Slack notification can
# direct maintainers to the corresponding investigation branch.
UPSTREAM_DRAFT_FAILURE_MRS_FILE=".ci_upstream_update_draft_mrs.txt"
rm -f "${UPSTREAM_DRAFT_FAILURE_MRS_FILE}"

echo "========================================"
echo "Upstream Version Multi-MR Update"
echo "========================================"
if [[ "${CREATE_METHOD}" == "workqueue" ]]; then
    echo "Mode: WORKQUEUE (${WORKQUEUE_SERVICE_URL})"
elif [[ "${CREATE_METHOD}" == "git-push" ]]; then
    echo "Mode: GIT-PUSH (MRs will be created)"
else
    echo "Mode: DRY-RUN (no MRs will be created)"
fi
if [[ "${CLONE_MODE}" == true ]]; then
    echo "Clone: ${TEMP_DIR}"
fi
echo "Target branch: ${TARGET_BRANCH}"
if [[ -n "${ONLY_PACKAGE}" ]]; then
    echo "Package filter: ${ONLY_PACKAGE}"
fi
if [[ ${MAX_UPDATES} -gt 0 ]]; then
    echo "Max updates: ${MAX_UPDATES}"
fi
echo ""

# Merge request pipelines already have the source commit checked out. Keep that
# checkout so manual jobs exercise the code under review rather than switching
# back to the default branch.
if [[ -z "${CI_MERGE_REQUEST_IID:-}" ]]; then
    git checkout --quiet "${TARGET_BRANCH}" 2>/dev/null || true
fi

# Save the current commit before running updates (used by git-push path)
START_COMMIT=$(git rev-parse HEAD)

# create_mr.sh refuses to push a branch that already exists on the remote, so
# rebuilding an update whose MR branch already exists only spends the job's
# time budget to discard the result.  Skip those packages' builds.  Diagnostic
# failure branches (chore/upstream-update-failure-<pkg>) are not update
# branches; a package that has one should still be retried.
SKIP_PACKAGE_ARGS=()
if [[ "${CREATE_METHOD}" == "git-push" ]]; then
    if EXISTING_UPDATE_REFS=$(git ls-remote --heads "${REMOTE:-origin}" \
        'refs/heads/chore/upstream-update-*' 2>/dev/null); then
        while read -r _ ref; do
            [[ -z "${ref:-}" ]] && continue
            package="${ref#refs/heads/chore/upstream-update-}"
            if [[ "${package}" == failure-* ]]; then
                continue
            fi
            SKIP_PACKAGE_ARGS+=(--skip-package "${package}")
        done <<<"${EXISTING_UPDATE_REFS}"
    else
        echo "WARNING: could not list existing update branches; rebuilding every package" >&2
    fi
fi

# ── Phase 2: Run upstream version check ─────────────────────────────

# Request JSON so this wrapper can reliably distinguish update failures from
# successful updates.  The checker exits 1 for both cases when at least one
# update is available, so its exit status alone cannot drive the scheduled
# job's final status.
CHECK_ARGS=(./ci/check_upstream_versions.py check --update --sign-off --json)

if [[ "${CREATE_METHOD}" == "workqueue" ]]; then
    CHECK_ARGS+=(--workqueue)
    echo "Running check_upstream_versions.py check --update --workqueue --sign-off ..."
else
    echo "Running check_upstream_versions.py check --update --sign-off ..."
fi
echo ""

if [[ -n "${ONLY_PACKAGE}" ]]; then
    CHECK_ARGS+=("${ONLY_PACKAGE}")
fi
if [[ ${#SKIP_PACKAGE_ARGS[@]} -gt 0 ]]; then
    CHECK_ARGS+=("${SKIP_PACKAGE_ARGS[@]}")
fi

CHECK_OUTPUT=$(mktemp)
CHECK_EXIT_CODE=0
# Capture only stdout (JSON) for parsing; stderr remains visible in CI logs.
"${CHECK_ARGS[@]}" > "${CHECK_OUTPUT}" || CHECK_EXIT_CODE=$?

if ! CHECK_FAILURES=$(jq '[.results[] | select(.error != null or .update_error != null)] | length' "${CHECK_OUTPUT}"); then
    echo "ERROR: Could not parse check_upstream_versions.py JSON output"
    rm -f "${CHECK_OUTPUT}"
    exit 1
fi
if ! [[ "${CHECK_FAILURES}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: check_upstream_versions.py produced no parseable JSON output"
    rm -f "${CHECK_OUTPUT}"
    exit 1
fi
if ! CHECK_WARNINGS=$(jq '[.results[] | .warnings[]?] | length' "${CHECK_OUTPUT}"); then
    echo "ERROR: Could not parse upstream warnings"
    rm -f "${CHECK_OUTPUT}"
    exit 1
fi
if ! [[ "${CHECK_WARNINGS}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: check_upstream_versions.py produced no parseable warning count"
    rm -f "${CHECK_OUTPUT}"
    exit 1
fi
if ! CHECK_WARNING_PACKAGES=$(jq \
    '[.results[] | select((.warnings // []) | length > 0)] | length' \
    "${CHECK_OUTPUT}"); then
    echo "ERROR: Could not count packages with upstream warnings"
    rm -f "${CHECK_OUTPUT}"
    exit 1
fi
if ! [[ "${CHECK_WARNING_PACKAGES}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: check_upstream_versions.py produced no parseable warning package count"
    rm -f "${CHECK_OUTPUT}"
    exit 1
fi
if ! SKIPPED_UPDATES=$(jq '[.results[] | select(.skipped == true)] | length' "${CHECK_OUTPUT}"); then
    echo "ERROR: Could not parse skipped upstream updates"
    rm -f "${CHECK_OUTPUT}"
    exit 1
fi
if ! [[ "${SKIPPED_UPDATES}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: check_upstream_versions.py produced no parseable skipped update count"
    rm -f "${CHECK_OUTPUT}"
    exit 1
fi

# Preserve a short, one-line-per-package failure summary for both the job log
# and the scheduled-job Slack notification. The full error (which can contain
# Gorget JSON and remediation guidance) remains in the job log linked from Slack.
if ! jq -r '
    .results[]
    | select(.error != null or .update_error != null)
    | "\(.package): \((.update_error // .error) | split("\n")[0])"
' "${CHECK_OUTPUT}" > "${UPSTREAM_FAILURES_FILE}"; then
    echo "ERROR: Could not create upstream update failure summary"
    rm -f "${CHECK_OUTPUT}" "${UPSTREAM_FAILURES_FILE}"
    exit 1
fi
if [[ ! -s "${UPSTREAM_FAILURES_FILE}" ]]; then
    rm -f "${UPSTREAM_FAILURES_FILE}"
fi

if [[ ${CHECK_WARNINGS} -gt 0 ]]; then
    # Avoid flooding Slack when several upstream warnings exist for each
    # package. The checker stderr and JSON above retain every detailed warning.
    printf '%s upstream warning(s) across %s package(s); see job log for details\n' \
        "${CHECK_WARNINGS}" "${CHECK_WARNING_PACKAGES}" > "${UPSTREAM_WARNINGS_FILE}"
else
    rm -f "${UPSTREAM_WARNINGS_FILE}"
fi

# Keep update-processing failures separate from version-discovery failures.
# Only the former have a candidate version and a source-processing error that
# a maintainer can act on in a draft MR.
if ! UPDATE_FAILURE_RECORDS_TEXT=$(jq -r \
    '.results[] | select(.update_error != null) | @base64' "${CHECK_OUTPUT}"); then
    echo "ERROR: Could not parse failed upstream update records"
    rm -f "${CHECK_OUTPUT}" "${UPSTREAM_FAILURES_FILE}"
    exit 1
fi
UPDATE_FAILURE_RECORDS=()
if [[ -n "${UPDATE_FAILURE_RECORDS_TEXT}" ]]; then
    mapfile -t UPDATE_FAILURE_RECORDS <<<"${UPDATE_FAILURE_RECORDS_TEXT}"
fi

# Save the update commits before creating draft diagnostic branches below.
# The draft branches are intentionally based on START_COMMIT and would
# otherwise hide successfully created update commits from Phase 3.
UPDATE_COMMIT_HEAD=$(git rev-parse HEAD)
COMMITS_CREATED=$(git rev-list --count "${START_COMMIT}..${UPDATE_COMMIT_HEAD}" 2>/dev/null || echo "0")
# shellcheck disable=SC2312  # Command substitution in mapfile is intentional
mapfile -t COMMIT_SHAS < <(git rev-list --reverse "${START_COMMIT}..${UPDATE_COMMIT_HEAD}")

UPDATES_APPLIED=$(jq '[.results[] | select(.updated == true)] | length' "${CHECK_OUTPUT}")
# shellcheck disable=SC2312  # Command substitution in mapfile is intentional
mapfile -t SUCCESSFUL_PACKAGES < <(
    jq -r '.results[] | select(.updated == true) | .package' "${CHECK_OUTPUT}"
)
# shellcheck disable=SC2312  # Command substitution in mapfile is intentional
mapfile -t SKIPPED_PACKAGES < <(
    jq -r '.results[] | select(.skipped == true) | .package' "${CHECK_OUTPUT}"
)

jq . "${CHECK_OUTPUT}"
rm -f "${CHECK_OUTPUT}"

if [[ "${CREATE_METHOD}" == "workqueue" ]]; then
    for PACKAGE in "${SUCCESSFUL_PACKAGES[@]}"; do
        cleanup_resolved_failure_mr "${PACKAGE}"
    done
fi

echo ""
if [[ -s "${UPSTREAM_FAILURES_FILE}" ]]; then
    echo "Upstream package update/check failures:"
    while IFS= read -r failure; do
        echo "  - ${failure}"
    done < "${UPSTREAM_FAILURES_FILE}"
    echo ""
fi
if [[ -s "${UPSTREAM_WARNINGS_FILE}" ]]; then
    echo "Upstream warnings:"
    while IFS= read -r warning; do
        echo "  - ${warning}"
    done < "${UPSTREAM_WARNINGS_FILE}"
    echo ""
fi
if [[ ${SKIPPED_UPDATES} -gt 0 ]]; then
    echo "Updates skipped because an update MR branch already exists:"
    for SKIPPED_PACKAGE in "${SKIPPED_PACKAGES[@]}"; do
        echo "  - ${SKIPPED_PACKAGE}"
    done
    echo ""
fi

# ── Phase 2b: Create draft MRs for failed package updates ───────────

for FAILURE_RECORD in "${UPDATE_FAILURE_RECORDS[@]}"; do
    FAILURE_JSON=$(printf '%s' "${FAILURE_RECORD}" | base64 --decode)
    PACKAGE=$(jq -r '.package' <<<"${FAILURE_JSON}")
    CURRENT_VERSION=$(jq -r '.current_version // "unknown"' <<<"${FAILURE_JSON}")
    UPSTREAM_VERSION=$(jq -r '.upstream_version // "unknown"' <<<"${FAILURE_JSON}")
    # Push options are newline-delimited. Keep multi-line errors intact for
    # GitLab by encoding their newlines as the literal \n sequence, as used
    # by create_mr.sh descriptions elsewhere.
    # A gorget report can contain thousands of repeated Go module errors.
    # GitLab receives the description as a Git push option, whose protocol
    # packet cannot hold an unbounded report. Keep a useful opening excerpt;
    # the scheduled job log retains the complete error.
    # Escaping never shortens the string. Keep one character beyond the output
    # limit so truncation is still detected when the input has no newlines.
    UPDATE_ERROR=$(jq -r '
        ((.update_error // .error // "unknown error")[:4001] | gsub("\n"; "\\n")) as $error
        | if ($error | length) > 4000
          then $error[:4000] + "\\n...[truncated; see full CI job log]"
          else $error
          end
    ' <<<"${FAILURE_JSON}")
    BRANCH_NAME="chore/upstream-update-failure-${PACKAGE}"
    MR_TITLE="Draft: investigate failed upstream update for ${PACKAGE}"
    MR_DESCRIPTION="Automated upstream update from ${CURRENT_VERSION} to ${UPSTREAM_VERSION} failed.\n\nError:\n\`\`\`\n${UPDATE_ERROR}\n\`\`\`\n\nThis diagnostic MR intentionally contains no partial package changes. The updater discarded them after the failure to avoid proposing an unsafe update. Reproduce the update from this branch, apply and validate the required fix, then replace this empty commit with the resolved package changes."
    if [[ -n "${CI_JOB_URL:-}" ]]; then
        MR_DESCRIPTION+="\n\nFull job log: ${CI_JOB_URL}"
    fi

    echo "----------------------------------------"
    echo "Processing failed update: ${PACKAGE}"
    echo "  ${CURRENT_VERSION} -> ${UPSTREAM_VERSION}"

    if ! git checkout --quiet "${START_COMMIT}"; then
        echo "  Failed to checkout starting commit"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (draft MR checkout failed)")
        continue
    fi

    git branch -D "${BRANCH_NAME}" 2>/dev/null || true
    if ! git checkout -b "${BRANCH_NAME}" 2>/dev/null; then
        echo "  Failed to create draft MR branch ${BRANCH_NAME}"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (draft MR branch creation failed)")
        continue
    fi

    if ! git commit --allow-empty -m "Draft: investigate failed upstream update for ${PACKAGE}" >/dev/null; then
        echo "  Failed to create diagnostic commit for ${PACKAGE}"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (draft MR commit failed)")
        continue
    fi

    if [[ "${CREATE_METHOD}" == "dry-run" ]]; then
        echo "  [DRY-RUN] Draft MR would be created for ${PACKAGE}"
        continue
    fi

    MR_EXIT_CODE=0
    MR_OUTPUT=$(./ci/create_mr.sh --branch "${BRANCH_NAME}" --title "${MR_TITLE}" \
        --description "${MR_DESCRIPTION}" --draft --jira HUM-6984 2>&1) || MR_EXIT_CODE=$?
    echo "${MR_OUTPUT}"

    MR_URL="https://${GITLAB_HOST}/${GITLAB_PROJECT}/-/merge_requests?source_branch=${BRANCH_NAME}"
    if [[ ${MR_EXIT_CODE} -eq 0 ]]; then
        echo "  Created draft failure MR for ${PACKAGE}"
        DRAFT_FAILURE_MR_URLS+=("${PACKAGE}: ${MR_URL}")
        echo "${PACKAGE}: ${MR_URL}" >> "${UPSTREAM_DRAFT_FAILURE_MRS_FILE}"
    elif [[ ${MR_EXIT_CODE} -eq 2 ]]; then
        echo "  Draft failure MR already exists for ${PACKAGE}"
        DRAFT_FAILURE_MR_URLS+=("${PACKAGE}: ${MR_URL}")
        echo "${PACKAGE}: ${MR_URL}" >> "${UPSTREAM_DRAFT_FAILURE_MRS_FILE}"
    else
        echo "  Failed to create draft failure MR for ${PACKAGE}"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (draft MR creation failed)")
    fi
done

# Exit codes from check_upstream_versions.py:
#   0 = no updates available
#   1 = updates found and applied
#   2 = errors occurred
if [[ ${CHECK_EXIT_CODE} -eq 0 ]]; then
    if [[ ${CHECK_WARNINGS} -gt 0 ]]; then
        echo "No updates available; ${CHECK_WARNINGS} upstream warning(s) reported."
    else
        echo "No upstream version updates available. All tracked packages are up-to-date."
    fi
    exit 0
elif [[ ${CHECK_EXIT_CODE} -eq 2 ]]; then
    echo "Warning: check_upstream_versions.py reported errors (exit code 2)"
    echo "Continuing to process any updates that were applied..."
    echo ""
fi

# ── Workqueue path: submission already happened in Python ───────────

if [[ "${CREATE_METHOD}" == "workqueue" ]]; then
    echo "Workqueue: ${UPDATES_APPLIED} submitted, ${CHECK_FAILURES} failure(s), ${CHECK_WARNINGS} warning(s)"
    if [[ ${CHECK_FAILURES} -gt 0 ]]; then
        exit 1
    fi
    exit 0
fi

# ── Phase 3: Collect commits, create MRs (git-push path) ───────────

if [[ ${COMMITS_CREATED} -eq 0 ]]; then
    if [[ ${SKIPPED_UPDATES} -gt 0 && ${CHECK_FAILURES} -eq 0 ]]; then
        echo "No commits were created: all ${SKIPPED_UPDATES} available update(s) already have an MR."
        exit 0
    fi
    echo "No commits were created despite updates being reported."
    echo "This may indicate all updates failed."
    echo "  Update/check failures: ${CHECK_FAILURES}"
    exit 1
fi

echo "Found ${COMMITS_CREATED} upstream update commit(s)"
echo ""

# Apply --max-updates limit by truncating the commit array
if [[ ${MAX_UPDATES} -gt 0 && ${#COMMIT_SHAS[@]} -gt ${MAX_UPDATES} ]]; then
    echo "Limiting to ${MAX_UPDATES} updates (--max-updates=${MAX_UPDATES})"
    COMMIT_SHAS=("${COMMIT_SHAS[@]:0:${MAX_UPDATES}}")
    echo ""
fi

# Return to the known base without moving the most recent diagnostic branch.
# Each loop iteration below creates its package branch from this commit.
git checkout --quiet "${START_COMMIT}"

# Process each commit to create individual MRs
for COMMIT_SHA in "${COMMIT_SHAS[@]}"; do
    # Get commit message first line and extract package name + version
    COMMIT_MSG=$(git log -1 --format=%s "${COMMIT_SHA}")

    # Extract package name and version from commit message
    # Keep the optional prefix in sync with _STALE_PATCH_PREFIX in check_upstream_versions.py.
    # Format: "Update <package> to <version>" (optionally prefixed for stale patches)
    if [[ ${COMMIT_MSG} =~ ^(STALE\ PATCH:\ )?Update\ ([^\ ]+)\ to\ (.+)$ ]]; then
        STALE_PATCH="${BASH_REMATCH[1]}"
        PACKAGE="${BASH_REMATCH[2]}"
        VERSION="${BASH_REMATCH[3]}"
    else
        echo "----------------------------------------"
        echo "Skipping commit ${COMMIT_SHA:0:8}: cannot parse from: ${COMMIT_MSG}"
        continue
    fi

    echo "----------------------------------------"
    echo "Processing: ${PACKAGE}"
    echo "  Commit: ${COMMIT_SHA:0:8}"
    echo "  Message: ${COMMIT_MSG}"

    # Create package-specific branch
    BRANCH_NAME="chore/upstream-update-${PACKAGE}"
    MR_TITLE="${STALE_PATCH}chore(rpms): Update ${PACKAGE} to ${VERSION}"

    # Create a new branch from START_COMMIT and cherry-pick this commit
    if ! git checkout --quiet "${START_COMMIT}"; then
        echo "  Failed to checkout starting commit"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (checkout failed)")
        continue
    fi

    # Delete branch if it exists, then create fresh
    git branch -D "${BRANCH_NAME}" 2>/dev/null || true

    if ! git checkout -b "${BRANCH_NAME}" 2>/dev/null; then
        echo "  Failed to create branch ${BRANCH_NAME}"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (branch creation failed)")
        continue
    fi

    # Cherry-pick the specific commit
    if ! git cherry-pick "${COMMIT_SHA}" >/dev/null 2>&1; then
        echo "  Failed to cherry-pick commit for ${PACKAGE}"
        MR_FAILURES=$((MR_FAILURES + 1))
        FAILED_PACKAGES+=("${PACKAGE} (cherry-pick failed)")
        git cherry-pick --abort 2>/dev/null || true
        continue
    fi

    if [[ "${CREATE_METHOD}" == "dry-run" ]]; then
        # Dry-run: show what would be created
        echo "  Would create MR:"
        echo "      Branch: ${BRANCH_NAME}"
        echo "      Title: ${MR_TITLE}"
        echo "  [DRY-RUN] MR would be created for ${PACKAGE}"
        PACKAGES_UPDATED=$((PACKAGES_UPDATED + 1))
    else
        # --create-mrs: actually create the MR
        echo "  Creating MR: ${MR_TITLE}"

        MR_ARGS=(--branch "${BRANCH_NAME}" --title "${MR_TITLE}")
        if [[ -n "${STALE_PATCH}" ]]; then
            MR_ARGS+=(--draft --description "Exact upstreamed patches were removed; please review before merging.")
        else
            MR_ARGS+=(--auto-merge)
        fi

        # Create MR using existing create_mr.sh
        # Exit codes: 0 = created, 2 = already exists, 1 = failure
        MR_EXIT_CODE=0
        ./ci/create_mr.sh "${MR_ARGS[@]}" || MR_EXIT_CODE=$?

        if [[ ${MR_EXIT_CODE} -eq 0 ]]; then
            echo "  Created MR for ${PACKAGE}"
            PACKAGES_UPDATED=$((PACKAGES_UPDATED + 1))

            # Track MR URL if we have GitLab info
            if [[ -n "${GITLAB_HOST:-}" && -n "${GITLAB_PROJECT:-}" ]]; then
                MR_URL="https://${GITLAB_HOST}/${GITLAB_PROJECT}/-/merge_requests?source_branch=${BRANCH_NAME}"
                CREATED_MR_URLS+=("${MR_URL}")
            fi
        elif [[ ${MR_EXIT_CODE} -eq 2 ]]; then
            # MR already exists
            EXISTING_UPDATES=$((EXISTING_UPDATES + 1))
        else
            echo "  Failed to create MR for ${PACKAGE}"
            MR_FAILURES=$((MR_FAILURES + 1))
            FAILED_PACKAGES+=("${PACKAGE} (MR creation failed)")
        fi

        # A local checker commit is not enough to consider the diagnostic MR
        # resolved. Wait until create_mr.sh has created or found the replacement.
        if [[ ${MR_EXIT_CODE} -eq 0 || ${MR_EXIT_CODE} -eq 2 ]]; then
            cleanup_resolved_failure_mr "${PACKAGE}"
        fi
    fi
done

# Return to TARGET_BRANCH
git checkout --quiet "${TARGET_BRANCH}" 2>/dev/null || true

# ── Phase 4: Summary ───────────────────────────────────────────────

echo ""
echo "========================================"
echo "Summary"
echo "========================================"
if [[ "${CREATE_METHOD}" == "dry-run" ]]; then
    echo "  Mode: DRY-RUN"
    echo "  Updates found:           ${PACKAGES_UPDATED}"
else
    echo "  Mode: GIT-PUSH"
    echo "  MRs created:             ${PACKAGES_UPDATED}"
fi
echo "  Commits found:           ${COMMITS_CREATED}"
echo "  Commits processed:       ${#COMMIT_SHAS[@]}"
echo "  Skipped (existing MR):   ${SKIPPED_UPDATES}"
echo "  Existing updates:        ${EXISTING_UPDATES}"
echo "  Update/check failures:   ${CHECK_FAILURES}"
echo "  Upstream warnings:       ${CHECK_WARNINGS}"
echo "  Draft failure MRs:       ${#DRAFT_FAILURE_MR_URLS[@]}"
echo "  MR failures:             ${MR_FAILURES}"

if [[ ${#FAILED_PACKAGES[@]} -gt 0 ]]; then
    echo ""
    echo "Failed packages:"
    for failed_pkg in "${FAILED_PACKAGES[@]}"; do
        echo "  - ${failed_pkg}"
    done
fi

if [[ ${#DRAFT_FAILURE_MR_URLS[@]} -gt 0 ]]; then
    echo ""
    echo "Draft MRs for failed upstream updates:"
    for mr_url in "${DRAFT_FAILURE_MR_URLS[@]}"; do
        echo "  - ${mr_url}"
    done
fi

if [[ ${#CREATED_MR_URLS[@]} -gt 0 ]]; then
    echo ""
    echo "Created MR URLs:"
    for mr_url in "${CREATED_MR_URLS[@]}"; do
        echo "  - ${mr_url}"
    done
fi
echo "========================================"

# Exit with error if the version checker or MR creation found failures.  This
# comes after successful MRs are processed so one bad package does not block
# the remaining updates, but it lets scheduled CI and its Slack hook surface
# the failures.
TOTAL_FAILURES=$((CHECK_FAILURES + MR_FAILURES))
if [[ ${TOTAL_FAILURES} -gt 0 ]]; then
    echo ""
    echo "Exiting with error due to ${TOTAL_FAILURES} failure(s)"
    exit 1
fi

echo ""
if [[ "${CREATE_METHOD}" == "dry-run" ]]; then
    echo "Dry-run completed successfully!"
else
    echo "All upstream updates processed successfully!"
fi
exit 0
