#!/bin/bash
set -euo pipefail

test_files=$(cd -- "$(dirname -- "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "${work}"
cp "${test_files}/fips_startup_test.go" startup.go

# Keep inherited Go configuration from changing the module or runtime mode.
export GOENV=off GOTOOLCHAIN=local GOFLAGS='' GOEXPERIMENT='' GODEBUG=''
export GOLANG_NATIVE_HOSTFIPS_OVERRIDE=0
unset GOFIPS140

goroot=$(go env GOROOT)
snapshot=$(cat "${goroot}/lib/fips140/certified.txt")
info=$(go version -m "${goroot}/bin/go")
grep -F "GOFIPS140=${snapshot}" <<< "${info}"
grep -E 'DefaultGODEBUG=.*fips140=auto(,|$)' <<< "${info}"
host_fips=0
if [[ -e /proc/sys/crypto/fips_enabled ]]; then
    host_fips=$(cat /proc/sys/crypto/fips_enabled)
fi

reject_disabled() {
    local output
    if output=$(GOLANG_NATIVE_HOSTFIPS_OVERRIDE=1 GODEBUG=fips140=off "$1" 2>&1); then
        echo "FAIL: startup guard allowed disabled FIPS" >&2
        exit 1
    fi
    grep -F 'Host FIPS mode is enabled, but the required GODEBUG=fips140 module is disabled' <<< "${output}"
}

# The guard is optional. Ordinary binaries must still permit the override.
go build -o ordinary startup.go
GOLANG_NATIVE_HOSTFIPS_OVERRIDE=1 GODEBUG=fips140=off ./ordinary

for mechanism in tag experiment; do
    flags=()
    export GOEXPERIMENT=
    if [[ "${mechanism}" == tag ]]; then
        flags=(-tags=strictfipsruntime)
    else
        export GOEXPERIMENT=strictfipsruntime
    fi
    go build "${flags[@]}" -o guarded startup.go
    GOLANG_NATIVE_HOSTFIPS_OVERRIDE=1 ./guarded
    GOLANG_NATIVE_HOSTFIPS_OVERRIDE=0 GODEBUG=fips140=on ./guarded
    GOLANG_NATIVE_HOSTFIPS_OVERRIDE=1 GODEBUG=fips140=only ./guarded
    reject_disabled ./guarded

    if [[ "${host_fips}" == 0 ]]; then
        GOLANG_NATIVE_HOSTFIPS_OVERRIDE=0 GODEBUG=fips140=off ./guarded
    fi

    # The guard checks runtime activation even without a frozen snapshot.
    GOFIPS140=off go build "${flags[@]}" -o guarded-in-tree startup.go
    reject_disabled ./guarded-in-tree
    GOLANG_NATIVE_HOSTFIPS_OVERRIDE=1 GODEBUG=fips140=on ./guarded-in-tree
    echo "PASS: ${mechanism} startup guard"
done

export GOEXPERIMENT=
if [[ -f "${test_files}/fips_mldsa_test.go" ]]; then
    cp "${test_files}/fips_mldsa_test.go" mldsa.go
    go build -o mldsa-unavailable mldsa.go
    for mode in off on only auto; do
        GOLANG_NATIVE_HOSTFIPS_OVERRIDE=1 GODEBUG=fips140=${mode} ./mldsa-unavailable
    done
fi
