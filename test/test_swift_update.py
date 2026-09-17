"""Regression tests for the swift-lang version update helper."""

import json
import os
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
UPDATE_SCRIPT = ROOT_DIR / "rpms" / "swift-lang" / "update-swift-version.sh"


def _run_update(tmp_path: Path, version: str, schemes: dict[str, dict]) -> subprocess.CompletedProcess[str]:
    config = {
        "branch-schemes": schemes,
        "repos": {
            "swift": {"remote": {"id": "swiftlang/swift"}},
            "cmark": {"remote": {"id": "swiftlang/swift-cmark"}},
        },
    }
    config_path = tmp_path / "update-checkout-config.json"
    config_path.write_text(json.dumps(config))

    spec_path = tmp_path / "swift-lang.spec"
    spec_path.write_text(
        "Version: 0\n"
        "# Begin forge sources\n"
        "old source data\n"
        "# End forge sources\n"
        "%{lua:\n"
        "for i = 0, 53 do\n"
        "end\n"
        "}\n"
        "%prep\n"
        "%{lua:\n"
        "for i = 0, 53 do\n"
        "end\n"
        "}\n"
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        "#!/bin/sh\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --output|-o) output=$2; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "cp \"$SWIFT_TEST_CONFIG\" \"$output\"\n"
    )
    fake_curl.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["SWIFT_TEST_CONFIG"] = str(config_path)
    return subprocess.run(
        [str(UPDATE_SCRIPT), version],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _scheme(revision: str) -> dict:
    return {
        "repos": {
            "swift": revision,
            "cmark": revision,
        }
    }


def test_update_swift_version_uses_major_minor_scheme(tmp_path: Path) -> None:
    result = _run_update(
        tmp_path,
        "6.3.3",
        {"release/6.3": _scheme("release/6.3")},
    )

    assert result.returncode == 0, result.stderr
    assert "Using checkout branch scheme release/6.3" in result.stdout
    spec = (tmp_path / "swift-lang.spec").read_text()
    assert "Version: 6.3.3" in spec
    assert "%global forgeurl1  https://github.com/swiftlang/swift-cmark" in spec
    assert "%global tag1       swift-%{version0}-RELEASE" in spec
    assert spec.count("for i = 0, 1 do") == 2
    assert "for i = 0, 53 do" not in spec


def test_update_swift_version_prefers_exact_scheme(tmp_path: Path) -> None:
    result = _run_update(
        tmp_path,
        "6.4.0",
        {
            "release/6.4.0": _scheme("release/6.4.0"),
            "release/6.4.x": _scheme("release/6.4.x"),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "Using checkout branch scheme release/6.4.0" in result.stdout
    assert "Version: 6.4.0" in (tmp_path / "swift-lang.spec").read_text()


def test_update_swift_version_reports_missing_scheme(tmp_path: Path) -> None:
    result = _run_update(tmp_path, "6.5.0", {"main": _scheme("main")})

    assert result.returncode == 1
    assert "has no supported release branch scheme" in result.stdout
    assert "release/6.5.0, release/6.5, release/6.5.x" in result.stdout
