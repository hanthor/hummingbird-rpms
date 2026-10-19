"""CLI validation and propagation of RPM parallelism into both build paths."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "ci/build_rpms.sh"


@pytest.mark.parametrize("value", [None, "0", "-1", "1.5", "two", "2;touch injected", "02"])
def test_jobs_requires_positive_integer(value: str | None) -> None:
    args = ["bash", str(SCRIPT), "--jobs"]
    if value is not None:
        args.append(value)
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "--jobs requires a positive integer" in result.stderr


@pytest.mark.parametrize("hermetic", [False, True])
@pytest.mark.parametrize("jobs,profile", [(None, "normal"), ("3", "normal"), (None, "varied")])
def test_jobs_reaches_mock(tmp_path: Path, hermetic: bool, jobs: str | None, profile: str) -> None:
    repo = tmp_path / "repo"
    for directory in ["ci", "mock", "rpms/probe", ".git", "bin"]:
        (repo / directory).mkdir(parents=True)
    shutil.copyfile(SCRIPT, repo / "ci/build_rpms.sh")
    (repo / "mock/mock.cfg").write_text("# test config\n")
    (repo / "rpms/probe/probe.spec").write_text("Name: probe\n")
    log = tmp_path / "podman.jsonl"
    podman = repo / "bin/podman"
    podman.write_text(
        "#!/usr/bin/python3\n"
        "import json, os, sys\n"
        "with open(os.environ['PROBE_COMMAND_LOG'], 'a') as output:\n"
        "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    podman.chmod(0o755)
    setfacl = repo / "bin/setfacl"
    setfacl.write_text("#!/bin/sh\nexit 0\n")
    setfacl.chmod(0o755)
    args = ["bash", str(repo / "ci/build_rpms.sh"), "--nocheck", "--variance-profile", profile]
    if hermetic:
        args.append("--hermetic")
    if jobs:
        args.extend(["--jobs", jobs])
    args.append("probe")
    result = subprocess.run(
        args, capture_output=True, text=True, check=False,
        env={**os.environ, "PATH": f"{repo}/bin:{os.environ['PATH']}",
             "PROBE_COMMAND_LOG": str(log)},
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    if hermetic:
        mock_calls = [call for call in calls if "mock" in call]
        assert len(mock_calls) == 2
        for call in mock_calls:
            assert "--nocheck" in call
            if jobs:
                assert call[call.index("--define") + 1] == "_smp_build_ncpus 3"
            else:
                assert "--define" not in call
        build = next(call for call in mock_calls if "--hermetic-build" in call)
        assert build[build.index("--profile") + 1] == profile
        calculation = next(call for call in mock_calls if "--calculate-build-dependencies" in call)
        assert "--profile" not in calculation
    else:
        inner_script = next(call[-1] for call in calls if "bash" in call)
        subprocess.run(["bash", "-n", "-c", inner_script], check=True)
        assert f"--profile '{profile}'" in inner_script
        if jobs:
            assert "--define '_smp_build_ncpus 3'" in inner_script
        else:
            assert "--define" not in inner_script


@pytest.mark.parametrize("args", [
    ["--variance-profile"], ["--variance-profile", "unknown"],
    ["--variance-profile", "varied", "--jobs", "4", "probe"],
])
def test_invalid_variance_options(args: list[str]) -> None:
    result = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "Error:" in result.stderr
