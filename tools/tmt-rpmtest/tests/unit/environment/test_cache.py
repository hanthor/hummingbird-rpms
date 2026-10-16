"""Cache reuse, invalidation, publication, and independent runtime ownership."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tmt_rpmtest.environment import cache, lifecycle
from tmt_rpmtest.environment.rpm import DEFAULT_BUILDER, digest


class Builder:
    image = DEFAULT_BUILDER

    def __init__(self):
        self.calls = []

    def prepare(self, packages, repositories, artifacts, source, work):
        self.calls.append(work)
        work.mkdir(parents=True)
        (work / "rootfs.tar").write_bytes(b"assembled root")
        manifest = {
            "packages": packages,
            "candidates": [{"sha256": digest(source / artifact)} for artifact in artifacts],
            "rootfs_sha256": digest(work / "rootfs.tar"),
        }
        (work / "manifest.json").write_text(json.dumps(manifest))


@pytest.fixture
def request_root(tmp_path):
    builder = Builder()

    def request(settings=None, *, directory=None):
        return cache.prepare_root(
            builder,
            settings or {"packages": ["curl", "libcurl"]},
            tmp_path,
            directory or tmp_path / f"root-{len(builder.calls)}",
            tmp_path / "cache",
        )

    return builder, request


def test_reuse_package_set_with_private_copies(tmp_path, request_root):
    builder, request = request_root
    first = request(directory=tmp_path / "first")
    second = request({"packages": ["libcurl", "curl"]}, directory=tmp_path / "second")
    assert first == {"key": second["key"], "hit": False}
    assert second["hit"] is True
    assert len(builder.calls) == 1
    # Changing one consumer's copy cannot change another root or the cache.
    (tmp_path / "first/rootfs.tar").write_bytes(b"modified consumer")
    request(directory=tmp_path / "third")
    assert (tmp_path / "second/rootfs.tar").read_bytes() == b"assembled root"
    assert (tmp_path / "third/rootfs.tar").read_bytes() == b"assembled root"


@pytest.mark.parametrize("change", ["packages", "repositories", "image", "arch", "assembly", "rpm"])
def test_inputs_invalidate_cache(tmp_path, request_root, monkeypatch, change):
    builder, request = request_root
    rpm = tmp_path / "candidate.rpm"
    rpm.write_bytes(b"candidate")
    settings = {"packages": ["curl"], "artifacts": [str(rpm)]}
    first = request(settings)
    if change == "packages":
        settings["packages"].append("gcc")
    elif change == "repositories":
        settings["repositories"] = ["https://example.org/repo"]
    elif change == "image":
        builder.image = "example.org/builder@sha256:" + "b" * 64
    elif change == "arch":
        monkeypatch.setattr(cache.platform, "machine", lambda: "another-architecture")
    elif change == "assembly":
        monkeypatch.setattr(cache, "ASSEMBLE", "new installation settings")
    else:
        rpm.write_bytes(b"rebuilt candidate")
    second = request(settings)
    assert first["key"] != second["key"]
    assert not second["hit"]
    assert len(builder.calls) == 2


def test_failed_build_is_not_published(tmp_path, request_root, monkeypatch):
    builder, request = request_root
    prepare = builder.prepare

    def failure(*args):
        prepare(*args)
        raise OSError("assembly failed")

    monkeypatch.setattr(builder, "prepare", failure)
    with pytest.raises(OSError, match="assembly failed"):
        request(directory=tmp_path / "failed")
    assert not [path for path in (tmp_path / "cache").iterdir() if path.is_dir()]
    monkeypatch.setattr(builder, "prepare", prepare)
    assert not request(directory=tmp_path / "successful")["hit"]


@pytest.mark.parametrize("filename", ["rootfs.tar", "manifest.json"])
def test_corruption_fails_closed(tmp_path, request_root, filename):
    builder, request = request_root
    info = request()
    cached = tmp_path / "cache" / info["key"] / filename
    cached.chmod(0o644)
    cached.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="cached RPM root changed"):
        request(directory=tmp_path / "reuse")
    assert len(builder.calls) == 1
    assert not (tmp_path / "reuse").exists()


def test_candidate_change_during_preparation_is_not_cached(tmp_path, request_root, monkeypatch):
    builder, request = request_root
    rpm = tmp_path / "candidate.rpm"
    rpm.write_bytes(b"before")
    prepare = builder.prepare

    def replace_candidate(*args):
        rpm.write_bytes(b"after")
        prepare(*args)

    monkeypatch.setattr(builder, "prepare", replace_candidate)
    with pytest.raises(ValueError, match="candidates changed"):
        request({"packages": ["curl"], "artifacts": [str(rpm)]})
    assert not [path for path in (tmp_path / "cache").iterdir() if path.is_dir()]


def test_simultaneous_requests_build_once(tmp_path, request_root):
    builder, request = request_root
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda name: request(directory=tmp_path / name), ["a", "b"]))
    assert len(builder.calls) == 1
    assert sorted(info["hit"] for info in results) == [False, True]


def test_cache_hit_has_fresh_ownership_and_runtime_limits(tmp_path, monkeypatch):
    builder = Builder()
    cleaned = []

    class Runtime:
        def __init__(self, *, owner):
            self.owner = owner

        def cleanup(self):
            cleaned.append(self.owner)

    monkeypatch.setattr(lifecycle, "Podman", Runtime)
    monkeypatch.setattr(lifecycle, "RPMBuilder", lambda *args: builder)
    configurations = []
    for name, cpus in [("a", 1), ("b", 2)]:
        directory = tmp_path / name
        lifecycle.prepare(
            {"packages": ["curl"], "cpus": cpus},
            directory,
            tmp_path,
            cache=tmp_path / "cache",
            publish=False,
        )
        configurations.append(json.loads((directory / "session.json").read_text()))
        lifecycle.finish(directory)
    assert len(builder.calls) == 1
    assert configurations[0]["owner"] != configurations[1]["owner"]
    assert [configuration["limits"]["cpus"] for configuration in configurations] == [1, 2]
    assert [configuration["cache"]["hit"] for configuration in configurations] == [False, True]
    assert cleaned == [configuration["owner"] for configuration in configurations]


def test_separate_runs_do_not_reuse_roots(tmp_path):
    builder = Builder()
    for run in ["run-a", "run-b"]:
        info = cache.prepare_root(
            builder,
            {"packages": ["curl"]},
            tmp_path,
            tmp_path / run / "root",
            tmp_path / run / "cache",
        )
        assert not info["hit"]
    assert len(builder.calls) == 2
