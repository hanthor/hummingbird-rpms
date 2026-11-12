"""Integration tests for dist-git update exit status and failure reporting."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UPDATE_SCRIPT = ROOT / "ci" / "dist_git_update_multi_mr.sh"


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _create_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    ci_dir = repo / "ci"
    ci_dir.mkdir(parents=True)
    shutil.copy2(UPDATE_SCRIPT, ci_dir / UPDATE_SCRIPT.name)
    metadata_dir = repo / "metadata"
    metadata_dir.mkdir()
    for package in ("sudo", "tar"):
        (metadata_dir / f"{package}.json").write_text(
            json.dumps({"modification_status": "clean", "version": "1"}) + "\n"
        )
    (repo / "local.txt").write_text("original\n")

    # Exercise the real wrapper with fake package updates: no Fedora, GitLab,
    # or lookaside access is needed, and only this temporary repository changes.
    updater = ci_dir / "dist_git.py"
    updater.write_text(
        """#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

package = sys.argv[-1]
with open(os.environ["DIST_GIT_TEST_UPDATE_CALLS"], "a") as log:
    log.write(package + "\\n")
outcome = json.loads(os.environ["DIST_GIT_TEST_RESULTS"])[package]
state_file = Path(".dist_git_update_state.json")
# Model the dangerous old startup behavior to prove the wrapper never calls
# the next updater while recovery is pending.
state_file.unlink(missing_ok=True)
if outcome == "pending":
    package_dir = Path("rpms") / package
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / f"{package}.spec").write_text("Version: 3\\n")
    Path("local.txt").write_text("pending package changes\\n")
    metadata = json.loads((Path("metadata") / f"{package}.json").read_text())
    metadata["version"] = "2"
    state_file.write_text(json.dumps({"package": package, "metadata": metadata,
                                     "commit_msg": f"Update {package} from 1 to 2"}))
    sys.exit(1)
if outcome == "updated":
    path = Path("metadata") / f"{package}.json"
    metadata = json.loads(path.read_text())
    metadata["version"] = "2"
    path.write_text(json.dumps(metadata) + "\\n")
    subprocess.run(["git", "add", str(path)], check=True)
    subprocess.run(
        ["git", "commit", "-m", f"Update {package} from 1 to 2"],
        check=True, stdout=subprocess.DEVNULL,
    )
    sys.exit(0)
sys.exit(outcome)
"""
    )
    updater.chmod(0o755)

    create_mr = ci_dir / "create_mr.sh"
    create_mr.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["DIST_GIT_TEST_MR_CALLS"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
print("SUCCESS: Created MR at https://gitlab.example/group/rpms/-/merge_requests/42")
sys.exit(int(os.environ["DIST_GIT_TEST_MR_EXIT_CODE"]))
"""
    )
    create_mr.chmod(0o755)

    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.name", "Test User")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "Test fixture")
    return repo


