"""Run the shared NATS suite through each version's local tmt plans."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tmt.utils import yaml_to_list

REPO = Path(__file__).resolve().parents[4]


@pytest.mark.skipif(os.environ.get("RPMTEST_LIVE") != "1", reason="Set RPMTEST_LIVE=1.")
@pytest.mark.parametrize("version", ["2.12", "2.14"])
def test_nats_contract_and_messaging(tmp_path, version):
    run = tmp_path / "run"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tmt",
            "--root",
            str(REPO / f"test/rpmtest/nats-server{version}"),
            "--allow-unsafe-behavior=provision/local",
            "run",
            "--all",
            "--keep",
            "--id",
            str(run),
        ],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout
    for name in ("contract", "messaging"):
        results = yaml_to_list((run / f"plans/{name}/execute/results.yaml").read_text())
        assert {item["name"]: item["result"] for item in results} == {f"/tests/{name}": "pass"}
