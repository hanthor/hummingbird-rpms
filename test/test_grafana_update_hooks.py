"""Regression tests for Grafana's Gorget post-update step."""

import os
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
PIPELINE_FILE = ROOT_DIR / "metadata" / "grafana13.1.source-pipeline.yaml"


def _run_post_update(tmp_path: Path, version: str) -> str:
    pipeline = yaml.safe_load(PIPELINE_FILE.read_text())
    (tmp_path / "grafana13.1.spec").write_text(
        "Patch14:        grafana-ngalert-datasource-authz-cve.patch\n"
    )
    source_dir = tmp_path / f"grafana-{version}"
    source_dir.mkdir()
    (source_dir / "package.json").write_text("{}\n")
    with tarfile.open(tmp_path / f"grafana-{version}.tar.gz", "w:gz") as archive:
        archive.add(source_dir, arcname=source_dir.name)

    helper = tmp_path / "prepare-yarn-cache-source.sh"
    helper.write_text("#!/bin/sh\nexit 0\n")
    generator = tmp_path / "generate-bundled-npm-provides.py"
    generator.write_text("#!/bin/sh\nexit 0\n")
    generator.chmod(0o755)

    env = os.environ.copy()
    env["VERSION"] = version
    env["PACKAGE_DIR"] = str(tmp_path)
    result = subprocess.run(
        ["bash", "-c", pipeline["post"][0]["command"][-1]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return (tmp_path / "grafana13.1.spec").read_text()


@pytest.mark.parametrize("version", ["13.1.4", "13.1.6"])
def test_post_update_disables_upstreamed_ngalert_patch(
    tmp_path: Path, version: str
) -> None:
    spec = _run_post_update(tmp_path, version)

    assert "#Patch14:        grafana-ngalert-datasource-authz-cve.patch" in spec


def test_post_update_keeps_ngalert_patch_for_older_release(tmp_path: Path) -> None:
    spec = _run_post_update(tmp_path, "13.1.3")

    assert "Patch14:        grafana-ngalert-datasource-authz-cve.patch" in spec
    assert "#Patch14:" not in spec
