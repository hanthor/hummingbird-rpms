"""Integration tests for the upstream-update shell wrapper."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

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
    },
    {
      "package": "python3.15",
      "error": null,
      "update_error": null,
      "updated": false,
      "warnings": [
        "skipped upstream check: Anitya reported no version matching track_upstream '3.15'"
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
    assert "No updates available; 4 upstream warning(s) reported." in result.stdout
    assert "All tracked packages are up-to-date." not in result.stdout
    assert (repo / ".ci_upstream_update_warnings.txt").read_text() == (
        "4 upstream warning(s) across 3 package(s); "
        "see job log for details\n"
    )


def _schedule_notifications() -> yaml.SequenceNode:
    config = yaml.compose((ROOT / '.gitlab-ci.yml').read_text())
    assert isinstance(config, yaml.MappingNode)
    notifications = next(value for key, value in config.value
                         if key.value == '.notify_schedule_result')
    assert isinstance(notifications, yaml.SequenceNode)
    # Both scheduled jobs must use the same tested notification hook.
    for job_name in ('upstream_version_update', 'dist_git_update'):
        job = next(value for key, value in config.value if key.value == job_name)
        assert isinstance(job, yaml.MappingNode)
        after_script = next(value for key, value in job.value if key.value == 'after_script')
        assert after_script is notifications
    return notifications


def _run_schedule_notification(
    tmp_path: Path,
    *,
    job_name: str = 'upstream_version_update',
    status: str = 'success',
    source: str = 'schedule',
    webhook: str = 'https://slack.example/webhook',
    files: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    notifications = _schedule_notifications()
    script = notifications.value[0]
    assert isinstance(script, yaml.ScalarNode)
    repo = tmp_path / 'repo'
    repo.mkdir()
    for name, content in (files or {}).items():
        (repo / name).write_text(content + '\n')
    bin_dir, curl_log = _create_fake_curl(tmp_path)
    env = os.environ.copy()
    env.update({
        'PATH': f"{bin_dir}:{env['PATH']}", 'CURL_LOG': str(curl_log),
        'CI_PIPELINE_SOURCE': source, 'CI_JOB_STATUS': status,
        'CI_JOB_NAME': job_name, 'CI_JOB_URL': 'https://gitlab.example/job/1',
        'SLACK_WEBHOOK_URL': webhook,
    })

    result = subprocess.run(['bash', '-eu', '-c', script.value], cwd=repo,
                            env=env, capture_output=True, text=True, check=False)

    calls = [json.loads(line) for line in curl_log.read_text().splitlines()] \
        if curl_log.exists() else []
    messages = [json.loads(call[call.index('--data') + 1])['text'] for call in calls]
    return result, messages


@pytest.mark.parametrize('job_name', ['upstream_version_update', 'dist_git_update'])
@pytest.mark.parametrize('with_warnings', [False, True])
def test_successful_schedule_sends_plain_heartbeat(
    tmp_path: Path, job_name: str, with_warnings: bool,
) -> None:
    """Every success sends a heartbeat, without routine warning details."""
    files = {'.ci_upstream_update_warnings.txt': '37 warnings across 7 packages'} \
        if with_warnings else {}
    result, messages = _run_schedule_notification(tmp_path, job_name=job_name, files=files)

    assert result.returncode == 0, result.stderr
    assert messages == [
        f'✅ {job_name} scheduled pipeline succeeded: https://gitlab.example/job/1'
    ]
    if with_warnings:
        assert (tmp_path / 'repo' / '.ci_upstream_update_warnings.txt').read_text() == (
            '37 warnings across 7 packages\n'
        )


@pytest.mark.parametrize('job_name', ['upstream_version_update', 'dist_git_update'])
@pytest.mark.parametrize('with_warnings', [False, True])
def test_failed_schedule_retains_actionable_details(
    tmp_path: Path, job_name: str, with_warnings: bool,
) -> None:
    files = {'.ci_conflict_mrs.txt': 'https://gitlab.example/mr/1'}
    if job_name == 'upstream_version_update':
        files['.ci_upstream_update_failures.txt'] = 'protobuf (source download failed)'
        files['.ci_upstream_update_draft_mrs.txt'] = 'https://gitlab.example/mr/2'
        details = (
            '\nUpstream package update/check failures:\n- protobuf (source download failed)'
            '\nDraft MRs for failed upstream updates:\n- https://gitlab.example/mr/2'
        )
    else:
        files['.ci_dist_git_update_failures.txt'] = 'sudo (MR creation failed)'
        details = '\nFailed packages:\n- sudo (MR creation failed)'
    if with_warnings:
        files['.ci_upstream_update_warnings.txt'] = '37 warnings across 7 packages'
    result, messages = _run_schedule_notification(
        tmp_path, job_name=job_name, status='failed', files=files,
    )

    assert result.returncode == 0, result.stderr
    assert messages == [
        f'⚠️ {job_name} scheduled pipeline failed: https://gitlab.example/job/1'
        '\nMRs needing manual resolution:\n- https://gitlab.example/mr/1'
        + details
    ]


def test_failed_schedule_without_details_still_notifies(tmp_path: Path) -> None:
    result, messages = _run_schedule_notification(tmp_path, status='failed')

    assert result.returncode == 0, result.stderr
    assert messages == [
        '⚠️ upstream_version_update scheduled pipeline failed: https://gitlab.example/job/1'
    ]


@pytest.mark.parametrize('source', ['push', 'web', 'merge_request_event'])
@pytest.mark.parametrize('status', ['success', 'failed'])
def test_non_scheduled_run_does_not_notify(tmp_path: Path, source: str, status: str) -> None:
    result, messages = _run_schedule_notification(
        tmp_path, source=source, status=status,
        files={'.ci_upstream_update_warnings.txt': '37 warnings across 7 packages'},
    )

    assert result.returncode == 0, result.stderr
    assert messages == []


@pytest.mark.parametrize('status', ['canceled', 'running', 'pending', 'skipped'])
def test_unfinished_or_canceled_schedule_does_not_claim_success(
    tmp_path: Path, status: str,
) -> None:
    result, messages = _run_schedule_notification(
        tmp_path, status=status,
        files={'.ci_upstream_update_warnings.txt': '37 warnings across 7 packages'},
    )

    assert result.returncode == 0, result.stderr
    assert messages == []


@pytest.mark.parametrize('status', ['success', 'failed'])
def test_schedule_without_webhook_skips_notification(tmp_path: Path, status: str) -> None:
    result, messages = _run_schedule_notification(tmp_path, status=status, webhook='')

    assert result.returncode == 0, result.stderr
    assert messages == []
    assert 'SLACK_WEBHOOK_URL is not set, skipping Slack notification' in result.stdout


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


def _run_update_error_fixture(
    tmp_path: Path, error: str,
) -> tuple[subprocess.CompletedProcess[str], str]:
    repo, ci_dir = _create_success_fixture(tmp_path)
    (repo / "failure.json").write_text(json.dumps({"results": [{
        "package": "opentelemetry-collector-contrib",
        "current_version": "0.162.0",
        "upstream_version": "0.163.1",
        "error": None,
        "update_error": error,
        "updated": False,
        "warnings": [],
    }]}))
    checker = ci_dir / "check_upstream_versions.py"
    checker.write_text("#!/bin/bash\ncat failure.json\nexit 2\n")

    description_path = tmp_path / "description.txt"
    create_mr = ci_dir / "create_mr.sh"
    create_mr.write_text("""#!/bin/bash
