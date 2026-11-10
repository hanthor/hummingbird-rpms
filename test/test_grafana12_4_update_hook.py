"""Grafana 12.4 switches to its generated frontend patch on update."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT_DIR / "rpms/grafana12.4"


def test_12_4_14_activation_preserves_backend_patches(tmp_path: Path) -> None:
    package_dir = tmp_path / "grafana12.4"
    package_dir.mkdir()
    (package_dir / "frontend-overrides.json").write_text(
        (PACKAGE_DIR / "frontend-overrides.json").read_text()
    )
    spec = package_dir / "grafana12.4.spec"
    spec.write_text(
        "Version:        12.4.14\n"
        + "".join(f"Patch{number}: old-{number}.patch\n" for number in range(27))
    )

    subprocess.run(
        [sys.executable, str(ROOT_DIR / "ci/grafana_frontend.py"),
         str(package_dir), str(tmp_path), "12.4.14", "activate-spec"],
        check=True,
    )

    updated = spec.read_text()
    for number in (0, 1, 2, 26):
        assert f"Patch{number}: old-{number}.patch" in updated
    config = json.loads((package_dir / "frontend-overrides.json").read_text())
    for number in config["legacy_patches"]:
        assert f"#Patch{number}: old-{number}.patch" in updated
    assert "Patch27:        grafana-frontend-%{version}.patch" in updated


def test_post_update_activates_and_applies_frontend(tmp_path: Path) -> None:
    hooks = yaml.safe_load((ROOT_DIR / "metadata/grafana12.4.update-hooks.yaml").read_text())
    source_dir = tmp_path / "grafana-12.4.14"
    source_dir.mkdir()
    (source_dir / "package.json").write_text("{}\n")
    with tarfile.open(tmp_path / "grafana-12.4.14.tar.gz", "w:gz") as archive:
        archive.add(source_dir, arcname=source_dir.name)
    helper = tmp_path / "prepare-yarn-cache-source.sh"
    helper.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> calls\n')
    generator = tmp_path / "generate-bundled-npm-provides.py"
    generator.write_text("#!/bin/sh\nexit 0\n")
    generator.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c", hooks["post_update"]],
        cwd=tmp_path,
        env=os.environ | {"UPDATE_NEW_VERSION": "12.4.14"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls[0] == ". 12.4.14 --activate-spec"
    assert calls[1].endswith("/grafana-12.4.14 12.4.14")
