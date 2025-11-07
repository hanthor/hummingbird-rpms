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
    --rpm PATH           Path to the binary RPM file to test (required)
    --src-rpm PATH       Path to source RPM file (optional, enables source RPM tests)
    --verbose, -v        Enable verbose output during testing
    --engine ENGINE      Specify container engine (podman or docker, default: podman)
    --help, -h           Show this help message

ARGUMENTS:
    PACKAGE_NAME         Name of the RPM package (must correspond to a folder in rpms/)
                         Examples: rootfiles, setup

EXAMPLES:
    $0 --rpm /path/to/rootfiles-1.0-1.fc40.x86_64.rpm rootfiles
    $0 --rpm /path/to/setup-2.0-1.fc40.noarch.rpm --verbose setup
    $0 --rpm /path/to/setup.rpm --src-rpm /path/to/setup.src.rpm setup

DESCRIPTION:
    Runs default tests for the specified RPM package. Tests are defined in
    ci/default-tests/tests-rpm.yml and package-specific tests in
    rpms/<package>/tests-rpm.yml (if present).
EOF
}

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
base_dir="$(dirname "${SCRIPT_DIR}")"

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

# Parse command line arguments
TEST_VERBOSE=false
TEST_ENGINE=podman
TEST_SRC_RPM=""
RPM_PATH=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --verbose|-v)
            TEST_VERBOSE=true
            shift
            ;;
        --engine)
            TEST_ENGINE=$2
            shift 2
            ;;
        --rpm)
            RPM_PATH=$2
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

if [[ -z ${RPM_PATH:-} ]]; then
    echo "Error: --rpm PATH is required"
    echo "Usage: $0 [OPTIONS] PACKAGE_NAME"
    exit 1
fi >&2

# Validate package directory exists
package_dir="${base_dir}/rpms/${PACKAGE_NAME}"
if [[ ! -d ${package_dir} ]]; then
    echo "Error: Package directory '${package_dir}' not found"
    exit 1
fi >&2

# Validate RPM file exists
if [[ ! -f ${RPM_PATH} ]]; then
    echo "Error: RPM file '${RPM_PATH}' not found"
    exit 1
fi >&2

# Validate source RPM file exists if provided
if [[ -n ${TEST_SRC_RPM} ]] && [[ ! -f ${TEST_SRC_RPM} ]]; then
    echo "Error: Source RPM file '${TEST_SRC_RPM}' not found"
    exit 1
fi >&2

# Setup test environment
export TEST_RPM="${RPM_PATH}"
export TEST_SRC_RPM
export TEST_VERBOSE
export TEST_ENGINE
export PACKAGE_NAME

temp_dir=$(mktemp -d)
trap 'rm -rf "${temp_dir}"' EXIT

log_heading "Testing ${PACKAGE_NAME}"
log_info "RPM file: ${TEST_RPM}"
if [[ -n ${TEST_SRC_RPM} ]]; then
    log_info "Source RPM file: ${TEST_SRC_RPM}"
fi
log_info "Container engine: ${TEST_ENGINE}"
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

test_names=$(jq -r 'keys_unsorted[]' <<< "${test_data}")

# Run tests
current_test=0
total_tests=0
passed_tests=0
failed_tests=0
known_issue_tests=0
unexpected_pass_tests=0
declare -a known_issues_found

while IFS= read -r test_name; do
    total_tests=$((total_tests + 1))
    current_test=$((current_test + 1))

    log_heading "Test ${current_test}: ${test_name}"

    single_test_data=$(jq --compact-output --arg test_name "${test_name}" '.[$test_name]' <<< "${test_data}")

    log_info "Running test..."
    command=$(jq -r '.command' <<< "${single_test_data}")
    known_issues=$(jq -c '.known_issues // []' <<< "${single_test_data}")
    source_dir=$(jq -r '.source_dir // "."' <<< "${single_test_data}")

    # Run test in subshell to avoid side effects
    (
        [[ ${TEST_VERBOSE} == true ]] && set -x
        # Change to the directory where the test is defined
        cd "${source_dir}"
        # Ensure TEST_RPM, TEST_SRC_RPM and TEST_ENGINE are available in subshell
        export TEST_RPM="${TEST_RPM}"
        export TEST_SRC_RPM="${TEST_SRC_RPM}"
        export TEST_ENGINE="${TEST_ENGINE}"
        eval "${command}"
    ) &> "${temp_dir}/output.log" &

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
done <<< "${test_names}"

# Print summary
if (( known_issue_tests > 0 )); then
    log_heading "Known Failures Found"
    echo "${known_issues_found[@]}" | tr ' ' '\n' | grep -v '^null$' | grep -v '^$' | sort -u | sed 's/^/- /'
fi

log_heading "Test Summary"
log_info "Passed: ${passed_tests}/${total_tests}"
log_info "Known Failures: ${known_issue_tests}/${total_tests}"
log_info "Unknown Failures: ${failed_tests}/${total_tests}"
log_info "Unexpected Passes: ${unexpected_pass_tests}/${total_tests}"

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