while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--description" ]]; then
        printf '%s' "$2" > "$MR_DESCRIPTION_LOG"
        exit 0
    fi
    shift
done
exit 1
""")
    create_mr.chmod(0o755)
    env = os.environ.copy()
    env.pop("WORKQUEUE_SERVICE_URL", None)
    env.pop("WORKQUEUE_OIDC_TOKEN", None)
    env["MR_DESCRIPTION_LOG"] = str(description_path)
    env["CI_JOB_URL"] = "https://gitlab.example/jobs/123"

    result = subprocess.run(
        [str(ci_dir / UPDATE_SCRIPT.name), "--create-mrs"], cwd=repo,
        capture_output=True, text=True, env=env,
    )

    assert result.returncode == 1, result.stderr
    return result, description_path.read_text()


def test_large_update_error_keeps_diagnostic_mr_push_option_bounded(
    tmp_path: Path,
) -> None:
    error = "go mod tidy failed: missing internal/obi-src/go.mod\n" + (
        "module error repeated by Go\n" * 10_000
    )
    result, description = _run_update_error_fixture(tmp_path, error)

    assert result.returncode == 1, result.stderr  # The package update failed.
    assert "go mod tidy failed: missing internal/obi-src/go.mod" in description
    assert "[truncated; see full CI job log]" in description
    assert "https://gitlab.example/jobs/123" in description
    assert len(description.encode()) < 8_000


@pytest.mark.parametrize("error", [
    pytest.param("", id="empty"),
    pytest.param("short failure", id="short"),
    pytest.param("first line\nsecond line\n", id="newlines"),
    pytest.param(r"literal \n stays literal", id="literal-backslash"),
    pytest.param("x" * 3999, id="raw-below-limit"),
    pytest.param("x" * 4000, id="raw-at-limit"),
    pytest.param("x" * 4001, id="raw-over-limit"),
    pytest.param("\n" * 1999 + "x", id="escaped-below-limit"),
    pytest.param("\n" * 2000, id="escaped-at-limit"),
    pytest.param("\n" * 2000 + "x", id="escaped-over-limit"),
    pytest.param("x" * 3999 + "\n", id="limit-splits-escape"),
    pytest.param("é" * 4000, id="unicode-at-limit"),
    pytest.param("🙂" * 4001, id="unicode-over-limit"),
    pytest.param("e\u0301" * 2001, id="unicode-combining"),
])
def test_update_error_excerpt_preserves_escaped_limit(tmp_path: Path, error: str) -> None:
    result, description = _run_update_error_fixture(tmp_path, error)

    # Keep the original contract: escape first, then limit the escaped text.
    expected = error.replace("\n", "\\n")
    if len(expected) > 4000:
        expected = expected[:4000] + "\\n...[truncated; see full CI job log]"

    assert result.returncode == 1, result.stderr
    assert f"Error:\\n```\\n{expected}\\n```\\n\\n" in description
