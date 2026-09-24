"""Regression tests for the nodejs22 source pipeline."""

import shutil
import subprocess
import tarfile
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
PIPELINE_FILE = ROOT_DIR / "metadata" / "nodejs22.source-pipeline.yaml"
PACKAGE_DIR = ROOT_DIR / "rpms" / "nodejs22"


def _packaging_scripts_command() -> list[str]:
    pipeline = yaml.safe_load(PIPELINE_FILE.read_text())
    assert isinstance(pipeline, dict)
    transforms = pipeline.get("transform")
    assert isinstance(transforms, list)

    for transform in transforms:
        if not isinstance(transform, dict):
            continue
        if "packaging-scripts.tar.gz" not in transform.get("outputs", []):
            continue
        command = transform.get("command")
        assert isinstance(command, list)
        assert all(isinstance(argument, str) for argument in command)
        return command

    raise AssertionError("nodejs22 packaging-scripts transform not found")


def test_packaging_scripts_archive_normalizes_gitlab_checkout_modes(
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "nodejs22"
    packaging_dir = package_dir / "packaging"
    packaging_dir.mkdir(parents=True)

    source_modes = {
        "Makefile": 0o666,
        "packaging/make-nodejs-tarball.sh": 0o777,
        "packaging/fill-versions.sh": 0o777,
    }
    for relative_path, mode in source_modes.items():
        destination = package_dir / relative_path
        shutil.copyfile(PACKAGE_DIR / relative_path, destination)
        destination.chmod(mode)

    command = [
        argument.replace("${PACKAGE_DIR}", str(package_dir))
        for argument in _packaging_scripts_command()
    ]
    subprocess.run(command, cwd=tmp_path, check=True)

    with tarfile.open(tmp_path / "packaging-scripts.tar.gz") as archive:
        member_modes = {member.name: member.mode for member in archive.getmembers()}

    assert member_modes == {
        "Makefile": 0o644,
        "packaging/make-nodejs-tarball.sh": 0o755,
        "packaging/fill-versions.sh": 0o755,
    }
