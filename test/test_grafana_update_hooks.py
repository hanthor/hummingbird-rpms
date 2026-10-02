"""Regression tests for Grafana's Gorget post-update step."""

import json
import os
import re
import shutil
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


def test_grafana13_2_post_update_provides_use_patched_tree(tmp_path: Path) -> None:
    package = "grafana13.2"
    version = "13.2.3"
    hooks = yaml.safe_load((ROOT_DIR / "metadata" / f"{package}.update-hooks.yaml").read_text())
    spec = tmp_path / f"{package}.spec"
    spec.write_text(
        "# BEGIN generated bundled npm Provides\n"
        "Provides:       bundled(npm(patched)) = 1.0.0\n"
        "# END generated bundled npm Provides\n"
    )
    source_dir = tmp_path / f"grafana-{version}"
    source_dir.mkdir()
    (source_dir / "package.json").write_text(json.dumps({
        "name": "grafana", "dependencies": {"patched": "^1.0.0"},
    }))
    (source_dir / "yarn.lock").write_text(
        '"patched@npm:^1.0.0":\n'
        '  version: 1.0.0\n'
        '  resolution: "patched@npm:1.0.0"\n'
    )
    with tarfile.open(tmp_path / f"grafana-{version}.tar.gz", "w:gz") as archive:
        archive.add(source_dir, arcname=source_dir.name)
    # The hook must call the shared helper before reading the extracted tree.
    (tmp_path / "prepare-yarn-cache-source.sh").write_text(
        '#!/bin/bash\nset -euo pipefail\nsed -i s/1.0.0/1.0.1/g "$1/yarn.lock"\n'
    )
    generator = "generate-bundled-npm-provides.py"
    shutil.copy2(ROOT_DIR / "rpms" / package / generator, tmp_path / generator)
    env = os.environ.copy()
    env["UPDATE_NEW_VERSION"] = version
    result = subprocess.run(
        ["bash", "-c", hooks["post_update"]], cwd=tmp_path, env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "bundled(npm(patched)) = 1.0.1" in spec.read_text()
    assert "bundled(npm(patched)) = 1.0.0" not in spec.read_text()


def test_grafana13_2_frontend_patch_order_matches_spec() -> None:
    package_dir = ROOT_DIR / "rpms" / "grafana13.2"
    declared = re.findall(r"^Patch\d+:\s+(\S+)",
                          (package_dir / "grafana13.2.spec").read_text(), re.MULTILINE)
    frontend = [name for name in declared if re.search(
        r"^\+\+\+ b/(?:package.json|yarn.lock|\.yarnrc.yml)(?:\s|$)",
        (package_dir / name).read_text(), re.MULTILINE,
    )]
    applied = re.findall(r'patch .* < "\$\{package_dir\}/([^"\n]+)"',
                         (package_dir / "prepare-yarn-cache-source.sh").read_text())
    assert applied == frontend


@pytest.mark.parametrize("patch_name", [
    "grafana-yarn-cache.patch", "grafana-xorm-go122-build-constraint.patch",
])
def test_grafana13_2_patch_hunk_counts_are_valid(patch_name: str) -> None:
    patch = ROOT_DIR / "rpms" / "grafana13.2" / patch_name
    result = subprocess.run(
        ["git", "apply", "--numstat", str(patch)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
