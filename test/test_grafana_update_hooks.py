"""Regression tests for package-specific Grafana update hooks."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
HOOKS_FILE = ROOT_DIR / "metadata" / "grafana13.1.update-hooks.yaml"


def _run_post_update(tmp_path: Path, version: str) -> str:
    hooks = yaml.safe_load(HOOKS_FILE.read_text())
    (tmp_path / "grafana13.1.spec").write_text(
        "Patch14:        grafana-ngalert-datasource-authz-cve.patch\n"
    )
    generator = tmp_path / "generate-bundled-npm-provides.py"
    generator.write_text("#!/bin/sh\nexit 0\n")
    generator.chmod(0o755)

    env = os.environ.copy()
    env["UPDATE_NEW_VERSION"] = version
    result = subprocess.run(
        ["bash", "-c", hooks["post_update"]],
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
