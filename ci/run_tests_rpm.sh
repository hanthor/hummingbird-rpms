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

# Fail the test with a stderr message and exit code 1
TEST_FAIL() {
    echo "$1" >&2
    exit 1
}
export -f TEST_FAIL

# Setup local DNF repository - copy RPMs to container
# $1 = container_id
# $2+ = RPM file paths (one or more)
setup_local_repo() {
    local container_id=$1
    shift
    local rpm_files=("$@")

    if [[ ${#rpm_files[@]} -eq 0 ]]; then
        return 0
    fi

    # Copy all RPMs to container
    for rpm_file in "${rpm_files[@]}"; do
        "${TEST_ENGINE}" cp "${rpm_file}" "${container_id}:/tmp/local-repo/"
    done
}

# Finalize local DNF repository - run commands in the container
# $1 = container_id
finalize_local_repo() {
    local container_id=$1

    # Create repository metadata
    "${TEST_ENGINE}" exec --user 0 -e HOME=/root "${container_id}" dnf install -y createrepo_c 2>/dev/null || \
    "${TEST_ENGINE}" exec --user 0 -e HOME=/root "${container_id}" yum install -y createrepo_c 2>/dev/null || true

    "${TEST_ENGINE}" exec --user 0 -e HOME=/root "${container_id}" createrepo_c /tmp/local-repo

    # Configure DNF to use local repo with high priority
    cat > /tmp/local-repo.conf << 'REPO_CONFIG'
[local-repo]
name=Local Test Repository
baseurl=file:///tmp/local-repo
enabled=1
gpgcheck=0
priority=1
REPO_CONFIG

    "${TEST_ENGINE}" cp /tmp/local-repo.conf "${container_id}:/etc/yum.repos.d/local-repo.repo"
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
        local source=$(echo "${TEST_PACKAGE_SOURCES}" | jq -r --arg pkg "${pkg}" '.[$pkg] // "fedora"')
        if [[ "${source}" == "local" ]]; then
            local_packages+=("${pkg}")
        else
            fedora_packages+=("${pkg}")
        fi
    done

    # Install packages
    if [[ ${#local_packages[@]} -gt 0 ]]; then
        "${TEST_ENGINE}" exec --user 0 -e HOME=/root "${container_id}" dnf install -y "${local_packages[@]}" || true
    fi

    if [[ ${#fedora_packages[@]} -gt 0 ]]; then
        "${TEST_ENGINE}" exec --user 0 -e HOME=/root "${container_id}" dnf install -y "${fedora_packages[@]}" || true
    fi
}

export -f setup_local_repo
export -f finalize_local_repo
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

# Initialize overall counters
overall_total_tests=0
overall_passed_tests=0
overall_failed_tests=0
overall_known_issue_tests=0
overall_unexpected_pass_tests=0
declare -a overall_known_issues_found

# Run tests for each RPM
rpm_index=0
for rpm_file in "${RPM_PATHS[@]}"; do
    rpm_index=$((rpm_index + 1))
    export TEST_RPM="${rpm_file}"

    log_heading "Testing Binary RPM ${rpm_index}/${#RPM_PATHS[@]}: $(basename "${rpm_file}")"

    # Run binary RPM tests
    current_test=0
    total_tests=0
    passed_tests=0
    failed_tests=0
    known_issue_tests=0
    unexpected_pass_tests=0
    declare -a known_issues_found

    while IFS= read -r test_name; do
        [[ -z ${test_name} ]] && continue
        total_tests=$((total_tests + 1))
        current_test=$((current_test + 1))

        log_heading "Test ${current_test}: ${test_name}"

        single_test_data=$(jq --compact-output --arg test_name "${test_name}" '.[$test_name]' <<< "${test_data}")

        log_info "Running test..."
        command=$(jq -r '.command' <<< "${single_test_data}")
        known_issues=$(jq -c '.known_issues // []' <<< "${single_test_data}")
        source_dir=$(jq -r '.source_dir // "."' <<< "${single_test_data}")
        environment=$(jq -c '.environment // {}' <<< "${single_test_data}")

        # Check if test requires environment setup
        if [[ "${environment}" != "{}" ]]; then
            # Run test with environment setup
            base_image=$(jq -r '.base // "quay.io/hummingbird/core-runtime:latest-builder"' <<< "${environment}")
            packages=$(jq -r '.packages[]? // empty' <<< "${environment}" | tr '\n' ' ')

            # Create package sources map (first package from local repo, others from fedora)
            package_sources=$(jq -c '
                if .packages then
                    [.packages[] | select(. != null)] as $pkgs |
                    ($pkgs[0] // "rpm") as $first |
                    ($pkgs | map(select(. == $first) | {(.): "local"}) + map(select(. != $first) | {(.): "fedora"}) | add)
                else {} end
            ' <<< "${environment}")

            # Run test in subshell with environment setup
            (
                [[ ${TEST_VERBOSE} == true ]] && set -x
                # Change to the directory where the test is defined
                cd "${source_dir}"

                # Create container
                container_id=$("${TEST_ENGINE:?}" create --entrypoint sleep "${base_image}" infinity)
                cleanup() {
                    "${TEST_ENGINE}" rm -f "${container_id}" 2>/dev/null || true
                }
                trap cleanup EXIT

                # Start container first
                "${TEST_ENGINE}" start "${container_id}"

                # Create repo directory in container
                "${TEST_ENGINE}" exec --user 0 -e HOME=/root "${container_id}" mkdir -p /tmp/local-repo

                # Copy RPMs to container
                setup_local_repo "${container_id}" "${ALL_REPO_RPMS[@]}"

                # Finalize local repo (create metadata and configure DNF)
                finalize_local_repo "${container_id}"

                # Install packages
                if [[ -n "${packages}" ]]; then
                    export TEST_PACKAGE_SOURCES="${package_sources}"
                    install_test_packages "${container_id}" ${packages}
                fi

                # Copy source RPM if provided
                if [[ -n ${TEST_SRC_RPM} ]]; then
                    "${TEST_ENGINE}" cp "${TEST_SRC_RPM}" "${container_id}:/tmp/test.src.rpm"
                fi

                # Copy primary RPM file if needed by test
                "${TEST_ENGINE}" cp "${TEST_RPM}" "${container_id}:/tmp/test.rpm"

                # Ensure TEST_RPM, TEST_SRC_RPM, TEST_ENGINE and TEST_IMAGE are available in subshell
                export TEST_RPM TEST_SRC_RPM TEST_ENGINE TEST_IMAGE

                # Run test command
                eval "${command}"

                # Cleanup
                "${TEST_ENGINE}" stop "${container_id}"
            ) &> "${temp_dir}/output.log" &
        else
            # Run test without environment setup (local testing)
            (
                [[ ${TEST_VERBOSE} == true ]] && set -x
                # Change to the directory where the test is defined
                cd "${source_dir}"
                # Ensure TEST_RPM, TEST_SRC_RPM, TEST_ENGINE and TEST_IMAGE are available in subshell
                export TEST_RPM TEST_SRC_RPM TEST_ENGINE TEST_IMAGE
                eval "${command}"
            ) &> "${temp_dir}/output.log" &
        fi

        if ! wait -n; then
            issue_result=$(check_known_issues "${known_issues}" "${temp_dir}/output.log")
            if [[ -n "${issue_result}" ]]; then
                # Trim leading/trailing whitespace from the combined line
                issue_line=$(tr '\n' ' ' <<< "${issue_result}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
                log_known_failure "${issue_line}"
                known_issue_tests=$((known_issue_tests + 1))
                issue_url=$(head -n1 <<< "${issue_result}")
                # Only add to known_issues_found if not empty/null
                if [[ -n ${issue_url} && ${issue_url} != "null" ]]; then
                    known_issues_found+=("${issue_url}")
                fi
            else
                echo "Command output:"
                cat "${temp_dir}/output.log"
                log_fail "Test failed with unknown issue"
                failed_tests=$((failed_tests + 1))
            fi
        else
            if [[ ${TEST_VERBOSE} == true ]]; then
                echo "Command output:"
                cat "${temp_dir}/output.log"
            fi

            if [[ ${known_issues} != "[]" ]]; then
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
    done <<< "${binary_rpm_test_names}"

    # Print summary for this RPM
    if (( known_issue_tests > 0 )); then
        log_heading "Known Failures Found for $(basename "${rpm_file}")"
        echo "${known_issues_found[@]}" | tr ' ' '\n' | grep -v '^null$' | grep -v '^$' | sort -u | sed 's/^/- /' || true
    fi

    log_heading "Test Summary for $(basename "${rpm_file}")"
    log_info "Passed: ${passed_tests}/${total_tests}"
    log_info "Known Failures: ${known_issue_tests}/${total_tests}"
    log_info "Unknown Failures: ${failed_tests}/${total_tests}"
    log_info "Unexpected Passes: ${unexpected_pass_tests}/${total_tests}"

    # Accumulate results
    overall_total_tests=$((overall_total_tests + total_tests))
    overall_passed_tests=$((overall_passed_tests + passed_tests))
    overall_failed_tests=$((overall_failed_tests + failed_tests))
    overall_known_issue_tests=$((overall_known_issue_tests + known_issue_tests))
    overall_unexpected_pass_tests=$((overall_unexpected_pass_tests + unexpected_pass_tests))
    overall_known_issues_found+=("${known_issues_found[@]}")

    echo ""
done

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

    # Run source RPM tests
    current_test=0
    total_tests=0
    passed_tests=0
    failed_tests=0
    known_issue_tests=0
    unexpected_pass_tests=0
    declare -a known_issues_found

    while IFS= read -r test_name; do
        [[ -z ${test_name} ]] && continue
        total_tests=$((total_tests + 1))
        current_test=$((current_test + 1))

        log_heading "Source RPM Test ${current_test}: ${test_name}"

        single_test_data=$(jq --compact-output --arg test_name "${test_name}" '.[$test_name]' <<< "${test_data}")

        log_info "Running test..."
        command=$(jq -r '.command' <<< "${single_test_data}")
        known_issues=$(jq -c '.known_issues // []' <<< "${single_test_data}")
        source_dir=$(jq -r '.source_dir // "."' <<< "${single_test_data}")
        environment=$(jq -c '.environment // {}' <<< "${single_test_data}")

        # Check if test requires environment setup
        if [[ "${environment}" != "{}" ]]; then
            # Run test with environment setup
            base_image=$(jq -r '.base // "quay.io/hummingbird/core-runtime:latest-builder"' <<< "${environment}")
            packages=$(jq -r '.packages[]? // empty' <<< "${environment}" | tr '\n' ' ')

            # Create package sources map (packages from local repo, unless specified otherwise)
            package_sources=$(jq -c '
                if .packages then
                    [.packages[] | select(. != null)] | map({(.): "fedora"}) | add
                else {} end
            ' <<< "${environment}")

            # Run test in subshell with environment setup
            (
                [[ ${TEST_VERBOSE} == true ]] && set -x
                # Change to the directory where the test is defined
                cd "${source_dir}"

                # Create container
                container_id=$("${TEST_ENGINE:?}" create --entrypoint sleep "${base_image}" infinity)
                cleanup() {
                    "${TEST_ENGINE}" rm -f "${container_id}" 2>/dev/null || true
                }
                trap cleanup EXIT

                # Start container first
                "${TEST_ENGINE}" start "${container_id}"

                # Setup local repo with all repo RPMs if available
                if [[ ${#ALL_REPO_RPMS[@]} -gt 0 ]]; then
                    "${TEST_ENGINE}" exec --user 0 -e HOME=/root "${container_id}" mkdir -p /tmp/local-repo
                    setup_local_repo "${container_id}" "${ALL_REPO_RPMS[@]}"
                    finalize_local_repo "${container_id}"
                fi

                # Install packages
                if [[ -n "${packages}" ]]; then
                    export TEST_PACKAGE_SOURCES="${package_sources}"
                    install_test_packages "${container_id}" ${packages}
                fi

                # Copy source RPM if provided
                if [[ -n ${TEST_SRC_RPM} ]]; then
                    "${TEST_ENGINE}" cp "${TEST_SRC_RPM}" "${container_id}:/tmp/test.src.rpm"
                fi

                # Ensure TEST_RPM, TEST_SRC_RPM, TEST_ENGINE and TEST_IMAGE are available in subshell
                export TEST_RPM TEST_SRC_RPM TEST_ENGINE TEST_IMAGE

                # Run test command
                eval "${command}"

                # Cleanup
                "${TEST_ENGINE}" stop "${container_id}"
            ) &> "${temp_dir}/output.log" &
        else
            # Run test without environment setup (local testing)
            (
                [[ ${TEST_VERBOSE} == true ]] && set -x
                # Change to the directory where the test is defined
                cd "${source_dir}"
                # Ensure TEST_RPM, TEST_SRC_RPM, TEST_ENGINE and TEST_IMAGE are available in subshell
                export TEST_RPM TEST_SRC_RPM TEST_ENGINE TEST_IMAGE
                eval "${command}"
            ) &> "${temp_dir}/output.log" &
        fi

        if ! wait -n; then
            issue_result=$(check_known_issues "${known_issues}" "${temp_dir}/output.log")
            if [[ -n "${issue_result}" ]]; then
                # Trim leading/trailing whitespace from the combined line
                issue_line=$(tr '\n' ' ' <<< "${issue_result}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
                log_known_failure "${issue_line}"
                known_issue_tests=$((known_issue_tests + 1))
                issue_url=$(head -n1 <<< "${issue_result}")
                # Only add to known_issues_found if not empty/null
                if [[ -n ${issue_url} && ${issue_url} != "null" ]]; then
                    known_issues_found+=("${issue_url}")
                fi
            else
                echo "Command output:"
                cat "${temp_dir}/output.log"
                log_fail "Test failed with unknown issue"
                failed_tests=$((failed_tests + 1))
            fi
        else
            if [[ ${TEST_VERBOSE} == true ]]; then
                echo "Command output:"
                cat "${temp_dir}/output.log"
            fi

            if [[ ${known_issues} != "[]" ]]; then
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
    done <<< "${src_rpm_test_names}"

    # Print summary for source RPM tests
    if (( known_issue_tests > 0 )); then
        log_heading "Known Failures Found for Source RPM Tests"
        echo "${known_issues_found[@]}" | tr ' ' '\n' | grep -v '^null$' | grep -v '^$' | sort -u | sed 's/^/- /' || true
    fi

    log_heading "Source RPM Test Summary"
    log_info "Passed: ${passed_tests}/${total_tests}"
    log_info "Known Failures: ${known_issue_tests}/${total_tests}"
    log_info "Unknown Failures: ${failed_tests}/${total_tests}"
    log_info "Unexpected Passes: ${unexpected_pass_tests}/${total_tests}"

    # Accumulate source RPM test results into overall results
    overall_total_tests=$((overall_total_tests + total_tests))
    overall_passed_tests=$((overall_passed_tests + passed_tests))
    overall_failed_tests=$((overall_failed_tests + failed_tests))
    overall_known_issue_tests=$((overall_known_issue_tests + known_issue_tests))
    overall_unexpected_pass_tests=$((overall_unexpected_pass_tests + unexpected_pass_tests))
    overall_known_issues_found+=("${known_issues_found[@]}")

    echo ""
fi

# Print overall summary if multiple RPMs were tested or source RPM tests were run
has_src_rpm_tests=false
[[ -n ${src_rpm_test_names} ]] && has_src_rpm_tests=true

if (( ${#RPM_PATHS[@]} > 1 )) || [[ ${has_src_rpm_tests} == true ]]; then
    if (( overall_known_issue_tests > 0 )); then
        log_heading "Overall Known Failures Found"
        echo "${overall_known_issues_found[@]}" | tr ' ' '\n' | grep -v '^null$' | grep -v '^$' | sort -u | sed 's/^/- /' || true
    fi

    summary_title="Overall Test Summary"
    if (( ${#RPM_PATHS[@]} > 1 )) && [[ ${has_src_rpm_tests} == true ]]; then
        summary_title="Overall Test Summary (${#RPM_PATHS[@]} Binary RPMs + Source RPM)"
    elif (( ${#RPM_PATHS[@]} > 1 )); then
        summary_title="Overall Test Summary (${#RPM_PATHS[@]} Binary RPMs)"
    elif [[ ${has_src_rpm_tests} == true ]]; then
        summary_title="Overall Test Summary (Binary + Source RPM)"
    fi

    log_heading "${summary_title}"
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
