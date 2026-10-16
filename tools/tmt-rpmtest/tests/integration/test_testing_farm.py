"""Run a component-filtered CI test with preparation inside its native result."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tmt.utils import to_yaml, yaml_to_dict, yaml_to_list

CANDIDATE = os.environ.get("RPMTEST_CURL_RPM")
REPO = Path(__file__).resolve().parents[4]


@pytest.mark.skipif(not CANDIDATE, reason="Set RPMTEST_CURL_RPM for live CI execution.")
def test_flat_ci_executes_only_selected_component_test(tmp_path):
    root = tmp_path / "metadata"
    (root / ".fmf").mkdir(parents=True)
    (root / ".fmf/version").write_text("1\n")
    (root / "ci").mkdir()
    shutil.copytree(REPO / "test/rpmtest", root / "test/rpmtest")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    shutil.copyfile(Path(CANDIDATE), artifacts / "curl.rpm")
    metadata = yaml_to_dict((REPO / "ci/run_tests_rpm.fmf").read_text())
    # Exercise the real CI discovery and test entry point using local artifacts.
    # The RHEL bootstrap is replaced with this test's installed Python environment.
    metadata["prepare"] = {"how": "shell", "script": "true"}
    metadata["environment"] = {
        "RPMTEST_PYTHON": sys.executable,
        "RPMTEST_RPMS": str(artifacts),
    }
    metadata["finish"]["script"] = (
        '"$RPMTEST_PYTHON" -m tmt_rpmtest.environment finish --directory "${TMT_PLAN_DATA}/rpmtest"'
    )
    (root / "ci/run_tests_rpm.fmf").write_text(to_yaml(metadata))
    run = tmp_path / "run"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tmt",
            "--root",
            str(root),
            "--allow-unsafe-behavior=provision/local",
            "run",
            "--all",
            "--keep",
            "--id",
            str(run),
            "--environment",
            "IMAGE_NAME=curl-main",
            "provision",
            "--how",
            "local",
            "tests",
            "--name",
            "^/curl/tests/transfers$",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=600,
        env={
            key: value
            for key, value in os.environ.items()
            if key
            in {"HOME", "PATH", "LANG", "USER", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}
        },
    )
    assert result.returncode == 0, result.stdout
    results = yaml_to_list((run / "ci/run_tests_rpm/execute/results.yaml").read_text())
    assert {item["name"]: item["result"] for item in results} == {
        "/rpmtest/curl/tests/transfers": "pass"
    }
    environments = list((run / "ci/run_tests_rpm/data/rpmtest").glob("test-*/manifest.json"))
    assert len(environments) == 1
    # The transfer test retains its own minimal package request, without compiler packages.
    import json

    manifest = json.loads(environments[0].read_text())
    assert manifest["packages"] == ["curl"]
    assert any(item["name"] == "curl" and item["selected"] for item in manifest["candidates"])
