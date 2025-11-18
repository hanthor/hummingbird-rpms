#!/bin/bash

# Run tests for RPM packages
# Use --help for detailed usage information

set -euo pipefail
shopt -s inherit_errexit

show_help() {
    cat << EOF
Usage: $0 [OPTIONS] PACKAGE_NAME

Run tests for RPM packages.

OPTIONS:
    --repo DIR            Directory containing RPM files to use as local repository
                          (required) Will find and test PACKAGE_NAME in this directory
                          All RPMs in directory are added to the repository
    --src-rpm PATH        Path to source RPM file (optional, enables source RPM tests)
    --verbose, -v         Enable verbose output during testing
    --help, -h            Show this help message

ARGUMENTS:
    PACKAGE_NAME          Name of the RPM package (must correspond to a folder in rpms/)
                          Examples: rootfiles, setup, rpm

EXAMPLES:
    # Test RPM from a directory repository
    $0 --repo /path/to/rpms rpm

    # Test with source RPM
    $0 --repo /path/to/rpms --src-rpm /path/to/setup.src.rpm setup

    # Test with verbose output
    $0 --repo ./.out/rpm/RPMS --verbose setup

DESCRIPTION:
    Runs default tests for the specified RPM package. Tests are defined in
    ci/default-tests/tests-rpm.yml and package-specific tests in
    rpms/<package>/tests-rpm.yml (if present).

    All RPMs in the repository directory are added to a local repository,
    allowing tests to reference any RPM in the set. The PACKAGE_NAME is used to
    locate the primary RPM to test.
EOF
}

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
base_dir="$(dirname "${SCRIPT_DIR}")"

