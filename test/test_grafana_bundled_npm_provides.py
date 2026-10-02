"""Tests for Grafana's bundled npm Provides generator."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("package", ["grafana13.1", "grafana13.2"])
def test_generator_reads_patched_source_tree(tmp_path: Path, package: str) -> None:
    generator_path = ROOT_DIR / "rpms" / package / "generate-bundled-npm-provides.py"
    generator = tmp_path / generator_path.name
    shutil.copy2(generator_path, generator)
    spec = tmp_path / f"{package}.spec"
    spec.write_text(
        "# BEGIN generated bundled npm Provides\n"
        "Provides:       bundled(npm(old)) = 1.0.0\n"
        "# END generated bundled npm Provides\n"
    )
    source_dir = tmp_path / "patched-source"
    source_dir.mkdir()
    (source_dir / "package.json").write_text(
        json.dumps({"name": "grafana", "dependencies": {"patched": "^2.0.0"}})
    )
    (source_dir / "yarn.lock").write_text(
        '"patched@npm:^2.0.0":\n'
        '  version: 2.1.0\n'
        '  resolution: "patched@npm:2.1.0"\n'
    )

    result = subprocess.run(
        [sys.executable, generator, "13.1.6", source_dir],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Provides:       bundled(npm(patched)) = 2.1.0" in spec.read_text()
    assert "bundled(npm(old))" not in spec.read_text()
