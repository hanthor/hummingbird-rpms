"""Integration tests for the upstream-update shell wrapper."""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPDATE_SCRIPT = ROOT / "ci" / "upstream_update_multi_mr.sh"


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_warning_only_run_does_not_claim_all_packages_are_up_to_date(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    ci_dir = repo / "ci"
    ci_dir.mkdir(parents=True)
    shutil.copy2(UPDATE_SCRIPT, ci_dir / UPDATE_SCRIPT.name)

    checker = ci_dir / "check_upstream_versions.py"
    checker.write_text(
        """#!/bin/bash
cat <<'EOF'
{
  "results": [
    {
      "package": "libfabric",
      "error": null,
      "update_error": null,
      "updated": false,
      "warnings": [
        "skipped upstream version 2.6.0: required source artifact is not published"
      ]
    }
  ]
}
EOF
"""
    )
    checker.chmod(0o755)

    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.name", "Test User")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "add", "ci")
    _run_git(repo, "commit", "-m", "Test fixture")

    result = subprocess.run(
        [str(ci_dir / UPDATE_SCRIPT.name)],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "No updates available; 1 source warning(s) reported." in result.stdout
    assert "All tracked packages are up-to-date." not in result.stdout
    assert (repo / ".ci_upstream_update_warnings.txt").read_text() == (
        "libfabric: skipped upstream version 2.6.0: "
        "required source artifact is not published\n"
    )