# Support running from any directory by converting relative paths to absolute
convert_path_to_absolute() {
    local path=$1
    if [[ "${path}" = /* ]]; then
        # Already absolute
        echo "${path}"
    else
        # Relative path - convert relative to current directory, not script location
        local dir_name
        local base_name
        dir_name=$(cd "${CURRENT_DIR}" && cd "$(dirname "${path}")" && pwd) || return 1
        base_name=$(basename "${path}")
        echo "${dir_name}/${base_name}"
    fi
}

# Store the directory from which the script was invoked
CURRENT_DIR="$(pwd)"

# Color codes for logging
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
LIGHT_CYAN='\033[1;36m'
NC='\033[0m' # No Color

log_heading() { printf "${LIGHT_CYAN}=== %s ===${NC}\n" "$*" >&2; }
log_pass() { printf "  ${GREEN}[PASS]${NC} %s\n" "$*" >&2; }
log_unexpected_pass() { printf "  ${RED}[UNEXPECTED PASS]${NC} %s\n" "$*" >&2; }
log_known_failure() { printf "  ${YELLOW}[KNOWN FAILURE]${NC} %s\n" "$*" >&2; }
log_fail() { printf "  ${RED}[UNKNOWN FAILURE]${NC} %s\n" "$*" >&2; }
log_info() { printf "  %s\n" "$*" >&2; }

# Convert YAML to JSON (simple Python-based converter)
yaml_to_json() {
    python3 -c 'import sys, yaml, json; json.dump(yaml.safe_load(sys.stdin), sys.stdout)'
}

# Check if test output matches any known issues
# Returns "<issue_url>\n<description> [fails: <frequency>]" if found, empty string otherwise
check_known_issues() {
    local known_issues_json=$1 test_output_file=$2 patterns

    if [[ ${known_issues_json} != "[]" ]]; then
        patterns=$(jq -r '
            .[] |
            if (type == "object" and (.pattern | type == "array"))
                then .pattern[]
                else .pattern
            end
        ' <<< "${known_issues_json}")
        while IFS= read -r pattern; do
            if grep -qE "${pattern}" "${test_output_file}" 2>/dev/null; then
                local issue description fails_value
                known_issue_json=$(jq -c --arg pattern "${pattern}" '
                    .[] | select(
                            (.pattern == $pattern) or
                            (.pattern | type == "array" and index($pattern) != null)
                    )
                ' <<< "${known_issues_json}" | head -n1)
                issue=$(jq -r '.issue // "no-issue-url"' <<< "${known_issue_json}")
                description=$(jq -r '.description' <<< "${known_issue_json}")
                fails_value=$(jq -r '.fails // "always"' <<< "${known_issue_json}")

                # Don't include null or empty issue URLs in output
                if [[ ${issue} == "null" || ${issue} == "no-issue-url" ]]; then
                    echo ""
                    echo "${description} [fails: ${fails_value}]"
                else
                    echo "${issue}"
                    echo "${description} [fails: ${fails_value}]"
                fi
                return 0
            fi
        done <<< "${patterns}"
    fi
}

# Determine package sources from environment JSON
# $1 = environment JSON
# $2 = test type ("binary" or "source")
# Returns: JSON map of package names to sources
determine_package_sources() {
    local environment=$1
    local test_type=$2

    jq -c '
        if .packages.local or .packages.remote then
            # New format with explicit local/remote
            ((.packages.local // []) | map({(.): "local"}) | add // {}) as $local |
            ((.packages.remote // []) | map({(.): "fedora"}) | add // {}) as $remote |
            $local + $remote
        elif .packages then
            # Old format: different handling for binary vs source tests
            [.packages[] | select(. != null)] as $pkgs |
            if $pkgs | length == 0 then
                {}
            elif env.test_type == "binary" then
                # Binary: first package from local, rest from fedora
                ($pkgs[0]) as $first |
                ($pkgs | map(select(. == $first) | {(.): "local"}) + map(select(. != $first) | {(.): "fedora"}) | add)
            else
                # Source: all packages from fedora
                $pkgs | map({(.): "fedora"}) | add
            end
        else {} end
    ' --arg test_type "${test_type}" <<< "${environment}"
}

# Setup and start a test container
# $1 = base_image
# Returns: container_id (echoed to stdout)
# Note: Cleanup is handled in run_test_command, not here
setup_test_container() {
    local base_image=$1
    local mount_args=("-v" "${REPO_PATH}:/test/rpms:ro,z")

    if [[ -n ${SRC_RPM_DIR} ]]; then
        mount_args+=("-v" "${SRC_RPM_DIR}:/test/srpms:ro,z")
    fi

    if [[ -n ${HOST_REPO_DIR} && -d ${HOST_REPO_DIR} ]]; then
        mount_args+=("-v" "${HOST_REPO_DIR}:/tmp/local-repo:ro,z")
    fi

    local container_id
    container_id=$("${TEST_ENGINE:?}" create "${mount_args[@]}" --entrypoint sleep "${base_image}" infinity)

    "${TEST_ENGINE}" start "${container_id}" >&2

    # Copy repo configuration file into container
    if [[ -f ${HOST_REPO_DIR}/local-repo.repo ]]; then
        "${TEST_ENGINE}" cp "${HOST_REPO_DIR}/local-repo.repo" "${container_id}:/etc/yum.repos.d/local-repo.repo" >&2
    fi

    echo "${container_id}"
}

# Run test command in container or locally
# $1 = command
# $2 = source_dir
# $3 = container_id (empty for local execution)
# $4 = packages (space-separated, optional)
# $5 = package_sources (JSON, optional)
# $6 = output_file
run_test_command() {
    local command=$1
    local source_dir=$2
    local container_id=$3
    local packages=$4
    local package_sources=$5
    local output_file=$6

    if [[ -n ${container_id} ]]; then
        # Run in container
        (
            [[ ${TEST_VERBOSE} == true ]] && set -x
            cd "${source_dir}"

            # Setup cleanup to remove container immediately (avoid 10s SIGTERM timeout)
            cleanup_container() {
                "${TEST_ENGINE}" rm -f "${container_id}" 2>/dev/null || true
            }
            trap cleanup_container EXIT

            # Install packages if needed
            if [[ -n "${packages}" ]]; then
                export TEST_PACKAGE_SOURCES="${package_sources}"
                # Split space-separated packages into array for proper argument passing
                read -ra packages_array <<< "${packages}"
                install_test_packages "${container_id}" "${packages_array[@]}"
            fi

            # Determine source RPM directory argument
            local src_rpm_dir_arg=""
            if [[ -n ${SRC_RPM_DIR} ]]; then
                src_rpm_dir_arg="/test/srpms"
            fi

            # Run test command
            "${TEST_ENGINE}" exec \
                --user 0 \
                -e HOME=/root \
                -e PACKAGE_NAME="${PACKAGE_NAME}" \
                -e TEST_RPMS_DIR="/test/rpms" \
                -e TEST_SRC_RPMS_DIR="${src_rpm_dir_arg}" \
                -e TEST_ENGINE="${TEST_ENGINE}" \
                -e TEST_IMAGE="${TEST_IMAGE}" \
                "${container_id}" \
                bash -c "${command}"

            # Explicit cleanup (trap is backup)
            cleanup_container
        ) &> "${output_file}" &
    else
        # Run locally
        (
            [[ ${TEST_VERBOSE} == true ]] && set -x
            cd "${source_dir}"
            export PACKAGE_NAME TEST_RPMS_DIR="${REPO_PATH}" TEST_SRC_RPMS_DIR="${SRC_RPM_DIR}" TEST_ENGINE TEST_IMAGE
            eval "${command}"
        ) &> "${output_file}" &
    fi
}

# Handle test result and update counters
# $1 = output_file
# $2 = known_issues JSON
# Variables updated: passed_tests, failed_tests, known_issue_tests, unexpected_pass_tests
# Array updated: known_issues_found
handle_test_result() {
    local output_file=$1
    local known_issues=$2

    if ! wait -n; then
        # Test failed
        local issue_result
        issue_result=$(check_known_issues "${known_issues}" "${output_file}")
        if [[ -n "${issue_result}" ]]; then
            local issue_line
            issue_line=$(tr '\n' ' ' <<< "${issue_result}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            log_known_failure "${issue_line}"
            known_issue_tests=$((known_issue_tests + 1))
            local issue_url
            issue_url=$(head -n1 <<< "${issue_result}")
            if [[ -n ${issue_url} && ${issue_url} != "null" ]]; then
                known_issues_found+=("${issue_url}")
            fi
        else
            echo "Command output:" >&2
            cat "${output_file}" >&2
            log_fail "Test failed with unknown issue"
            failed_tests=$((failed_tests + 1))
        fi
    else
        # Test passed
        if [[ ${TEST_VERBOSE} == true ]]; then
            echo "Command output:" >&2
            cat "${output_file}" >&2
        fi

        if [[ ${known_issues} != "[]" ]]; then
            local always_fail_issues
            always_fail_issues=$(jq -cr '.[] | select(.fails == "always" or (.fails | not)) | .issue + " " + .description' <<< "${known_issues}")
            if [[ -n "${always_fail_issues}" ]]; then
                log_unexpected_pass "${always_fail_issues}"
                unexpected_pass_tests=$((unexpected_pass_tests + 1))
            else
                log_pass "Test passed"
                passed_tests=$((passed_tests + 1))
            fi
        else
            log_pass "Test passed"
            passed_tests=$((passed_tests + 1))
        fi
    fi
}

# Run a single test
# $1 = test_name
# $2 = test_data (JSON for the specific test)
# $3 = test_type ("binary" or "source")
# $4 = output_file
# Variables updated: passed_tests, failed_tests, known_issue_tests, unexpected_pass_tests
# Array updated: known_issues_found
run_single_test() {
    local test_name=$1
    local single_test_data=$2
    local test_type=$3
    local output_file=$4

    log_info "Running test..."
    local command source_dir environment known_issues
    command=$(jq -r '.command' <<< "${single_test_data}")
    known_issues=$(jq -c '.known_issues // []' <<< "${single_test_data}")
    source_dir=$(jq -r '.source_dir // "."' <<< "${single_test_data}")
    environment=$(jq -c '.environment // {}' <<< "${single_test_data}")

    if [[ "${environment}" != "{}" ]]; then
        # Test requires environment setup (container)
        local base_image packages package_sources container_id
        base_image=$(jq -r '.base // "quay.io/hummingbird/core-runtime:latest-builder"' <<< "${environment}")

        # Extract packages from both new format (local/remote) and old format (array)
        packages=$(jq -r '
            if .packages.local or .packages.remote then
                (.packages.local[]?, .packages.remote[]?) // empty
            else
                .packages[]? // empty
            end
        ' <<< "${environment}" | tr '\n' ' ')

        package_sources=$(determine_package_sources "${environment}" "${test_type}")
        container_id=$(setup_test_container "${base_image}")
        run_test_command "${command}" "${source_dir}" "${container_id}" "${packages}" "${package_sources}" "${output_file}"
    else
        # Test runs locally without container
        run_test_command "${command}" "${source_dir}" "" "" "" "${output_file}"
    fi

    handle_test_result "${output_file}" "${known_issues}"
}

# Print test summary
# $1 = summary_title
# $2 = total_tests
# $3 = passed_tests
# $4 = known_issue_tests
# $5 = failed_tests
# $6 = unexpected_pass_tests
# $7+ = known_issues_found array elements
print_test_summary() {
    local summary_title=$1
    local total=$2
    local passed=$3
    local known=$4
    local failed=$5
    local unexpected=$6
    shift 6
    local known_issues_array=("$@")

    if (( known > 0 )); then
        log_heading "Known Failures Found for ${summary_title}"
        printf '%s\n' "${known_issues_array[@]}" | grep -v '^null$' | grep -v '^$' | sort -u | sed 's/^/- /' >&2 || true
    fi

    log_heading "Test Summary for ${summary_title}"
    log_info "Passed: ${passed}/${total}"
    log_info "Known Failures: ${known}/${total}"
    log_info "Unknown Failures: ${failed}/${total}"
    log_info "Unexpected Passes: ${unexpected}/${total}"
}

# Run a suite of tests
# $1 = test_names (newline-separated)
# $2 = test_data (full JSON)
# $3 = test_type ("binary" or "source")
# $4 = suite_title (for logging)
# Returns via stdout: "total passed failed known unexpected known_issues..."
run_test_suite() {
    local test_names=$1
    local test_data=$2
    local test_type=$3
    local suite_title=$4

    local current_test=0
    local total_tests=0
    local passed_tests=0
    local failed_tests=0
    local known_issue_tests=0
    local unexpected_pass_tests=0
    declare -a known_issues_found

    local output_file
    output_file=$(mktemp)

    while IFS= read -r test_name; do
        [[ -z ${test_name} ]] && continue
        total_tests=$((total_tests + 1))
        current_test=$((current_test + 1))

        log_heading "Test ${current_test}: ${test_name}"

        local single_test_data
        single_test_data=$(jq --compact-output --arg test_name "${test_name}" '.[$test_name]' <<< "${test_data}")

        run_single_test "${test_name}" "${single_test_data}" "${test_type}" "${output_file}"
    done <<< "${test_names}"

    rm -f "${output_file}"

    # Print summary
    print_test_summary "${suite_title}" "${total_tests}" "${passed_tests}" "${known_issue_tests}" "${failed_tests}" "${unexpected_pass_tests}" "${known_issues_found[@]}"

    # Return results as space-separated values
    echo "${total_tests} ${passed_tests} ${failed_tests} ${known_issue_tests} ${unexpected_pass_tests} ${known_issues_found[*]}"
}

# Fail the test with a stderr message and exit code 1
TEST_FAIL() {
    echo "$1" >&2
    exit 1
}
export -f TEST_FAIL

# Build the repo builder image
# Returns: repo builder image name (echoed to stdout)
build_repo_builder_image() {
    local repo_builder_image="localhost/rpm-repo-builder:latest"
    local containerfile="${SCRIPT_DIR}/Containerfile.repo-builder"

    if [[ ! -f ${containerfile} ]]; then
        echo "Error: Containerfile.repo-builder not found at ${containerfile}" >&2
        exit 1
    fi

    log_info "Building repo builder image: ${repo_builder_image}"
    "${TEST_ENGINE}" build -t "${repo_builder_image}" -f "${containerfile}" "${SCRIPT_DIR}" >&2

    echo "${repo_builder_image}"
}

# Create local DNF repository on the host
# $1 = host directory to create repo in
# $2+ = RPM file paths (one or more)
create_host_repo() {
    local repo_dir=$1
    shift
    local rpm_files=("$@")
    local repo_builder_image="${REPO_BUILDER_IMAGE}"

    if [[ ${#rpm_files[@]} -eq 0 ]]; then
        return 0
    fi

    # Create repo directory on host
    mkdir -p "${repo_dir}"

    # Copy all RPMs to host repo directory
    for rpm_file in "${rpm_files[@]}"; do
        cp "${rpm_file}" "${repo_dir}/"
    done

    # Run createrepo_c on the host directory using the repo builder container
    "${TEST_ENGINE}" run --rm -v "${repo_dir}:/repo:z" "${repo_builder_image}" createrepo_c /repo

    # Create repository configuration file in host directory
    cat > "${repo_dir}/local-repo.repo" << 'REPO_CONFIG'
[local-repo]
name=Local Test Repository
baseurl=file:///tmp/local-repo
enabled=1
gpgcheck=0
priority=1
REPO_CONFIG
}

# Install packages from appropriate sources
# $1 = container_id
# $2+ = package names
# Environment variables:
#   TEST_PACKAGE_SOURCES = JSON map of package names to sources (local or fedora)
install_test_packages() {
    local container_id=$1
    shift
    local packages=("$@")

    if [[ ${#packages[@]} -eq 0 ]]; then
        return 0
    fi

    # Separate packages by source
    local local_packages=()
    local fedora_packages=()

    for pkg in "${packages[@]}"; do
        local source
        source=$(echo "${TEST_PACKAGE_SOURCES}" | jq -r --arg pkg "${pkg}" '.[$pkg] // "fedora"')
        if [[ "${source}" == "local" ]]; then
            local_packages+=("${pkg}")
        else
            fedora_packages+=("${pkg}")
        fi
    done

    # Install packages from their designated repositories
    # Local packages: ONLY from local-repo (not from Fedora)
    # Use 'reinstall' to force installing from local-repo even if package already exists
    if [[ ${#local_packages[@]} -gt 0 ]]; then
        "${TEST_ENGINE}" exec --user 0 -e HOME=/root "${container_id}" dnf reinstall -y --from-repo=local-repo "${local_packages[@]}"
    fi

    # Fedora packages: ONLY from Fedora repositories (not from local-repo)
    # Use 'install' since we just need any version from Fedora repos
    if [[ ${#fedora_packages[@]} -gt 0 ]]; then
        "${TEST_ENGINE}" exec --user 0 -e HOME=/root "${container_id}" dnf install -y --from-repo=rawhide "${fedora_packages[@]}"
    fi
}

export -f install_test_packages

# Parse command line arguments
TEST_VERBOSE=false
TEST_ENGINE=podman
TEST_IMAGE="${TEST_IMAGE:-quay.io/hummingbird/core-runtime:latest-builder}"
TEST_SRC_RPM=""
REPO_PATH=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --verbose|-v)
            TEST_VERBOSE=true
            shift
            ;;
        --repo)
            REPO_PATH=$2
            shift 2
            ;;
        --src-rpm)
            TEST_SRC_RPM=$2
            shift 2
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            echo "Usage: $0 [OPTIONS] PACKAGE_NAME"
            exit 1
            ;;
        *)
            if [[ -z ${PACKAGE_NAME:-} ]]; then
                PACKAGE_NAME=$1
            else
                echo "Error: Too many arguments"
                echo "Usage: $0 [OPTIONS] PACKAGE_NAME"
                exit 1
            fi
            shift
            ;;
    esac
done >&2

# Validate arguments
if [[ -z ${PACKAGE_NAME:-} ]]; then
    echo "Error: PACKAGE_NAME is required"
    echo "Usage: $0 [OPTIONS] PACKAGE_NAME"
    exit 1
fi >&2

if [[ -z ${REPO_PATH:-} ]]; then
    echo "Error: --repo DIR is required"
    echo "Usage: $0 [OPTIONS] PACKAGE_NAME"
    exit 1
fi >&2

# Validate package directory exists
package_dir="${base_dir}/rpms/${PACKAGE_NAME}"
if [[ ! -d ${package_dir} ]]; then
    echo "Error: Package directory '${package_dir}' not found"
    exit 1
fi >&2

# Process repo directory: find all RPMs and locate the package
RPM_PATHS=()
ALL_REPO_RPMS=()

REPO_PATH="$(convert_path_to_absolute "${REPO_PATH}")"
if [[ ! -d ${REPO_PATH} ]]; then
    echo "Error: Repository directory '${REPO_PATH}' not found"
    exit 1
fi >&2

# Find all RPMs in the repo directory
while IFS= read -r rpm_file; do
    ALL_REPO_RPMS+=("${rpm_file}")
done < <(find "${REPO_PATH}" -maxdepth 1 -name "*.rpm" -type f | sort)

if [[ ${#ALL_REPO_RPMS[@]} -eq 0 ]]; then
    echo "Error: No RPM files found in '${REPO_PATH}'"
    exit 1
fi >&2

# Find the RPM matching PACKAGE_NAME (binary RPM, not source)
package_rpm=""
for rpm_file in "${ALL_REPO_RPMS[@]}"; do
    # Skip source RPMs (.src.rpm)
    if [[ ${rpm_file} =~ \.src\.rpm$ ]]; then
        continue
    fi

    # Extract the base name without extension
    base_name=$(basename "${rpm_file}" .rpm)
    # Extract package name (everything before the version)
    # RPM format: name-version-release.arch.rpm
    # We'll check if the filename starts with the package name followed by a dash and digit
    if [[ ${base_name} =~ ^${PACKAGE_NAME}-[0-9] ]]; then
        package_rpm="${rpm_file}"
        break
    fi
done

if [[ -z ${package_rpm} ]]; then
    echo "Error: No RPM found for package '${PACKAGE_NAME}' in '${REPO_PATH}'"
    echo "Available RPMs:"
    ls -1 "${REPO_PATH}"/*.rpm 2>/dev/null || true
    exit 1
fi >&2

RPM_PATHS=("${package_rpm}")

# Validate source RPM file exists if provided
if [[ -n ${TEST_SRC_RPM} ]]; then
    TEST_SRC_RPM="$(convert_path_to_absolute "${TEST_SRC_RPM}")"
    if [[ ! -f ${TEST_SRC_RPM} ]]; then
        echo "Error: Source RPM file '${TEST_SRC_RPM}' not found"
        exit 1
    fi
fi >&2

# Setup test environment
export TEST_SRC_RPM
export TEST_VERBOSE
export TEST_ENGINE
export TEST_IMAGE
export PACKAGE_NAME

temp_dir=$(mktemp -d)
trap 'rm -rf "${temp_dir}"' EXIT

# Determine the source RPM directory if source RPM is provided
SRC_RPM_DIR=""
if [[ -n ${TEST_SRC_RPM} ]]; then
    SRC_RPM_DIR="$(dirname "${TEST_SRC_RPM}")"
fi

log_heading "Testing ${PACKAGE_NAME}"
log_info "Repository path: ${REPO_PATH}"
log_info "Repository RPMs: ${#ALL_REPO_RPMS[@]}"
for rpm_file in "${ALL_REPO_RPMS[@]}"; do
    log_info "  - $(basename "${rpm_file}")"
done
log_info "Primary RPM: $(basename "${RPM_PATHS[0]}")"
if [[ -n ${TEST_SRC_RPM} ]]; then
    log_info "Source RPM file: ${TEST_SRC_RPM}"
fi
log_info "Container engine: ${TEST_ENGINE}"
log_info "Container image: ${TEST_IMAGE}"
log_info "Verbose mode: ${TEST_VERBOSE}"

# Load default tests
default_tests_file="${base_dir}/ci/default-tests/tests-rpm.yml"
if [[ ! -f ${default_tests_file} ]]; then
    echo "Error: Default tests file '${default_tests_file}' not found"
    exit 1
fi >&2

test_data=$(yaml_to_json < "${default_tests_file}")
test_data=$(jq --arg dir "${base_dir}/ci/default-tests" 'to_entries | map(.value.source_dir = $dir) | from_entries' <<< "${test_data}")

# Load package-specific tests if they exist
package_tests_file="${package_dir}/tests-rpm.yml"
if [[ -f ${package_tests_file} ]]; then
    log_info "Including package-specific tests from ${package_tests_file}"
    package_test_data=$(yaml_to_json < "${package_tests_file}")
    package_test_data=$(jq --arg dir "${package_dir}" 'to_entries | map(.value.source_dir = $dir) | from_entries' <<< "${package_test_data}")
    # Merge tests (package-specific tests override default tests with same name)
    test_data=$(jq -s '.[0] * .[1]' <(echo "${test_data}") <(echo "${package_test_data}"))
fi

# Separate binary RPM tests from source RPM tests
# Source RPM tests are those with names starting with "src-"
binary_rpm_test_names=$(jq -r 'keys_unsorted[] | select(startswith("src-") | not)' <<< "${test_data}")
src_rpm_test_names=$(jq -r 'keys_unsorted[] | select(startswith("src-"))' <<< "${test_data}")

# Build repo builder image and create local repository on host
log_heading "Preparing Local RPM Repository on Host"

# Build the repo builder image
REPO_BUILDER_IMAGE=$(build_repo_builder_image)

# Create temporary directory for the host repository
HOST_REPO_DIR=$(mktemp -d)
log_info "Created host repository directory: ${HOST_REPO_DIR}"

# Add cleanup for host repo directory
cleanup_host_repo() {
    if [[ -n ${HOST_REPO_DIR} && -d ${HOST_REPO_DIR} ]]; then
        log_info "Cleaning up host repository directory..."
        rm -rf "${HOST_REPO_DIR}"
    fi
    if [[ -n ${REPO_BUILDER_IMAGE} ]]; then
        log_info "Cleaning up repo builder image..."
        "${TEST_ENGINE}" rmi "${REPO_BUILDER_IMAGE}" 2>/dev/null || true
    fi
}
trap cleanup_host_repo EXIT

# Create the repository on the host
if [[ ${#ALL_REPO_RPMS[@]} -gt 0 ]]; then
    log_info "Creating local repository with ${#ALL_REPO_RPMS[@]} RPMs..."
    create_host_repo "${HOST_REPO_DIR}" "${ALL_REPO_RPMS[@]}"
    log_info "Repository created successfully"
else
    log_info "No RPMs to add to repository"
fi

# Initialize overall counters
overall_total_tests=0
overall_passed_tests=0
overall_failed_tests=0
overall_known_issue_tests=0
overall_unexpected_pass_tests=0
declare -a overall_known_issues_found

# Run binary RPM tests once for the package (not per-RPM)
# Set TEST_RPM to the primary package RPM
export TEST_RPM="${RPM_PATHS[0]}"

log_heading "Testing Binary RPM Package: ${PACKAGE_NAME}"

# Run binary RPM tests and capture results
results=$(run_test_suite "${binary_rpm_test_names}" "${test_data}" "binary" "Binary RPM Tests")
read -r total_tests passed_tests failed_tests known_issue_tests unexpected_pass_tests known_issues_rest <<< "${results}"

# Accumulate results
overall_total_tests=$((overall_total_tests + total_tests))
overall_passed_tests=$((overall_passed_tests + passed_tests))
overall_failed_tests=$((overall_failed_tests + failed_tests))
overall_known_issue_tests=$((overall_known_issue_tests + known_issue_tests))
overall_unexpected_pass_tests=$((overall_unexpected_pass_tests + unexpected_pass_tests))
if [[ -n ${known_issues_rest} ]]; then
    overall_known_issues_found+=(${known_issues_rest[@]})
fi

echo ""

# Run source RPM tests once (if any exist and TEST_SRC_RPM is provided)
if [[ -n ${src_rpm_test_names} ]]; then
    log_heading "Running Source RPM Tests"
    if [[ -n ${TEST_SRC_RPM} ]]; then
        log_info "Source RPM: ${TEST_SRC_RPM}"
    fi

    # Use the first binary RPM's TEST_RPM for any tests that might reference it
    if (( ${#RPM_PATHS[@]} > 0 )); then
        export TEST_RPM="${RPM_PATHS[0]}"
    fi

    # Run source RPM tests and capture results
    results=$(run_test_suite "${src_rpm_test_names}" "${test_data}" "source" "Source RPM Tests")
    read -r total_tests passed_tests failed_tests known_issue_tests unexpected_pass_tests known_issues_rest <<< "${results}"

    # Accumulate source RPM test results into overall results
    overall_total_tests=$((overall_total_tests + total_tests))
    overall_passed_tests=$((overall_passed_tests + passed_tests))
    overall_failed_tests=$((overall_failed_tests + failed_tests))
    overall_known_issue_tests=$((overall_known_issue_tests + known_issue_tests))
    overall_unexpected_pass_tests=$((overall_unexpected_pass_tests + unexpected_pass_tests))
    if [[ -n ${known_issues_rest} ]]; then
        overall_known_issues_found+=(${known_issues_rest[@]})
    fi

    echo ""
fi

# Print overall summary if source RPM tests were run in addition to binary tests
has_src_rpm_tests=false
[[ -n ${src_rpm_test_names} ]] && has_src_rpm_tests=true

if [[ ${has_src_rpm_tests} == true ]]; then
    if (( overall_known_issue_tests > 0 )); then
        log_heading "Overall Known Failures Found"
        echo "${overall_known_issues_found[@]}" | tr ' ' '\n' | grep -v '^null$' | grep -v '^$' | sort -u | sed 's/^/- /' || true
    fi

    log_heading "Overall Test Summary (Binary + Source RPM)"
    log_info "Passed: ${overall_passed_tests}/${overall_total_tests}"
    log_info "Known Failures: ${overall_known_issue_tests}/${overall_total_tests}"
    log_info "Unknown Failures: ${overall_failed_tests}/${overall_total_tests}"
    log_info "Unexpected Passes: ${overall_unexpected_pass_tests}/${overall_total_tests}"

    # Use overall results for exit code
    passed_tests=${overall_passed_tests}
    failed_tests=${overall_failed_tests}
    known_issue_tests=${overall_known_issue_tests}
    unexpected_pass_tests=${overall_unexpected_pass_tests}
fi

# Determine exit code
if (( failed_tests > 0 )); then
    log_fail "${failed_tests} test(s) failed with new/unexpected issues"
    exit 1
elif (( unexpected_pass_tests > 0 )); then
    log_unexpected_pass "${unexpected_pass_tests} test(s) passed but expected to fail"
    exit 1
elif (( known_issue_tests > 0 )); then
    log_known_failure "${known_issue_tests} test(s) failed with known issues"
else
    log_pass "All tests passed successfully!"
fi
