"""Serialize a run's downloads and freeze each repository on first use."""

import configparser
import fcntl
import hashlib
import json
import platform
import shutil
from pathlib import Path

from tmt_rpmtest.environment.integrity import digest

DOWNLOAD = Path(__file__).with_name("download.sh").read_text()
OPTIONS = r"""
dnf() {
  dnf5 --installroot=/work/resolve-root --use-host-config --releasever=rpmtest \
    --setopt=reposdir=/work/repos --setopt=cachedir=/pool/dnf \
    --setopt=system_cachedir=/pool/dnf --setopt=keepcache=True \
    --setopt=install_weak_deps=False --setopt=metadata_expire=-1 \
    --setopt=check_config_file_age=False --setopt=build_cache=False \
    --setopt=optional_metadata_types=filelists --setopt=skip_if_unavailable=False \
    --setopt=timeout=30 --setopt=minrate=1000 --no-gpgchecks -y "$@"
}
"""
METADATA = DOWNLOAD + OPTIONS + '\nset -eu\nrpmtest_download dnf --repo="$1" makecache >&2\n'
RESOLVE = (
    DOWNLOAD
    + OPTIONS
    + r"""
set -eu
rpmtest_resolve() {
  # Keep completed downloads across retries, but never reuse a partial transaction.
  rm -rf /work/resolve-root /work/transaction /work/packages || return
  mkdir -p /work/resolve-root /work/packages || return
  dnf --setopt=cacheonly=metadata install --downloadonly "$@" >&2 || return
  dnf --setopt=cacheonly=all install --store=/work/transaction "$@" >&2
}
rpmtest_download rpmtest_resolve "$@"
cp /work/transaction/packages/*.rpm /work/packages/
"""
)


def _hashes(pool: Path, pattern: str) -> dict[str, str]:
    return {str(path.relative_to(pool)): digest(path) for path in sorted(pool.glob(pattern))}


def _verify(pool: Path, hashes: dict[str, str]) -> None:
    for name, expected in hashes.items():
        path = pool / name
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"Shared download cache changed: {name}")


def _publish(pool: Path, state: dict) -> None:
    temporary = pool / "state.tmp"
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(pool / "state.json")


def resolve(runtime, image: str, work: Path, packages: list[str], cache: Path) -> dict:
    identity = {
        "version": 1,
        "builder": image,
        "architecture": platform.machine(),
        "scripts": [METADATA, RESOLVE],
    }
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    pool = cache / key
    pool.mkdir(parents=True, exist_ok=True)
    with (pool / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_path = pool / "state.json"
        state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {"repositories": {}, "packages": {}}
        )
        for repository in state["repositories"].values():
            _verify(pool, repository["files"])
        _verify(pool, state["packages"])
        # Discard unpublished files after an interrupted request, never a frozen entry.
        for path in pool.glob("dnf/*/packages/*.rpm"):
            if str(path.relative_to(pool)) not in state["packages"]:
                path.unlink()
        for path in pool.glob("dnf/*/solv"):
            shutil.rmtree(path)
        configuration = configparser.ConfigParser(interpolation=None)
        configuration.read(sorted((work / "repos").glob("*.repo")))
        for name in configuration.sections():
            if name in state["repositories"]:
                continue
            for path in pool.glob(f"dnf/{name}-*"):
                shutil.rmtree(path)
            runtime.build(image, work, METADATA, name, internet=True, cache=pool)
            files = _hashes(pool, f"dnf/{name}-*/repodata/*")
            if not files or not any(path.endswith("/repomd.xml") for path in files):
                raise ValueError(f"Repository {name} did not produce a metadata snapshot.")
            state["repositories"][name] = {"url": configuration[name]["baseurl"], "files": files}
            _publish(pool, state)
        before = set(state["packages"].values())
        try:
            runtime.build(image, work, RESOLVE, *packages, internet=True, cache=pool)
        finally:
            for repository in state["repositories"].values():
                _verify(pool, repository["files"])
            _verify(pool, state["packages"])
        cached = _hashes(pool, "dnf/*/packages/*.rpm")
        runtime.build(
            image,
            work,
            "find /pool/dnf -path '*/packages/*.rpm' -exec rpm -K --nosignature '{}' + >&2",
            cache=pool,
        )
        state["packages"] = cached
        _publish(pool, state)
        selected = {path.name: digest(path) for path in (work / "packages").glob("*.rpm")}
        return {
            "key": key,
            "reused_rpms": sum(sha in before for sha in selected.values()),
            "downloaded_rpms": len(set(cached.values()) - before),
            "repositories": {
                name: state["repositories"][name] for name in configuration.sections()
            },
        }
