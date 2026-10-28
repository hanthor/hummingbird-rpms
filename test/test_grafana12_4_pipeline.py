"""Check Grafana's native bundled Provides output and RPM inclusion."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent


def test_native_provides_follow_resolutions_and_exclude_dev_tools(tmp_path: Path) -> None:
    gorget = shutil.which("gorget")
    if gorget is None:
        pytest.skip("gorget is not installed")
    source = tmp_path / "source"
    source.mkdir()
    (source / "package.json").write_text(json.dumps({
        "name": "grafana",
        "dependencies": {"patched": "^1.0.0"},
        "devDependencies": {"build-tool": "^3.0.0"},
        "resolutions": {"patched": ">=2.1.0"},
    }))
    (source / "yarn.lock").write_text(
        '__metadata:\n  version: 8\n'
        '"patched@npm:>=2.1.0":\n'
        '  version: 2.1.0\n  resolution: "patched@npm:2.1.0"\n'
        '"build-tool@npm:^3.0.0":\n'
        '  version: 3.0.0\n  resolution: "build-tool@npm:3.0.0"\n'
    )
    for args in (
        ["init", "--quiet", "--initial-branch=main"], ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "--quiet", "-m", "fixture"],
    ):
        subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)
    package = tmp_path / "package"
    package.mkdir()
    (package / "grafana12.4.spec").write_text(
        "Name: grafana12.4\nVersion: 12.4.13\nRelease: 1\n"
        "Summary: Test\nLicense: MIT\nSource4: bundled-npm-provides.inc\n"
        "%include %{S:4}\n%description\nTest\n%files\n"
    )
    pipeline = yaml.safe_load(
        (ROOT_DIR / "metadata/grafana12.4.source-pipeline.yaml").read_text()
    )
    fixture_pipeline = tmp_path / "pipeline.yaml"
    fixture_pipeline.write_text(yaml.safe_dump({
        "fetch": [{"type": "git", "repo": str(source), "ref": "main"}],
        "post": pipeline["post"],
    }))
    result = subprocess.run([
        gorget, "--package-dir", str(package), "--pipeline-file", str(fixture_pipeline),
        "--output-dir", str(tmp_path / "output"), "--version", "12.4.13",
    ], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    include = package / "bundled-npm-provides.inc"
    assert include.read_text() == "Provides:       bundled(npm(patched)) = 2.1.0\n"
    if shutil.which("rpmspec"):
        result = subprocess.run([
            "rpmspec", "--define", f"_sourcedir {package}", "-q", "--provides",
            str(package / "grafana12.4.spec"),
        ], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert "bundled(npm(patched)) = 2.1.0" in result.stdout
