"""Testing Farm discovers only component tests and prepares their own roots."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tmt_rpmtest.execution import test as execution

REPO = Path(__file__).resolve().parents[5]


@pytest.mark.parametrize(
    "component,expected",
    [
        ("curl-main", {"contract", "transfers", "development"}),
        ("hello-main", {"contract", "greeting"}),
        ("openssl-main", set()),
        ("future-example-main", {"smoke"}),
        ("nats-server2-12-main", {"contract", "messaging"}),
        ("nats-server2-14-main", {"contract", "messaging"}),
    ],
)
def test_native_discovery_excludes_unrelated_components(tmp_path, component, expected):
    from tmt.utils import yaml_to_list

    root = tmp_path / "metadata"
    (root / ".fmf").mkdir(parents=True)
    (root / ".fmf/version").write_text("1\n")
    (root / "ci").mkdir()
    shutil.copy(REPO / "ci/run_tests_rpm.fmf", root / "ci/run_tests_rpm.fmf")
    shutil.copytree(REPO / "test/rpmtest", root / "test/rpmtest")
    # A new package needs no change to the shared CI discovery plan.
    new_test = root / "test/rpmtest/future-example/tests/smoke/main.fmf"
    new_test.parent.mkdir(parents=True)
    new_test.write_text("test: ':'\ncomponent: future-example-main\n")
    run = tmp_path / "run"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tmt",
            "--root",
            str(root),
            "run",
            "--id",
            str(run),
            "--environment",
            f"IMAGE_NAME={component}",
            "discover",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
        env={
            key: value
            for key, value in os.environ.items()
            if key in {"HOME", "PATH", "LANG", "USER"}
        },
    )
    assert result.returncode == 0, result.stdout
    plans = list(run.glob("ci/*/discover/tests.yaml"))
    assert len(plans) == 1
    tests = yaml_to_list(plans[0].read_text())
    selected = [test for test in tests if test.get("component")]
    assert {test["name"].rsplit("/", 1)[-1] for test in selected} == expected
    assert all(test["component"] == [component] for test in selected)
    assert len(tests) == len(expected) + 1  # Shared RPM checks always run.


@pytest.mark.parametrize("failure", [None, "prepare", "assertion", "interrupt"])
def test_test_environment_is_private_and_cleanup_runs(tmp_path, monkeypatch, failure):
    plan = REPO / "test/rpmtest/curl/plans/development.fmf"
    calls = []
    monkeypatch.delenv("RPMTEST_DIRECTORY", raising=False)
    monkeypatch.setenv("TMT_PLAN_DATA", str(tmp_path))
    monkeypatch.setenv("RPMTEST_RPMS", str(tmp_path / "candidates"))

    def prepare(settings, directory, root, *, publish):
        assert settings["packages"] == ["libcurl-devel", "gcc"]
        assert settings["artifacts"] == [str(tmp_path / "candidates/*.rpm")]
        assert publish is False
        assert root == REPO / "test/rpmtest"
        calls.append(("prepare", directory))
        if failure == "prepare":
            raise OSError("failed preparation")

    def script(path, **kwargs):
        assert Path(os.environ["RPMTEST_DIRECTORY"]) == calls[-1][1]
        assert kwargs["run_name"] == "__main__"
        if failure == "assertion":
            raise AssertionError("failed assertion")
        if failure == "interrupt":
            raise KeyboardInterrupt()

    monkeypatch.setattr(execution, "prepare", prepare)
    monkeypatch.setattr(execution.runpy, "run_path", script)
    monkeypatch.setattr(execution, "finish", lambda directory: calls.append(("finish", directory)))
    errors = {"prepare": OSError, "assertion": AssertionError, "interrupt": KeyboardInterrupt}
    if failure:
        with pytest.raises(errors[failure]):
            execution.execute(plan, tmp_path / "test.py")
    else:
        execution.execute(plan, tmp_path / "test.py")
        execution.execute(plan, tmp_path / "test.py")
        assert calls[0][1] != calls[2][1]
    assert [name for name, _ in calls] == ["prepare", "finish"] * (2 if failure is None else 1)
    assert "RPMTEST_DIRECTORY" not in os.environ


def test_local_plugin_environment_is_reused(tmp_path, monkeypatch):
    monkeypatch.setenv("RPMTEST_DIRECTORY", str(tmp_path))
    calls = []
    monkeypatch.setattr(execution.runpy, "run_path", lambda path, **kwargs: calls.append(path))
    execution.execute(tmp_path / "unused-plan.fmf", tmp_path / "test.py")
    assert calls == [str(tmp_path / "test.py")]
    assert os.environ["RPMTEST_DIRECTORY"] == str(tmp_path)


def test_finish_recovers_all_test_environments_after_one_cleanup_fails(tmp_path, monkeypatch):
    from tmt_rpmtest.environment import lifecycle

    (tmp_path / ".first.owner").write_text("first")
    (tmp_path / ".second.owner").write_text("second")
    cleaned = []

    class Runtime:
        def __init__(self, *, owner):
            self.owner = owner

        def cleanup(self):
            cleaned.append(self.owner)
            if self.owner == "first":
                raise OSError("cleanup failed")

    monkeypatch.setattr(lifecycle, "Podman", Runtime)
    with pytest.raises(ExceptionGroup, match="cleanup failed"):
        lifecycle.finish(tmp_path)
    assert set(cleaned) == {"first", "second"}


@pytest.mark.parametrize(
    "package,expected",
    [
        ("curl", {"contract", "transfers", "development"}),
        ("hello", {"contract", "greeting"}),
        ("nats-server2.12", {"contract", "messaging"}),
        ("nats-server2.14", {"contract", "messaging"}),
    ],
)
def test_shared_root_local_plans_select_only_their_package(tmp_path, package, expected):
    import re

    from tmt.utils import yaml_to_list

    root = tmp_path / "rpmtest"
    shutil.copytree(REPO / "test/rpmtest", root)
    run = tmp_path / "run"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tmt",
            "--root",
            str(root),
            "run",
            "--id",
            str(run),
            "discover",
            "plans",
            "--name",
            f"^/{re.escape(package)}/plans/",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    discovered = list(run.glob("*/plans/*/discover/tests.yaml"))
    assert len(discovered) == len(expected)
    selected = {test["name"] for path in discovered for test in yaml_to_list(path.read_text())}
    assert selected == {f"/{package}/tests/{name}" for name in expected}
