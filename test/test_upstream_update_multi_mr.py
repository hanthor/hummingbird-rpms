"""Integration tests for the upstream-update shell wrapper."""

import json
import os
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


def _create_success_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    ci_dir = repo / "ci"
    ci_dir.mkdir(parents=True)
    shutil.copy2(UPDATE_SCRIPT, ci_dir / UPDATE_SCRIPT.name)

    # A Bash stand-in keeps the fixture focused on the wrapper while retaining
    # the production checker's executable name.
    checker = ci_dir / "check_upstream_versions.py"
    checker.write_text(
        """#!/bin/bash
cat <<'EOF'
{
  "results": [
    {
      "package": "protobuf",
      "error": null,
      "update_error": null,
      "updated": true,
      "warnings": []
    }
  ]
}
EOF
exit 1
"""
    )
    checker.chmod(0o755)

    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.name", "Test User")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "add", "ci")
    _run_git(repo, "commit", "-m", "Test fixture")
    return repo, ci_dir


def _create_fake_curl(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_log = tmp_path / "curl.jsonl"
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["CURL_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")

url = next((arg for arg in args if arg.startswith("https://")), "")
if url.endswith("/merge_requests"):
    print(json.dumps([{
        "iid": 4493,
        "source_project_id": int(os.environ["MR_SOURCE_PROJECT_ID"]),
        "draft": True,
        "title": "Draft: investigate failed upstream update for protobuf",
    }]))
elif url.endswith("/merge_requests/4493/diffs"):
    print(os.environ["MR_DIFFS_RESPONSE"])
else:
    print("{}")
"""
    )
    fake_curl.chmod(0o755)
    return bin_dir, curl_log


def _run_success_fixture(
    repo: Path,
    ci_dir: Path,
    bin_dir: Path,
    curl_log: Path,
    diffs: list[dict[str, str]],
    *,
    mr_source_project_id: str = "73447720",
    use_workqueue: bool = True,
    create_mr_exit_code: int = 0,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "CHORE_MR_GITLAB_TOKEN": "test-token",
            "CI_API_V4_URL": "https://gitlab.example/api/v4",
            "CI_PROJECT_ID": "73447720",
            "CURL_LOG": str(curl_log),
            "MR_DIFFS_RESPONSE": json.dumps(diffs),
            "MR_SOURCE_PROJECT_ID": mr_source_project_id,
        }
    )
    if use_workqueue:
        env.update(
            {
                "WORKQUEUE_OIDC_TOKEN": "test-oidc-token",
                "WORKQUEUE_SERVICE_URL": "https://workqueue.example",
            }
        )
    else:
        checker = ci_dir / "check_upstream_versions.py"
        checker.write_text(
            checker.read_text().replace(
                "exit 1\n",
                'echo updated > protobuf.txt\n'
                'git add protobuf.txt\n'
                'git commit -m "Update protobuf to 36.2" >/dev/null\n'
                "exit 1\n",
            )
        )
        create_mr = ci_dir / "create_mr.sh"
        create_mr.write_text(f"#!/bin/bash\nexit {create_mr_exit_code}\n")
        create_mr.chmod(0o755)
    return subprocess.run(
        [str(ci_dir / UPDATE_SCRIPT.name), "--create-mrs"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        env=env,
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
        "skipped upstream version 2.6.0: required source artifact is not published",
        "skipped upstream version 2.5.0: required source artifact is not published"
      ]
    },
    {
      "package": "other-package",
      "error": null,
      "update_error": null,
      "updated": false,
      "warnings": [
        "skipped upstream version 3.0: required source artifact is not published"
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
    assert "No updates available; 3 source warning(s) reported." in result.stdout
    assert "All tracked packages are up-to-date." not in result.stdout
    assert (repo / ".ci_upstream_update_warnings.txt").read_text() == (
        "3 skipped candidate version(s) across 2 package(s); "
        "see job log for details\n"
    )


def test_successful_update_closes_empty_diagnostic_mr_and_deletes_branch(
    tmp_path: Path,
) -> None:
    repo, ci_dir = _create_success_fixture(tmp_path)
    bin_dir, curl_log = _create_fake_curl(tmp_path)

    result = _run_success_fixture(repo, ci_dir, bin_dir, curl_log, [])

    assert result.returncode == 0, result.stderr
    assert (
        "Closed resolved diagnostic MR !4493 and deleted "
        "chore/upstream-update-failure-protobuf"
    ) in result.stdout

    calls = [json.loads(line) for line in curl_log.read_text().splitlines()]
    assert any(
        "--request" in call
        and "PUT" in call
        and "--data-urlencode" in call
        and "state_event=close" in call
        and call[-1].endswith("/merge_requests/4493")
        for call in calls
    )
    assert any(
        "--request" in call
        and "DELETE" in call
        and call[-1].endswith(
            "/repository/branches/chore%2Fupstream-update-failure-protobuf"
        )
        for call in calls
    )


def test_successful_update_keeps_diagnostic_mr_with_maintainer_changes(
    tmp_path: Path,
) -> None:
    repo, ci_dir = _create_success_fixture(tmp_path)
    bin_dir, curl_log = _create_fake_curl(tmp_path)

    result = _run_success_fixture(
        repo,
        ci_dir,
        bin_dir,
        curl_log,
        [{"new_path": "rpms/protobuf/protobuf.spec"}],
    )

    assert result.returncode == 0
    assert (
        "WARNING: Resolved diagnostic MR !4493 for protobuf has changes; "
        "leaving it open"
    ) in result.stderr

    calls = [json.loads(line) for line in curl_log.read_text().splitlines()]
    assert not any(
        "--request" in call and ("PUT" in call or "DELETE" in call)
        for call in calls
    )


def test_successful_update_ignores_matching_diagnostic_mr_from_fork(
    tmp_path: Path,
) -> None:
    repo, ci_dir = _create_success_fixture(tmp_path)
    bin_dir, curl_log = _create_fake_curl(tmp_path)

    result = _run_success_fixture(
        repo,
        ci_dir,
        bin_dir,
        curl_log,
        [],
        mr_source_project_id="12345678",
    )

    assert result.returncode == 0
    calls = [json.loads(line) for line in curl_log.read_text().splitlines()]
    assert not any(
        "--request" in call and ("PUT" in call or "DELETE" in call)
        for call in calls
    )


def test_git_push_cleanup_waits_for_successful_mr_creation(tmp_path: Path) -> None:
    failed_repo, failed_ci_dir = _create_success_fixture(tmp_path / "failed")
    failed_bin_dir, failed_curl_log = _create_fake_curl(tmp_path / "failed")

    failed_result = _run_success_fixture(
        failed_repo,
        failed_ci_dir,
        failed_bin_dir,
        failed_curl_log,
        [],
        use_workqueue=False,
        create_mr_exit_code=1,
    )

    assert failed_result.returncode == 1
    assert not failed_curl_log.exists()

    created_repo, created_ci_dir = _create_success_fixture(tmp_path / "created")
    created_bin_dir, created_curl_log = _create_fake_curl(tmp_path / "created")

    created_result = _run_success_fixture(
        created_repo,
        created_ci_dir,
        created_bin_dir,
        created_curl_log,
        [],
        use_workqueue=False,
    )

    assert created_result.returncode == 0, created_result.stderr
    assert "Closed resolved diagnostic MR !4493" in created_result.stdout

    existing_repo, existing_ci_dir = _create_success_fixture(tmp_path / "existing")
    existing_bin_dir, existing_curl_log = _create_fake_curl(tmp_path / "existing")

    existing_result = _run_success_fixture(
        existing_repo,
        existing_ci_dir,
        existing_bin_dir,
        existing_curl_log,
        [],
        use_workqueue=False,
        create_mr_exit_code=2,
    )

    assert existing_result.returncode == 0, existing_result.stderr
    assert "Closed resolved diagnostic MR !4493" in existing_result.stdout


def test_stale_patch_update_creates_review_required_mr(tmp_path: Path) -> None:
    repo, ci_dir = _create_success_fixture(tmp_path)
    checker = ci_dir / "check_upstream_versions.py"
    checker.write_text(checker.read_text().replace(
        "exit 1\n", 'echo updated > protobuf.txt\n'
        'git add protobuf.txt\n'
        'git commit -m "STALE PATCH: Update protobuf to 36.2" >/dev/null\n'
        "exit 1\n",
    ))
    args_log = tmp_path / "mr-args.txt"
    create_mr = ci_dir / "create_mr.sh"
    create_mr.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{args_log}"\n')
    create_mr.chmod(0o755)

    result = subprocess.run(
        [str(ci_dir / UPDATE_SCRIPT.name), "--create-mrs"], cwd=repo,
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    args = args_log.read_text().splitlines()
    assert "--draft" in args
    assert "--auto-merge" not in args
    assert "STALE PATCH: chore(rpms): Update protobuf to 36.2" in args
