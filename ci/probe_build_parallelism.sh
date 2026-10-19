#!/bin/bash
set -euo pipefail

# Integration probe: same input lock/repository, separate clean offline builds.
# Keep this image in sync with ci/build_rpms.sh.
image=quay.io/redhat-user-workloads/rpm-build-pipeline-tenant/environment:latest@sha256:56bde7a1040650bc14ee927534426a52d88de30d588048c6a08be7a8758372cb
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p "${repo_root}/builds"
workdir=$(mktemp -d "${repo_root}/builds/parallelism-probe.XXXXXX")
echo "Probe artifacts: ${workdir}"
mkdir -p "${workdir}/sources" "${workdir}/config" "${workdir}/calculation" "${workdir}/mock"
cp "${repo_root}/test/fixtures/build-parallelism/"* "${workdir}/sources/"
arch=$(uname -m)
sed "s/@ARCH@/${arch}/g" "${repo_root}/mock/mock.cfg" > "${workdir}/config/mock.cfg"
podman unshare setfacl -R -m g:135:rwx -m default:g:135:rwx "${workdir}"

podman_args=(--rm --privileged --init -u mockbuilder
    -v "${workdir}:/probe:z" -v "${workdir}/mock:/var/lib/mock:z")
podman run "${podman_args[@]}" "${image}" \
    mock -r /probe/config/mock.cfg --spec /probe/sources/parallelism-probe.spec \
    --sources /probe/sources --resultdir /probe/calculation \
    --calculate-build-dependencies \
    --define 'probe_expected 2' --define '_smp_build_ncpus 2'
podman run --rm --privileged -v "${workdir}:/probe:z" "${image}" \
    mock-hermetic-repo --lockfile /probe/calculation/buildroot_lock.json \
    --output-repo /probe/offline-repo

for profile in jobs-2 jobs-4 jobs-4-capped-2; do
    jobs=4
    expected=4
    extra_args=()
    if [[ "${profile}" == jobs-2 ]]; then
        jobs=2
        expected=2
    elif [[ "${profile}" == jobs-4-capped-2 ]]; then
        expected=2
        extra_args+=(--define 'probe_cap 2')
    fi
    mkdir -p "${workdir}/${profile}"
    podman unshare setfacl -m g:135:rwx -m default:g:135:rwx "${workdir}/${profile}"
    echo "Running ${profile} (expected peak: ${expected})"
    podman run --network=none "${podman_args[@]}" "${image}" \
        mock --hermetic-build /probe/calculation/buildroot_lock.json /probe/offline-repo \
        --spec /probe/sources/parallelism-probe.spec --sources /probe/sources \
        --resultdir "/probe/${profile}" \
        --define "_smp_build_ncpus ${jobs}" --define "probe_expected ${expected}" \
        "${extra_args[@]}" > "${workdir}/${profile}/console.log" 2>&1 || {
            tail -80 "${workdir}/${profile}/console.log"
            exit 1
        }
done
podman unshare chown -R 0:0 "${workdir}"
echo "PASS: observed 2 and 4 concurrent recipes; package constraint reduced 4 to 2."
echo "Logs and diagnostic RPMs: ${workdir}"
