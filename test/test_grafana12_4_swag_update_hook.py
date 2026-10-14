import os
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("version", "disabled"),
    [("12.4.12", False), ("12.4.13", True), ("12.4.14", True)],
)
def test_post_update_disables_upstreamed_swag_patch(
    tmp_path: Path, version: str, disabled: bool
) -> None:
    hooks = yaml.safe_load(
        (ROOT_DIR / "metadata" / "grafana12.4.update-hooks.yaml").read_text()
    )
    spec = tmp_path / "grafana12.4.spec"
    spec.write_text("Patch14:        grafana-go-openapi-swag-cve.patch\n")
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

    result = subprocess.run(
        ["bash", "-c", hooks["post_update"]],
        cwd=tmp_path,
        env=os.environ | {"UPDATE_NEW_VERSION": version},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert spec.read_text().startswith(
        "#Patch14:" if disabled else "Patch14:"
    )
