"""Standard profiles must work without assumptions about CPU IDs or package size."""

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest


HELPER = Path(__file__).resolve().parents[1] / "ci/build_variance.py"
cpu_profile = runpy.run_path(str(HELPER))["cpu_profile"]


@pytest.mark.parametrize("cpus,selected,cap", [
    ([8], [8], 1),
    ([4, 9], [4], 1),
    ([2, 4, 7, 9], [2, 4, 7], 3),
    (list(range(32)), list(range(28)), 28),
])
def test_varied_profile(cpus: list[int], selected: list[int], cap: int) -> None:
    assert cpu_profile(cpus, "varied") == (selected, cap)
    assert cpu_profile(cpus, "normal") == (cpus, None)


@pytest.mark.parametrize("profile", ["normal", "varied"])
def test_command_inherits_affinity_and_report(tmp_path: Path, profile: str) -> None:
    mock = tmp_path / "mock"
    mock.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "print(json.dumps({'cpu_ids': sorted(os.sched_getaffinity(0)), 'args': sys.argv[1:]}))\n"
    )
    mock.chmod(0o755)
    report = tmp_path / "variance.json"
    available = sorted(os.sched_getaffinity(0))
    selected, cap = cpu_profile(available, profile)
    result = subprocess.run(
        [sys.executable, str(HELPER), "--profile", profile, "--report", str(report),
         "--", "mock", "--hermetic-build", "lock.json", "offline-repo"],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True, text=True, check=True,
    )
    observed = json.loads(result.stdout.splitlines()[-1])
    metadata = json.loads(report.read_text())
    assert observed["cpu_ids"] == selected
    assert metadata["available_cpu_ids"] == available
    assert metadata["effective_cpu_ids"] == selected
    assert metadata["rpm_cpu_cap"] == cap
    assert metadata["cpu_affinity_varied"] == (selected != available)
    assert sorted(os.sched_getaffinity(0)) == available
    if cap:
        assert observed["args"][-2:] == ["--define", f"_smp_ncpus_max {cap}"]
    else:
        assert "--define" not in observed["args"]
