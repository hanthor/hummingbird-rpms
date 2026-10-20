"""Universal profiles cover all candidates, including packages without a test suite."""

import json
import subprocess
from pathlib import Path

import pytest

from tmt_rpmtest.execution import universal

REPO = Path(__file__).resolve().parents[5]


@pytest.mark.parametrize("case", ["empty", "foreign", "ambiguous", "invalid"])
def test_candidate_selection_fails_closed(tmp_path, monkeypatch, case):
    if case != "empty":
        (tmp_path / "one.rpm").touch()
    if case == "ambiguous":
        (tmp_path / "two.rpm").touch()

    def query(*args, **kwargs):
        if case == "invalid":
            raise subprocess.CalledProcessError(1, "rpm")
        return subprocess.CompletedProcess(
            [], 0, "hello\tforeign" if case == "foreign" else "hello\tnoarch", ""
        )

    monkeypatch.setattr(universal.subprocess, "run", query)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        universal.candidates(tmp_path)


@pytest.mark.parametrize("failure", [None, "prepare", "contract", "selection", "cleanup"])
def test_each_output_has_an_independent_profile_and_cleanup(tmp_path, monkeypatch, failure):
    outputs = []
    for name in ("hello", "hello-debuginfo"):
        path = tmp_path / f"{name}.rpm"
        path.write_text(name)
        outputs.append((path, name))
    monkeypatch.setattr(universal, "candidates", lambda directory: outputs)
    cleaned = []

    def prepare(settings, environment, root, **kwargs):
        environment.mkdir()
        assert len(settings["artifacts"]) == 2
        assert set(settings["expected_artifacts"]) == {str(path) for path, _ in outputs}
        package = settings["packages"][0]
        (environment / "package").write_text(package)
        if failure == "prepare" and package == "hello":
            raise RuntimeError("dependency resolution failed")
        manifest = {
            "candidates": [
                {
                    "name": name,
                    "selected": name == package and failure != "selection",
                    "sha256": universal.digest(path),
                }
                for path, name in outputs
            ]
        }
        (environment / "manifest.json").write_text(json.dumps(manifest))

    def check(environment):
        if failure == "contract" and (environment / "package").read_text() == "hello":
            raise universal.ContractFailure("missing file")

    def finish(environment):
        cleaned.append(environment)
        if failure == "cleanup":
            raise ExceptionGroup("cleanup", [OSError("failed")])

    monkeypatch.setattr(universal, "prepare", prepare)
    monkeypatch.setattr(universal, "check_contract", check)
    monkeypatch.setattr(universal, "finish", finish)
    directory = tmp_path / "universal"
    if failure:
        with pytest.raises(universal.ContractFailure):
            universal.execute(tmp_path, directory, ["https://example.org/repo"])
    else:
        universal.execute(tmp_path, directory, ["https://example.org/repo"])
    results = json.loads((directory / "results.json").read_text())
    assert [item["package"] for item in results] == ["hello", "hello-debuginfo"]
    assert len(set(cleaned)) == 2
    assert results[0]["result"] == (
        "error" if failure in {"prepare", "cleanup"} else "fail" if failure else "pass"
    )
    if failure not in {"selection", "cleanup"}:
        assert results[1]["result"] == "pass"


def test_repository_configuration_retains_enabled_urls_only(tmp_path):
    (tmp_path / "repos.repo").write_text(
        "[runtime]\nbaseurl=https://example.org/$arch/\n"
        "[source]\nbaseurl=https://example.org/source/\nenabled=0\n"
    )
    assert universal.repositories(tmp_path) == ["https://example.org/$basearch/"]
    with pytest.raises(ValueError, match="No enabled"):
        universal.repositories(tmp_path / "absent")


def test_dependency_repository_priority_precedes_file_order(tmp_path):
    (tmp_path / "a-fedora.repo").write_text("[fedora]\nbaseurl=https://fedora.example.org/\n")
    (tmp_path / "z-hummingbird.repo").write_text(
        "[hummingbird]\nbaseurl=https://hummingbird.example.org/\npriority=10\n"
    )
    assert universal.repositories(tmp_path) == [
        "https://hummingbird.example.org/",
        "https://fedora.example.org/",
    ]


@pytest.mark.parametrize("failure", [None, "package", "binary", "integrity"])
def test_source_identity_is_checked_in_new_framework(tmp_path, monkeypatch, failure):
    def query(argv, **kwargs):
        if "-K" in argv:
            if failure == "integrity":
                raise subprocess.CalledProcessError(1, argv)
            output = "digests OK"
        elif "%{SOURCERPM}" in argv:
            output = "other.src.rpm" if failure == "binary" else "hello-1-1.hum1.src.rpm"
        else:
            output = "hello\thello-1-1.hum1.src.rpm"
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(universal.subprocess, "run", query)
    package = "other" if failure == "package" else "hello"
    if failure:
        with pytest.raises((universal.ContractFailure, subprocess.CalledProcessError)):
            universal.check_source(
                tmp_path / "source.src.rpm", [(tmp_path / "binary.rpm", "hello")], package
            )
    else:
        universal.check_source(
            tmp_path / "source.src.rpm", [(tmp_path / "binary.rpm", "hello")], package
        )


@pytest.mark.parametrize(
    "component,count", [("hello-main", 1), ("curl-main", 3), ("openssl-main", 0)]
)
def test_functional_coverage_does_not_count_package_contracts(component, count):
    coverage = universal.functional_coverage(REPO / "test/rpmtest", component)
    assert len(coverage["scenarios"]) == count
    assert coverage["result"] == ("declared" if count else "missing")