def _run_fixture(
    repo: Path, results: dict[str, int | str], *, mr_exit_code: int = 0,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({
        "CI_COMMIT_BRANCH": "main",
        "CI_DEFAULT_BRANCH": "main",
        "GITLAB_REMOTE_URL": "https://gitlab.example/group/rpms.git",
        "DIST_GIT_TEST_RESULTS": json.dumps(results),
        "DIST_GIT_TEST_UPDATE_CALLS": str(repo / "update_calls.txt"),
        "DIST_GIT_TEST_MR_CALLS": str(repo / "mr_calls.jsonl"),
        "DIST_GIT_TEST_MR_EXIT_CODE": str(mr_exit_code),
    })
    return subprocess.run(
        [str(repo / "ci" / UPDATE_SCRIPT.name)], cwd=repo,
        env=env, capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("exit_code", [1, 2, 3])
def test_failed_updates_without_commits_report_failures(
    tmp_path: Path, exit_code: int,
) -> None:
    repo = _create_fixture(tmp_path)
    initial_commit = _run_git(repo, "rev-parse", "HEAD")

    result = _run_fixture(repo, {"sudo": exit_code, "tar": exit_code})

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Summary" in result.stdout
    assert "Exiting with error due to 2 failed package(s)" in result.stdout
    assert "All packages are up-to-date" not in result.stdout
    reason = "conflict detection failed" if exit_code == 2 else "update failed"
    assert (repo / ".ci_dist_git_update_failures.txt").read_text() == (
        f"sudo ({reason})\ntar ({reason})\n"
    )
    assert (repo / "update_calls.txt").read_text() == "sudo\ntar\n"
    assert not (repo / "mr_calls.jsonl").exists()
    assert _run_git(repo, "rev-parse", "HEAD") == initial_commit


def test_failed_and_unchanged_updates_without_commits_fail(tmp_path: Path) -> None:
    repo = _create_fixture(tmp_path)

    result = _run_fixture(repo, {"sudo": 1, "tar": 0})

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Exiting with error due to 1 failed package(s)" in result.stdout
    assert "All packages are up-to-date" not in result.stdout
    assert (repo / ".ci_dist_git_update_failures.txt").read_text() == "sudo (update failed)\n"
    assert (repo / "update_calls.txt").read_text() == "sudo\ntar\n"
    assert not (repo / "mr_calls.jsonl").exists()


def test_unchanged_updates_remain_successful(tmp_path: Path) -> None:
    repo = _create_fixture(tmp_path)
    initial_commit = _run_git(repo, "rev-parse", "HEAD")
    # Stale diagnostics from a previous run must not accompany a clean result.
    (repo / ".ci_dist_git_update_failures.txt").write_text("previous failure\n")

    result = _run_fixture(repo, {"sudo": 0, "tar": 0})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "No package updates available. All packages are up-to-date." in result.stdout
    assert not (repo / ".ci_dist_git_update_failures.txt").exists()
    assert not (repo / "mr_calls.jsonl").exists()
    assert _run_git(repo, "rev-parse", "HEAD") == initial_commit


def test_failed_updates_without_commits_do_not_reset_local_changes(tmp_path: Path) -> None:
    repo = _create_fixture(tmp_path)
    (repo / "local.txt").write_text("local change\n")

    result = _run_fixture(repo, {"sudo": 1, "tar": 1})

    assert result.returncode == 1, result.stdout + result.stderr
    assert (repo / "local.txt").read_text() == "local change\n"


def test_failed_update_does_not_block_mr_for_successful_update(tmp_path: Path) -> None:
    repo = _create_fixture(tmp_path)

    result = _run_fixture(repo, {"sudo": 1, "tar": "updated"})

    assert result.returncode == 1, result.stdout + result.stderr
    assert (repo / ".ci_dist_git_update_failures.txt").read_text() == "sudo (update failed)\n"
    calls = [json.loads(line) for line in (repo / "mr_calls.jsonl").read_text().splitlines()]
    assert len(calls) == 1
    assert calls[0][calls[0].index("--branch") + 1] == "chore/dist-git-update-tar"
    assert "--auto-merge" in calls[0]


def test_mr_creation_failure_is_still_reported(tmp_path: Path) -> None:
    repo = _create_fixture(tmp_path)

    result = _run_fixture(repo, {"sudo": 0, "tar": "updated"}, mr_exit_code=1)

    assert result.returncode == 1, result.stdout + result.stderr
    assert (repo / ".ci_dist_git_update_failures.txt").read_text() == "tar (MR creation failed)\n"


@pytest.mark.parametrize('earlier_success', [False, True])
def test_pending_recovery_stops_batch_without_resetting_changes(tmp_path: Path, earlier_success: bool) -> None:
    """A failure with state must stop before another update or any Git reset/MR."""
    repo = _create_fixture(tmp_path)
    if earlier_success:
        (repo / 'metadata' / 'zstd.json').write_text(json.dumps({'modification_status': 'clean', 'version': '1'}))
        _run_git(repo, 'add', 'metadata/zstd.json')
        _run_git(repo, 'commit', '-m', 'Add later package')
    initial_count = int(_run_git(repo, 'rev-list', '--count', 'HEAD'))
    pending_package = 'tar' if earlier_success else 'sudo'
    results: dict[str, int | str] = {'sudo': 'updated', 'tar': 'pending', 'zstd': 'updated'} if earlier_success else {
        'sudo': 'pending', 'tar': 'updated',
    }

    result = _run_fixture(repo, results)

    assert result.returncode == 1, result.stdout + result.stderr
    assert (repo / 'update_calls.txt').read_text() == ('sudo\ntar\n' if earlier_success else 'sudo\n')
    state_file = repo / '.dist_git_update_state.json'
    assert state_file.exists(), 'A later update deleted the pending recovery state'
    state = json.loads(state_file.read_text())
    assert state['package'] == pending_package
    assert state['metadata']['version'] == '2'
    assert (repo / 'rpms' / pending_package / f'{pending_package}.spec').read_text() == 'Version: 3\n'
    assert (repo / 'local.txt').read_text() == 'pending package changes\n'
    assert json.loads((repo / 'metadata' / f'{pending_package}.json').read_text())['version'] == '1'
    assert not (repo / 'mr_calls.jsonl').exists(), 'MR processing must not reset pending package edits'
    assert int(_run_git(repo, 'rev-list', '--count', 'HEAD')) == initial_count + int(earlier_success)
    assert (repo / '.ci_dist_git_update_failures.txt').read_text() == f'{pending_package} (update failed)\n'
    assert 'update --continue' in result.stdout + result.stderr


def test_batch_refuses_existing_recovery_state_before_checkout(tmp_path: Path) -> None:
    repo = _create_fixture(tmp_path)
    _run_git(repo, 'checkout', '-b', 'pending-work')
    state_file = repo / '.dist_git_update_state.json'
    state_file.write_text('{"package": "sudo", "commit_msg": "Pending update"}\n')
    state_before = state_file.read_bytes()
    (repo / 'local.txt').write_text('pending manual resolution\n')
    head_before = _run_git(repo, 'rev-parse', 'HEAD')

    result = _run_fixture(repo, {'sudo': 0, 'tar': 0})

    assert result.returncode == 1, result.stdout + result.stderr
    assert not (repo / 'update_calls.txt').exists()
    assert not (repo / 'mr_calls.jsonl').exists()
    assert state_file.read_bytes() == state_before
    assert (repo / 'local.txt').read_text() == 'pending manual resolution\n'
    assert _run_git(repo, 'rev-parse', '--abbrev-ref', 'HEAD') == 'pending-work'
    assert _run_git(repo, 'rev-parse', 'HEAD') == head_before
