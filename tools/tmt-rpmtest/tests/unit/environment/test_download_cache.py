"""Overlapping requests reuse RPMs and frozen metadata without expanding their roots."""

import json
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest

from tmt_rpmtest.environment import downloads


class Runtime:
    def __init__(self):
        self.metadata_calls = []
        self.resolve_calls = []
        self.failure = None

    def build(self, image, work, script, *args, cache, **kwargs):
        if script == downloads.METADATA:
            self.metadata_calls.append(args[0])
            metadata = cache / "dnf" / f"{args[0]}-fixture" / "repodata"
            metadata.mkdir(parents=True, exist_ok=True)
            (metadata / "repomd.xml").write_text("frozen repository")
            if self.failure == "metadata":
                raise RuntimeError("repository unavailable")
        elif script == downloads.RESOLVE:
            self.resolve_calls.append(args)
            selected = work / "packages"
            if selected.exists():
                shutil.rmtree(selected)
            selected.mkdir()
            packages = cache / "dnf/source-fixture/packages"
            packages.mkdir(parents=True, exist_ok=True)
            for name in {"base", *args}:
                artifact = packages / f"{name}.rpm"
                if not artifact.exists():
                    artifact.write_text(name)
                shutil.copyfile(artifact, selected / artifact.name)
            if self.failure == "resolution":
                raise RuntimeError("download interrupted")
            if self.failure == "mutation":
                next(cache.glob("dnf/*/repodata/repomd.xml")).write_text("changed upstream")


@pytest.fixture
def requests(tmp_path):
    runtime = Runtime()

    def request(name, packages, *, repository="source"):
        work = tmp_path / name
        (work / "repos").mkdir(parents=True)
        (work / "repos/source.repo").write_text(
            f"[{repository}]\nbaseurl=https://example.org/{repository}/\n"
        )
        result = downloads.resolve(runtime, "builder", work, packages, tmp_path / "downloads")
        return result, work

    return runtime, request


def test_overlapping_requests_download_once_and_keep_minimal_selection(requests):
    runtime, request = requests
    first, _ = request("development", ["app", "compiler"])
    second, root = request("runtime", ["app"])
    assert first["downloaded_rpms"] == 3
    assert second["downloaded_rpms"] == 0
    assert second["reused_rpms"] == 2
    assert runtime.metadata_calls == ["source"]
    assert {path.name for path in (root / "packages").iterdir()} == {"app.rpm", "base.rpm"}
    assert first["repositories"] == second["repositories"]


def test_repository_sets_share_the_same_snapshot_and_package_cache(requests):
    runtime, request = requests
    first, _ = request("one", ["app"])
    second, _ = request("two", ["app"], repository="other")
    third, _ = request("three", ["app"])
    assert first["key"] == second["key"] == third["key"]
    assert runtime.metadata_calls == ["source", "other"]


@pytest.mark.parametrize("kind", ["metadata", "package", "missing"])
def test_published_cache_corruption_is_not_silently_repaired(tmp_path, requests, kind):
    runtime, request = requests
    info, _ = request("one", ["app"])
    pool = tmp_path / "downloads" / info["key"]
    path = (
        next(pool.glob("dnf/*/packages/*.rpm"))
        if kind == "package"
        else next(pool.glob("dnf/*/repodata/repomd.xml"))
    )
    if kind == "missing":
        path.unlink()
    else:
        path.write_text("tampered")
    with pytest.raises(ValueError, match="Shared download cache changed"):
        request("two", ["app"])
    assert len(runtime.resolve_calls) == 1


def test_failed_metadata_is_not_published(requests):
    runtime, request = requests
    runtime.failure = "metadata"
    with pytest.raises(RuntimeError, match="unavailable"):
        request("failed", ["app"])
    runtime.failure = None
    result, _ = request("success", ["app"])
    assert len(runtime.metadata_calls) == 2
    assert result["downloaded_rpms"] == 2


def test_failed_resolution_does_not_publish_partial_downloads(requests):
    runtime, request = requests
    runtime.failure = "resolution"
    with pytest.raises(RuntimeError, match="interrupted"):
        request("failed", ["app", "unrelated"])
    runtime.failure = None
    result, root = request("success", ["app"])
    assert result["downloaded_rpms"] == 2
    assert runtime.metadata_calls == ["source"]
    assert not list(root.parent.glob("downloads/*/dnf/*/packages/unrelated.rpm"))


def test_resolution_cannot_refresh_a_frozen_repository(requests):
    runtime, request = requests
    request("first", ["app"])
    runtime.failure = "mutation"
    with pytest.raises(ValueError, match="Shared download cache changed"):
        request("second", ["compiler"])


def test_simultaneous_overlapping_requests_initialize_metadata_once(requests):
    runtime, request = requests
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda name: request(name, ["app"]), ["one", "two"]))
    assert runtime.metadata_calls == ["source"]
    assert sorted(result["downloaded_rpms"] for result, _ in results) == [0, 2]


def test_cache_evidence_records_exact_repository_metadata(tmp_path, requests):
    _, request = requests
    info, _ = request("first", ["app"])
    state = json.loads((tmp_path / "downloads" / info["key"] / "state.json").read_text())
    assert info["repositories"] == state["repositories"]
    assert state["packages"]
