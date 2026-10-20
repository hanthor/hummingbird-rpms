"""Artifact bootstrap distinguishes unsupported architectures from broken inputs."""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[5]


@pytest.mark.parametrize("case", ["native", "foreign", "empty", "multiple-sources", "tampered"])
def test_artifact_disposition_and_transfer_integrity(tmp_path, case):
    tools = tmp_path / "bin"
    tools.mkdir()
    query = tools / "rpm"
    query.write_text("#!/bin/sh\nprintf '%s' \"$FIXTURE_ARCH\"\n")
    query.chmod(0o755)
    if case == "tampered":
        copy = tools / "cp"
        copy.write_text('#!/bin/sh\n/usr/bin/cp "$@"\necho changed >> "$2/$(basename "$1")"\n')
        copy.chmod(0o755)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    if case != "empty":
        (artifacts / "curl.rpm").write_text("binary")
        (artifacts / "curl.src.rpm").write_text("source")
    if case == "multiple-sources":
        (artifacts / "second.src.rpm").write_text("source")
    data = tmp_path / "data"
    data.mkdir()
    variables = tmp_path / "environment"
    environment = {key: value for key, value in os.environ.items() if key != "IMAGE_URL"}
    environment.update(
        PATH=f"{tools}:{os.environ['PATH']}",
        IMAGE_NAME="curl-main",
        RPMTEST_LOCAL_RPMS=str(artifacts),
        TMT_PLAN_DATA=str(data),
        TMT_PLAN_ENVIRONMENT_FILE=str(variables),
        FIXTURE_ARCH="foreign" if case == "foreign" else platform.machine(),
        RPMTEST_BOOTSTRAP_PYTHON=sys.executable,
    )
    result = subprocess.run(
        ["bash", str(REPO / "ci/prepare_rpm_artifacts.sh")],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if case in {"multiple-sources", "tampered", "empty"}:
        assert result.returncode != 0
        assert any(
            message in result.stderr
            for message in ["multiple source RPMs", "changed during transfer", "no RPM artifacts"]
        )
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        values = dict(line.split("=", 1) for line in variables.read_text().splitlines())
        assert values["RPMTEST_NO_NATIVE"] == ("true" if case == "foreign" else "false")


def test_remote_tag_is_resolved_once_before_manifest_and_files_are_fetched(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    for name, script in {
        "rpm": "printf noarch",
        "curl": ":",
        "tar": '/usr/bin/cp "$FIXTURE_ORAS" "$HOME/.local/bin/oras"',
    }.items():
        executable = tools / name
        executable.write_text("#!/bin/sh\n" + script + "\n")
        executable.chmod(0o755)
    oras = tmp_path / "oras"
    oras.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """
import hashlib
import json
import os
import pathlib
import sys
args = sys.argv[1:]
reference = 'registry.example/app@sha256:' + 'a' * 64
with open(os.environ['FIXTURE_ORAS_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[0] == 'resolve':
    print(reference)
elif args[:2] == ['manifest', 'fetch']:
    assert args[2] == reference
    print(json.dumps({'layers': [
        {'annotations': {'org.opencontainers.image.title': name},
         'digest': 'sha256:' + hashlib.sha256(name.encode()).hexdigest()}
        for name in ['curl.rpm', 'curl.src.rpm']
    ]}))
elif args[0] == 'pull':
    assert args[1] == reference
    destination = pathlib.Path(args[3])
    for name in ['curl.rpm', 'curl.src.rpm']:
        (destination / name).write_text(name)
else:
    raise AssertionError(args)
"""
    )
    oras.chmod(0o755)
    data = tmp_path / "data"
    data.mkdir()
    log = tmp_path / "oras.log"
    environment = os.environ | {
        "PATH": f"{tools}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "FIXTURE_ORAS": str(oras),
        "FIXTURE_ORAS_LOG": str(log),
        "IMAGE_NAME": "curl-main",
        "IMAGE_URL": "registry.example/app:moving-tag",
        "TMT_PLAN_DATA": str(data),
        "TMT_PLAN_ENVIRONMENT_FILE": str(tmp_path / "environment"),
        "RPMTEST_BOOTSTRAP_PYTHON": sys.executable,
    }
    result = subprocess.run(
        ["bash", str(REPO / "ci/prepare_rpm_artifacts.sh")],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [call[0] for call in calls] == ["resolve", "manifest", "pull"]
    assert (data / "rpm-artifacts.json").is_file()
