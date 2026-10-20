"""Reuse verified RPM roots within one run, without sharing runtime ownership."""

import fcntl
import glob
import hashlib
import json
import platform
import shutil
import tempfile
from pathlib import Path

from tmt_rpmtest.environment.rpm import ASSEMBLE, QUERY, RESOLVE, digest

FILES = ("rootfs.tar", "manifest.json")


def prepare_root(builder, settings: dict, source: Path, work: Path, cache: Path | None):
    packages = settings.get("packages", [])
    repositories = settings.get("repositories", [])
    artifacts = settings.get("artifacts", [])
    if cache is None:
        builder.prepare(packages, repositories, artifacts, source, work)
        return None

    candidates = set()
    for pattern in artifacts:
        matches = sorted(glob.glob(str(source / pattern)))
        if not matches:
            raise ValueError(f"No RPM artifacts match {pattern!r}.")
        candidates.update(digest(Path(path)) for path in matches)
    inputs = {
        "version": 2,
        "architecture": platform.machine(),
        "builder": builder.image,
        "packages": sorted(set(packages)),
        "repositories": repositories,
        "candidates": sorted(candidates),
        "assembly": [RESOLVE, ASSEMBLE, QUERY],
    }
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    cache.mkdir(parents=True, exist_ok=True)
    entry = cache / key
    # Lock each request independently; publish complete entries with one rename.
    with (cache / f".{key}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        hit = entry.exists()
        if hit:
            hashes = json.loads((entry / "hashes.json").read_text())
            if hashes != {name: digest(entry / name) for name in FILES}:
                raise ValueError("The cached RPM root changed before reuse.")
            work.mkdir(parents=True, exist_ok=False)
            for name in FILES:
                shutil.copyfile(entry / name, work / name)
            if hashes != {name: digest(work / name) for name in FILES}:
                raise ValueError("The cached RPM root changed while it was copied.")
        else:
            builder.prepare(
                packages, repositories, artifacts, source, work, download_cache=cache / "downloads"
            )
            manifest = json.loads((work / "manifest.json").read_text())
            if {item["sha256"] for item in manifest["candidates"]} != candidates:
                raise ValueError("RPM candidates changed during cache preparation.")
            if digest(work / "rootfs.tar") != manifest["rootfs_sha256"]:
                raise ValueError("The prepared RPM root changed before caching.")
            hashes = {name: digest(work / name) for name in FILES}
            with tempfile.TemporaryDirectory(prefix=f".{key}-", dir=cache) as temporary:
                staging = Path(temporary) / "entry"
                staging.mkdir()
                for name in FILES:
                    shutil.copyfile(work / name, staging / name)
                    (staging / name).chmod(0o444)
                if hashes != {name: digest(staging / name) for name in FILES}:
                    raise ValueError("The prepared RPM root changed while it was cached.")
                (staging / "hashes.json").write_text(json.dumps(hashes) + "\n")
                staging.rename(entry)
    return {"key": key, "hit": hit}
