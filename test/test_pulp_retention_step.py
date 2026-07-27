"""Safety checks for the local Pulp repository-retention stepper."""

import argparse
import importlib.util
import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests


SCRIPT = Path(__file__).resolve().parents[1] / "ci/pulp-setup/step_repository_retention.py"
spec = importlib.util.spec_from_file_location("step_repository_retention", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)


def marker(limit: int, completed: datetime) -> dict:
    return module.parse_marker(
        json.dumps({"state": "completed", "to": limit, "completed_at": completed.isoformat()})
    )


def test_first_step_is_bounded_and_targets_repository_versions():
    step = module.plan_step(
        None, 4959, None, NOW, target=100, step_size=10,
        cooldown=timedelta(hours=24),
    )
    assert step.next_limit == 4948


def test_step_counts_deletions_when_limit_exceeds_existing_versions():
    step = module.plan_step(
        1141, 1130, marker(1141, NOW - timedelta(days=2)), NOW,
        target=100, step_size=20, cooldown=timedelta(hours=24),
    )
    assert step.next_limit == 1109  # 1,130 total - 20 deletes - 1 protected


def test_cooldown_prevents_another_step():
    step = module.plan_step(
        4949, 4950, marker(4949, NOW - timedelta(hours=23)), NOW,
        target=100, step_size=10, cooldown=timedelta(hours=24),
    )
    assert step.next_limit is None
    assert "cooldown" in step.reason


def test_zero_cooldown_allows_next_step_immediately():
    step = module.plan_step(
        4949, 4950, marker(4949, NOW), NOW,
        target=100, step_size=10, cooldown=timedelta(0),
    )
    assert step.next_limit == 4939


def test_cooldown_shows_remaining_time_rounded_up_to_a_second():
    step = module.plan_step(
        9, 10,
        marker(9, NOW - timedelta(hours=9, minutes=14, seconds=26, milliseconds=500)),
        NOW, target=5, step_size=1, cooldown=timedelta(hours=24),
    )
    assert step.reason == "cooldown until 2026-11-04T05:45:33.500000+00:00 (in 14hr 45m 34s)"


@pytest.mark.parametrize("limit,stored", [(4900, None), (4900, 4950)])
def test_untracked_or_externally_changed_limit_fails_closed(limit, stored):
    with pytest.raises(ValueError):
        module.plan_step(
            limit, 4901, marker(stored, NOW - timedelta(days=2)) if stored else None,
            NOW, target=100, step_size=10, cooldown=timedelta(hours=24),
        )


def test_target_is_never_undershot():
    step = module.plan_step(
        105, 106, marker(105, NOW - timedelta(days=2)), NOW,
        target=100, step_size=10, cooldown=timedelta(hours=24),
    )
    assert step.next_limit == 100


def test_existing_finite_limit_requires_explicit_adoption():
    with pytest.raises(ValueError, match="completion label is missing"):
        module.plan_step(
            10, 11, None, NOW, target=5, step_size=10,
            cooldown=timedelta(hours=24),
        )
    step = module.plan_step(
        10, 11, None, NOW, target=5, step_size=10,
        cooldown=timedelta(hours=24), adopt_existing=True,
    )
    assert step.next_limit == 5


def test_pending_step_requires_reconciliation():
    pending = module.parse_marker('{"state":"pending","to":4949}')
    with pytest.raises(ValueError, match="pending"):
        module.plan_step(
            None, 4959, pending, NOW, target=100, step_size=10,
            cooldown=timedelta(hours=24),
        )


class FakeClient:
    def __init__(self, fail_update=False):
        self.limit = None
        self.labels = {}
        self.fail_update = fail_update
        self.marker_calls = 0

    def show(self, name):
        return {
            "name": name,
            "retain_repo_versions": self.limit,
            "pulp_labels": dict(self.labels),
        }

    def version_count(self, repository):
        return 4959 if self.limit is None else 4949

    def update_limit(self, name, limit):
        if self.fail_update:
            raise RuntimeError("task failed")
        self.limit = limit

    def simple_protected_latest(self, repository):
        return True

    def repository_tasks(self, name, *args):
        return []

    def set_marker(self, name, value):
        self.marker_calls += 1
        self.labels[module.LABEL_KEY] = json.dumps(value, separators=(",", ":"))


def test_label_written_only_after_successful_cleanup():
    args = argparse.Namespace(target=100, step=10, cooldown_hours=24,
                              max_wait_seconds=1800, apply=True, adopt_existing=False,
                              direct_update=True)
    client = FakeClient()
    with patch.object(module, "utc_now", return_value=NOW):
        result = module.run_one(client, "x86_64", args)
    assert client.limit == 4948
    assert client.marker_calls == 2
    assert "now 4949 versions" in result

    failed = FakeClient(fail_update=True)
    with patch.object(module, "utc_now", return_value=NOW):
        with pytest.raises(RuntimeError, match="task failed"):
            module.run_one(failed, "x86_64", args)
    assert failed.marker_calls == 1
    assert json.loads(failed.labels[module.LABEL_KEY])["state"] == "pending"


def test_daily_rerun_skips_after_completed_step():
    args = argparse.Namespace(target=100, step=10, cooldown_hours=24,
                              max_wait_seconds=1800, apply=True, adopt_existing=False,
                              direct_update=True)
    client = FakeClient()
    with patch.object(module, "utc_now", return_value=NOW):
        module.run_one(client, "x86_64", args)
        result = module.run_one(client, "x86_64", args)
    assert "cooldown" in result
    assert "last step None -> 4948 at 2026-11-03T15:00:00Z" in result
    assert client.marker_calls == 2


def test_long_update_stops_after_recording_completion():
    args = argparse.Namespace(target=100, step=10, cooldown_hours=24,
                              max_wait_seconds=1800, apply=True, adopt_existing=False,
                              direct_update=True)
    client = FakeClient()
    with patch.object(module, "utc_now", return_value=NOW), patch.object(
        module.time, "monotonic", side_effect=[0, 1801]
    ):
        with pytest.raises(RuntimeError, match="stopping before other repositories"):
            module.run_one(client, "x86_64", args)
    assert client.limit == 4948
    assert json.loads(client.labels[module.LABEL_KEY])["state"] == "completed"


def test_cli_label_set_can_succeed_without_stdout():
    client = object.__new__(module.PulpClient)
    client.config = Path("/tmp/pulp-config.toml")
    client.domain = "public-hummingbird"
    completed = subprocess.CompletedProcess(args=["pulp"], returncode=0, stdout="", stderr="")
    with patch.object(module.subprocess, "run", return_value=completed):
        assert client.cli("rpm", "repository", "label", "set") is None


def test_waiting_pulp_task_reports_heartbeat():
    class WaitingProcess:
        returncode = 0

        def __init__(self):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def wait(self, timeout):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd=["pulp"], timeout=timeout)
            return 0

    task_href = "/api/pulp/public-hummingbird/api/v3/tasks/01a1a348-4402-7a9e-8710-6e5e0591d584/"

    def fake_popen(command, *, stdout, stderr, text):
        assert "--background" in command and "--timeout" not in command
        stdout.write("{}")
        stdout.flush()
        stderr.write(f"Started background task {task_href}\n")
        stderr.flush()
        return process

    client = object.__new__(module.PulpClient)
    client.config = Path("/tmp/pulp-config.toml")
    client.domain = "public-hummingbird"
    client.base_url = "https://example.com"
    response = Mock()
    response.json.return_value = {"pulp_href": task_href, "state": "completed"}
    client.session = Mock()
    client.session.get.return_value = response
    process = WaitingProcess()
    with patch.object(module.subprocess, "Popen", side_effect=fake_popen), patch.object(
        module.time, "monotonic", side_effect=[0, 31, 32]
    ), patch.object(module, "progress") as log:
        assert client.cli("rpm", "repository", "update", wait=True,
                          activity="source: setting retention limit")["state"] == "completed"
    assert process.calls == 2
    assert log.call_count == 2
    assert "task ID 01a1a348-4402-7a9e-8710-6e5e0591d584" in log.call_args_list[0].args[0]
    assert "CLI dispatch still running after 31s" in log.call_args_list[1].args[0]
    assert client.session.get.call_args.kwargs["timeout"] == module.TASK_REQUEST_TIMEOUT


def test_failed_pulp_task_reports_id_before_error():
    class FailedProcess:
        returncode = 1

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def wait(self, timeout):
            return 1

    task_href = "/api/pulp/public-hummingbird/api/v3/tasks/01a1a348-4402-7a9e-8710-6e5e0591d584/"

    def fake_popen(command, *, stdout, stderr, text):
        stderr.write(f"Started background task {task_href}\nError: Task {task_href} failed\n")
        stderr.flush()
        return FailedProcess()

    client = object.__new__(module.PulpClient)
    client.config = Path("/tmp/pulp-config.toml")
    client.domain = "public-hummingbird"
    client.base_url = "https://example.com"
    response = Mock()
    response.json.return_value = {
        "pulp_href": task_href, "state": "failed", "error": {"description": "delete failed"},
    }
    client.session = Mock()
    client.session.get.return_value = response
    with patch.object(module.subprocess, "Popen", side_effect=fake_popen), patch.object(
        module.time, "monotonic", return_value=0
    ), patch.object(module, "progress") as log:
        with pytest.raises(RuntimeError, match="01a1a348-4402-7a9e-8710-6e5e0591d584"):
            client.cli("rpm", "repository", "update", wait=True,
                       activity="source: setting retention limit")
    assert "task ID 01a1a348-4402-7a9e-8710-6e5e0591d584" in log.call_args.args[0]


def test_task_poll_recovers_after_network_disconnect():
    task_href = "/api/pulp/public-hummingbird/api/v3/tasks/01a1a348-4402-7a9e-8710-6e5e0591d584/"
    client = object.__new__(module.PulpClient)
    client.domain = "public-hummingbird"
    client.base_url = "https://example.com"
    response = Mock()
    response.json.return_value = {"pulp_href": task_href, "state": "completed"}
    client.session = Mock()
    client.session.get.side_effect = [requests.ConnectionError("VPN changed"), response]
    with patch.object(module.time, "monotonic", side_effect=[0, 1]), patch.object(
        module.time, "sleep"
    ) as sleep, patch.object(module, "progress") as log:
        assert client.wait_for_task(task_href, "source: deleting version 1303")["state"] == "completed"
    assert client.session.get.call_count == 2
    sleep.assert_called_once_with(module.TASK_POLL_SECONDS)
    assert "cannot query task" in log.call_args_list[0].args[0]
    assert "connection restored" in log.call_args_list[1].args[0]


def test_task_poll_stops_after_parallel_watch_interrupt():
    client = object.__new__(module.PulpClient)
    client.domain = "public-hummingbird"
    client.stop_event = threading.Event()
    client.stop_event.set()
    client.session = Mock()
    task_href = "/api/pulp/public-hummingbird/api/v3/tasks/01a1a348-4402-7a9e-8710-6e5e0591d584/"
    with pytest.raises(RuntimeError, match="watch interrupted"):
        client.wait_for_task(task_href, "source: checking update")
    client.session.get.assert_not_called()


def test_dispatch_timeout_without_task_id_leaves_pending_for_review():
    class StalledProcess:
        returncode = -9
        killed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def wait(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired(cmd=["pulp"], timeout=timeout)
            return self.returncode

        def kill(self):
            self.killed = True

    process = StalledProcess()
    client = object.__new__(module.PulpClient)
    client.config = Path("/tmp/pulp-config.toml")
    client.domain = "public-hummingbird"
    with patch.object(module.subprocess, "Popen", return_value=process), patch.object(
        module.time, "monotonic", side_effect=[0, 61]
    ):
        with pytest.raises(module.PulpDispatchUncertain, match="outcome is uncertain and the pending label needs review"):
            client.cli("rpm", "repository", "version", "destroy", wait=True)
    assert process.killed


def test_dispatch_timeout_with_task_id_polls_existing_task():
    class StalledProcess:
        returncode = -9
        killed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def wait(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired(cmd=["pulp"], timeout=timeout)
            return self.returncode

        def kill(self):
            self.killed = True

    task_href = "/api/pulp/public-hummingbird/api/v3/tasks/01a1a348-4402-7a9e-8710-6e5e0591d584/"
    process = StalledProcess()

    def fake_popen(command, *, stdout, stderr, text):
        stderr.write(f"Started background task {task_href}\n")
        stderr.flush()
        return process

    client = object.__new__(module.PulpClient)
    client.config = Path("/tmp/pulp-config.toml")
    client.domain = "public-hummingbird"
    with patch.object(module.subprocess, "Popen", side_effect=fake_popen), patch.object(
        module.time, "monotonic", side_effect=[0, 61]
    ), patch.object(client, "wait_for_task", return_value={"state": "completed"}) as poll:
        assert client.cli("rpm", "repository", "version", "destroy", wait=True) == {
            "state": "completed",
        }
    assert process.killed
    poll.assert_called_once_with(task_href, "Pulp task")


TASK_UUID = "01a1a222-cea3-762d-adf8-920635c69a84"
TASK_HREF = f"/api/pulp/konflux-release-integration-tests/api/v3/tasks/{TASK_UUID}/"
REPO_PRN = "prn:rpm.rpmrepository:0199543d-f4a4-79fd-b7fd-7a0af259cfab"


class ReconcileClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.limit = 5
        self.labels[module.LABEL_KEY] = json.dumps({
            "state": "pending", "from": 10, "to": 5,
            "started_at": "2026-11-03T14:58:00Z",
        }, separators=(",", ":"))
        self.current_count = 6
        self.latest_task_href = TASK_HREF
        self.active = []
        self.update_task = {
            "pulp_href": TASK_HREF,
            "name": module.UPDATE_TASK_NAME,
            "state": "failed",
            "error": {"error_code": module.IMMEDIATE_TIMEOUT_CODE},
            "reserved_resources_record": [REPO_PRN],
            "pulp_created": "2026-11-03T14:59:00Z",
            "finished_at": "2026-11-03T14:59:20Z",
        }

    def show(self, name):
        return {**super().show(name), "prn": REPO_PRN}

    def version_count(self, repository):
        return self.current_count

    def task(self, uuid):
        assert uuid == TASK_UUID
        return self.update_task

    def repository_tasks(self, name, *args):
        if "--state-in" in args:
            return self.active
        return [{"pulp_href": self.latest_task_href}]


def reconcile_args(**overrides):
    values = dict(target=5, before_count=11, expected_count=6,
                  task_uuid=TASK_UUID, apply=False)
    values.update(overrides)
    return argparse.Namespace(**values)


def test_timed_out_step_requires_explicit_reconciliation_and_records_failure():
    client = ReconcileClient()
    with patch.object(module, "utc_now", return_value=NOW):
        preview = module.reconcile_one(client, "source", reconcile_args())
        assert "dry run" in preview
        assert client.marker_calls == 0
        result = module.reconcile_one(client, "source", reconcile_args(apply=True))
    assert "label reconciled" in result
    label = json.loads(client.labels[module.LABEL_KEY])
    assert label["state"] == "completed"
    assert (label["from"], label["to"], label["before_count"], label["after_count"]) == (10, 5, 11, 6)
    assert label["reconciliation"] == {
        "task_href": TASK_HREF, "task_state": "failed", "error_code": "PLP0024",
        "task_finished_at": "2026-11-03T14:59:20Z",
    }


@pytest.mark.parametrize("change,reason", [
    (lambda c: setattr(c, "current_count", 7), "version count"),
    (lambda c: c.update_task["error"].update(error_code="OTHER"), "PLP0024"),
    (lambda c: c.update_task.update(reserved_resources_record=[]), "did not reserve"),
    (lambda c: setattr(c, "latest_task_href", "/newer/task/"), "newer repository task"),
    (lambda c: c.active.append({"state": "running"}), "still active"),
])
def test_reconciliation_fails_closed_when_evidence_disagrees(change, reason):
    client = ReconcileClient()
    change(client)
    with pytest.raises(ValueError, match=reason):
        module.reconcile_one(client, "source", reconcile_args(apply=True))
    assert client.marker_calls == 0
    assert json.loads(client.labels[module.LABEL_KEY])["state"] == "pending"


def test_watch_automatically_reconciles_verified_pending_update():
    class PendingWatchClient(ReconcileClient):
        domain = "konflux-release-integration-tests"

        def repository_tasks(self, name, *args):
            if "--state-in" in args:
                return []
            return [self.update_task]

        def wait_for_task(self, task_href, activity):
            assert task_href == TASK_HREF
            raise module.PulpTaskFailed(self.update_task, "immediate task timed out")

        def update_limit(self, name, limit):
            raise AssertionError("the pending update must not be submitted again")

    client = PendingWatchClient()
    pending = json.loads(client.labels[module.LABEL_KEY])
    pending.update(strategy="limit_update", before_count=11,
                   expected_after_count=6, planned_prune_count=5)
    client.labels[module.LABEL_KEY] = json.dumps(pending, separators=(",", ":"))
    args = predelete_args(target=5, step=1, direct_update=True, watch=True, apply=True)
    result = module.run_one(client, "source", args)
    assert "label reconciled" in result
    label = json.loads(client.labels[module.LABEL_KEY])
    assert label["state"] == "completed"
    assert label["reconciliation"]["task_href"] == TASK_HREF


def test_watch_pending_update_remains_blocked_if_count_differs():
    class PendingWatchClient(ReconcileClient):
        domain = "konflux-release-integration-tests"

        def repository_tasks(self, name, *args):
            return [self.update_task]

        def wait_for_task(self, task_href, activity):
            raise module.PulpTaskFailed(self.update_task, "immediate task timed out")

    client = PendingWatchClient()
    pending = json.loads(client.labels[module.LABEL_KEY])
    pending.update(strategy="limit_update", before_count=11,
                   expected_after_count=6, planned_prune_count=5)
    client.labels[module.LABEL_KEY] = json.dumps(pending, separators=(",", ":"))
    client.current_count = 7
    with pytest.raises(ValueError, match="version count 7 differs"):
        module.run_one(client, "source", predelete_args(
            target=5, step=1, direct_update=True, watch=True, apply=True,
        ))
    assert json.loads(client.labels[module.LABEL_KEY])["state"] == "pending"


class PredeleteClient(FakeClient):
    def __init__(self, fail_delete=False):
        super().__init__()
        self.limit = 10
        self.count = 11
        self.deleted = []
        self.latest_number = 11
        self.fail_delete = fail_delete
        self.updated = False

    def show(self, name):
        return {
            **super().show(name),
            "pulp_href": "/repository/123/",
            "versions_href": "/repository/123/versions/",
            "latest_version_href": f"/repository/123/versions/{self.latest_number}/",
        }

    def version_count(self, repository):
        return self.count

    def repository_tasks(self, name, *args):
        return []

    def simple_protected_latest(self, repository):
        return True

    def oldest_versions(self, name, count):
        return [number for number in range(1, self.latest_number + 1)
                if number not in self.deleted][:count]

    def delete_version(self, name, number):
        if self.fail_delete:
            raise RuntimeError("delete task failed")
        self.deleted.append(number)
        self.count -= 1

    def update_limit(self, name, limit):
        assert self.count == limit + 1
        self.updated = True
        self.limit = limit


def predelete_args(**overrides):
    values = dict(target=5, step=1, cooldown_hours=24, max_wait_seconds=1800,
                  apply=False, adopt_existing=True, direct_update=False)
    values.update(overrides)
    return argparse.Namespace(**values)


def test_worker_predelete_then_limit_update(capsys):
    client = PredeleteClient()
    assert "delete 1 version (oldest)" in module.run_one(client, "aarch64", predelete_args())
    assert client.marker_calls == 0
    with patch.object(module, "utc_now", return_value=NOW):
        result = module.run_one(client, "aarch64", predelete_args(apply=True))
    assert "now 10 versions" in result
    progress_output = capsys.readouterr().err
    assert "deleting version 1 (1/1)" in progress_output
    assert "deleted version 1 (1/1)" in progress_output
    assert "setting retention limit 10 -> 9" in progress_output
    assert client.deleted == [1] and client.updated
    label = json.loads(client.labels[module.LABEL_KEY])
    assert label["state"] == "completed"
    assert label["deleted_versions"] == [1]
    assert (label["from"], label["to"], label["before_count"], label["after_count"]) == (10, 9, 11, 10)


def test_direct_update_prunes_without_worker_deletes(capsys):
    class DirectClient(PredeleteClient):
        def update_limit(self, name, limit):
            self.limit = limit
            self.count = limit + 1
            self.updated = True
            return {
                "started_at": "2026-11-03T15:00:01Z",
                "finished_at": "2026-11-03T15:00:09Z",
            }

    client = DirectClient()
    with patch.object(module, "utc_now", return_value=NOW):
        result = module.run_one(client, "source", predelete_args(
            target=5, step=2, direct_update=True, apply=True,
        ))
    assert "limit 10 -> 8; prune 2 versions" in result
    assert "now 9 versions" in result
    assert "removed 2 versions; Pulp update task 8.0s (4.00s/version, 0.25 versions/s)" in result
    assert "prune metrics: removed 2 versions" in capsys.readouterr().err
    assert client.deleted == [] and client.updated
    label = json.loads(client.labels[module.LABEL_KEY])
    assert label["strategy"] == "limit_update"
    assert label["planned_prune_count"] == 2
    assert label["pruned_count"] == 2
    assert label["pulp_task_seconds"] == 8.0
    assert (label["before_count"], label["after_count"]) == (11, 9)


def test_prune_metrics_handle_zero_or_missing_task_duration():
    phrase, metrics = module.prune_metrics(5, 5, {
        "started_at": "2026-11-03T15:00:01Z",
        "finished_at": "2026-11-03T15:00:09Z",
    })
    assert phrase == "removed 0 versions; Pulp update task 8.0s"
    assert metrics == {"pruned_count": 0, "pulp_task_seconds": 8.0}
    assert module.prune_metrics(5, 3, None) == (
        "removed 2 versions", {"pruned_count": 2},
    )
    assert module.task_runtime_seconds({
        "started_at": "2026-11-03T15:00:09Z",
        "finished_at": "2026-11-03T15:00:01Z",
    }) is None


def test_direct_timeout_reconciles_only_verified_prune():
    class TimedOutClient(PredeleteClient):
        def show(self, name):
            return {**super().show(name), "prn": REPO_PRN}

        def update_limit(self, name, limit):
            self.limit = limit
            self.count = limit + 1
            task = {
                "pulp_href": TASK_HREF, "name": module.UPDATE_TASK_NAME,
                "state": "failed", "error": {"error_code": module.IMMEDIATE_TIMEOUT_CODE},
                "reserved_resources_record": [REPO_PRN],
                "pulp_created": "2026-11-03T15:00:01Z",
                "started_at": "2026-11-03T15:00:02Z",
                "finished_at": "2026-11-03T15:00:10Z",
            }
            self.update_task = task
            raise module.PulpTaskFailed(task, "immediate task timed out")

        def task(self, uuid):
            assert uuid == TASK_UUID
            return self.update_task

        def repository_tasks(self, name, *args):
            if "--state-in" in args:
                return []
            return [{"pulp_href": TASK_HREF}]

    client = TimedOutClient()
    with patch.object(module, "utc_now", return_value=NOW):
        result = module.run_one(client, "source", predelete_args(
            target=5, step=1, direct_update=True, apply=True,
        ))
    assert "prune 1 version" in result
    assert "failed task" in result and "PLP0024" in result
    assert "removed 1 version; Pulp update task 8.0s" in result
    assert client.deleted == []
    label = json.loads(client.labels[module.LABEL_KEY])
    assert label["state"] == "completed" and label["to"] == 9
    assert label["reconciliation"]["task_href"] == TASK_HREF
    assert label["pruned_count"] == 1
    assert label["pulp_task_seconds"] == 8.0


def test_missing_cli_task_id_recovers_unique_update_and_reconciles():
    class MissingIdClient(PredeleteClient):
        domain = "konflux-release-integration-tests"

        def __init__(self):
            super().__init__()
            self.update_calls = 0
            self.update_task = {
                "pulp_href": TASK_HREF, "name": module.UPDATE_TASK_NAME,
                "state": "failed", "error": {"error_code": module.IMMEDIATE_TIMEOUT_CODE},
                "reserved_resources_record": [REPO_PRN],
                "pulp_created": "2026-11-03T15:00:01Z",
                "started_at": "2026-11-03T15:00:02Z",
                "finished_at": "2026-11-03T15:00:10Z",
            }

        def show(self, name):
            return {**super().show(name), "prn": REPO_PRN}

        def update_limit(self, name, limit):
            self.update_calls += 1
            self.limit = limit
            self.count = limit + 1
            raise module.PulpDispatchUncertain("CLI dispatch timed out without a task ID")

        def repository_tasks(self, name, *args):
            if "--state-in" in args:
                return []
            return [self.update_task]

        def wait_for_task(self, task_href, activity):
            assert task_href == TASK_HREF
            raise module.PulpTaskFailed(self.update_task, "immediate task timed out")

        def task(self, uuid):
            assert uuid == TASK_UUID
            return self.update_task

    client = MissingIdClient()
    with patch.object(module, "utc_now", return_value=NOW):
        result = module.run_one(client, "source", predelete_args(
            target=5, step=1, direct_update=True, apply=True,
        ))
    assert "removed 1 version; Pulp update task 8.0s" in result
    assert client.update_calls == 1
    label = json.loads(client.labels[module.LABEL_KEY])
    assert label["state"] == "completed"
    assert label["reconciliation"]["task_href"] == TASK_HREF


def test_missing_cli_task_id_rejects_ambiguous_repository_tasks():
    class AmbiguousClient:
        domain = "konflux-release-integration-tests"

        def show(self, name):
            return {"pulp_labels": {module.LABEL_KEY: "pending"}}

        def repository_tasks(self, name, *args):
            return [
                {"pulp_href": TASK_HREF, "name": module.UPDATE_TASK_NAME,
                 "reserved_resources_record": [REPO_PRN],
                 "pulp_created": "2026-11-03T15:00:01Z"},
                {"pulp_href": "/another/task/", "name": "another task",
                 "pulp_created": "2026-11-03T15:00:02Z"},
            ]

    with pytest.raises(RuntimeError, match="found 2 repository tasks"):
        module.find_dispatched_update(
            AmbiguousClient(), "source", REPO_PRN, "pending", "2026-11-03T15:00:00Z",
        )


def test_direct_timeout_with_incomplete_prune_stays_pending():
    class IncompleteTimedOutClient(PredeleteClient):
        def update_limit(self, name, limit):
            self.limit = limit
            task = {
                "pulp_href": TASK_HREF, "state": "failed",
                "error": {"error_code": module.IMMEDIATE_TIMEOUT_CODE},
            }
            raise module.PulpTaskFailed(task, "immediate task timed out")

    client = IncompleteTimedOutClient()
    with pytest.raises(ValueError, match="version count 11 differs"):
        module.run_one(client, "source", predelete_args(
            target=5, step=1, direct_update=True, apply=True,
        ))
    assert client.deleted == []
    assert json.loads(client.labels[module.LABEL_KEY])["state"] == "pending"


def test_direct_update_with_incomplete_prune_keeps_pending_marker():
    class IncompleteClient(PredeleteClient):
        def update_limit(self, name, limit):
            self.limit = limit

    client = IncompleteClient()
    with pytest.raises(ValueError, match="expected 10"):
        module.run_one(client, "source", predelete_args(
            target=5, step=1, direct_update=True, apply=True,
        ))
    assert client.deleted == []
    assert json.loads(client.labels[module.LABEL_KEY])["state"] == "pending"


def test_direct_update_skips_busy_repository_before_marking_pending():
    class BusyDirectClient(PredeleteClient):
        def repository_tasks(self, name, *args):
            return [{"pulp_href": "/api/pulp/test/api/v3/tasks/123/", "state": "waiting"}]

    client = BusyDirectClient()
    with pytest.raises(module.RepositoryBusy, match="first 123"):
        module.run_one(client, "source", predelete_args(
            target=5, step=1, direct_update=True, apply=True,
        ))
    assert client.deleted == [] and not client.updated
    assert client.marker_calls == 0


def test_failed_worker_delete_keeps_pending_marker_and_does_not_update_limit():
    client = PredeleteClient(fail_delete=True)
    with patch.object(module, "utc_now", return_value=NOW):
        with pytest.raises(RuntimeError, match="delete task failed"):
            module.run_one(client, "aarch64", predelete_args(apply=True))
    assert not client.updated
    assert client.count == 11
    assert json.loads(client.labels[module.LABEL_KEY])["state"] == "pending"


def test_busy_repository_gets_no_delete_or_pending_label():
    class BusyClient(PredeleteClient):
        def repository_tasks(self, name, *args):
            return [{
                "pulp_href": "/api/pulp/test/api/v3/tasks/123/",
                "state": "running",
            }]

    client = BusyClient()
    with pytest.raises(module.RepositoryBusy, match=r"first 123 \(running\)"):
        module.run_one(client, "source", predelete_args(apply=True))
    assert client.deleted == []
    assert client.marker_calls == 0


def test_repository_becoming_busy_during_planning_gets_no_pending_label():
    class BusyAfterPlan(PredeleteClient):
        def __init__(self):
            super().__init__()
            self.task_checks = 0

        def repository_tasks(self, name, *args):
            self.task_checks += 1
            if self.task_checks == 1:
                return []
            return [{"pulp_href": "/api/pulp/test/api/v3/tasks/456/", "state": "waiting"}]

    client = BusyAfterPlan()
    with pytest.raises(module.RepositoryBusy, match="first 456"):
        module.run_one(client, "source", predelete_args(apply=True))
    assert client.deleted == []
    assert client.marker_calls == 0


def test_watch_waits_for_repository_queue_before_each_delete(capsys):
    class QueuedDuringBatch(PredeleteClient):
        def __init__(self):
            super().__init__()
            self.queue_seen = False
            self.queue_cleared = False

        def repository_tasks(self, name, *args):
            if self.deleted == [1] and not self.queue_seen:
                self.queue_seen = True
                return [{
                    "pulp_href": "/api/pulp/test/api/v3/tasks/789/",
                    "name": "pulp_rpm.app.tasks.signing.signed_add_and_remove",
                    "state": "waiting",
                }]
            if self.queue_seen:
                self.queue_cleared = True
            return []

        def delete_version(self, name, number):
            if number == 2:
                assert self.queue_seen and self.queue_cleared
            super().delete_version(name, number)

    client = QueuedDuringBatch()
    with patch.object(module, "utc_now", return_value=NOW), patch.object(
        module.time, "sleep"
    ) as sleep:
        module.run_one(client, "source", predelete_args(
            apply=True, watch=True, poll_seconds=1, step=2,
        ))
    sleep.assert_called_once_with(1)
    assert client.deleted == [1, 2]
    output = capsys.readouterr().err
    assert "waiting for 1 active repository task" in output and "first 789" in output
    assert "queue is empty; resuming retention" in output


def test_watch_skips_busy_repository_and_completes_other_repositories(capsys):
    args = argparse.Namespace(target=5, poll_seconds=60)
    calls = []

    def run_one(client, name, run_args):
        calls.append(name)
        if name == "source" and calls.count("source") == 1:
            raise module.RepositoryBusy("one active task")
        return f"{name}: 6 versions, limit 5; target reached"

    with patch.object(module, "run_one", side_effect=run_one), patch.object(
        module.time, "sleep"
    ) as sleep:
        assert module.watch_repositories(object(), ("source", "x86_64"), args) == 0
    assert calls == ["source", "x86_64", "source"]
    sleep.assert_called_once_with(60)
    output = capsys.readouterr().out
    assert "source: busy" in output
    assert "x86_64: 6 versions, limit 5; target reached" in output


def test_watch_continues_after_completed_step_exceeds_wait_limit(capsys):
    args = argparse.Namespace(target=5, poll_seconds=60)
    calls = []

    def run_one(client, name, run_args):
        calls.append(name)
        if name == "source" and calls.count("source") == 1:
            raise module.StepWaitExceeded(
                "source: retention step completed in 31s; stopping before other repositories"
            )
        return f"{name}: 6 versions, limit 5; target reached"

    with patch.object(module, "run_one", side_effect=run_one), patch.object(module.time, "sleep"):
        assert module.watch_repositories(object(), ("source", "x86_64"), args) == 0
    assert calls == ["source", "x86_64", "source"]
    assert "continuing with other repositories in watch mode" in capsys.readouterr().out


def test_watch_continues_other_repositories_after_pending_failure(capsys):
    args = argparse.Namespace(target=5, poll_seconds=60)
    calls = []

    def run_one(client, name, run_args):
        calls.append(name)
        if name == "source":
            if calls.count(name) == 1:
                raise RuntimeError("delete task failed")
            if calls.count(name) < 4:
                raise ValueError("a previous retention step is pending")
        return f"{name}: 6 versions, limit 5; target reached"

    with patch.object(module, "run_one", side_effect=run_one), patch.object(module.time, "sleep"):
        assert module.watch_repositories(object(), ("source", "x86_64"), args) == 0
    assert calls == ["source", "x86_64", "source", "source", "source"]
    output = capsys.readouterr()
    assert "x86_64: 6 versions, limit 5; target reached" in output.out
    assert output.err.count("source: blocked: a previous retention step is pending") == 1
    assert "source: block cleared; resuming retention" in output.err


def test_watch_heartbeat_names_blocked_repositories(capsys):
    args = argparse.Namespace(target=5, poll_seconds=60)
    checks = 0

    def run_one(client, name, run_args):
        nonlocal checks
        if name == "source":
            if checks == 0:
                raise ValueError("a previous retention step is pending")
            return f"{name}: 6 versions, limit 5; target reached"
        checks += 1
        if checks == 1:
            raise module.RepositoryBusy("one active task")
        return f"{name}: 6 versions, limit 5; target reached"

    with patch.object(module, "run_one", side_effect=run_one), patch.object(
        module.time, "sleep"
    ), patch.object(module, "WATCH_HEARTBEAT_SECONDS", 0):
        assert module.watch_repositories(object(), ("source", "x86_64"), args) == 0
    assert "watch status: 0 target reached, 1 blocked, 1 still active; blocked: source" in (
        capsys.readouterr().err
    )


def test_watch_rejects_read_only_mode():
    with pytest.raises(SystemExit, match="2"):
        module.main(["--watch"])


def test_watch_defaults_to_no_cooldown_and_one_pass_keeps_24_hours():
    with patch.object(module, "PulpClient"), patch.object(
        module, "watch_repositories", return_value=0
    ) as watch, patch.object(module, "run_one", return_value="source: target reached") as one_pass:
        assert module.main(["--repos", "source", "--watch", "--apply"]) == 0
        assert watch.call_args.args[2].cooldown_hours == 0
        assert watch.call_args.args[2].direct_update is True
        assert module.main(["--repos", "source"]) == 0
        assert one_pass.call_args.args[2].cooldown_hours == 24
        assert one_pass.call_args.args[2].direct_update is True
        assert module.main(["--repos", "source", "--worker-predelete"]) == 0
        assert one_pass.call_args.args[2].direct_update is False


def test_watch_allows_worker_predelete_fallback():
    with patch.object(module, "PulpClient"), patch.object(
        module, "watch_repositories", return_value=0,
    ) as watch:
        assert module.main(["--repos", "source", "--watch", "--apply", "--worker-predelete"]) == 0
    assert watch.call_args.args[2].direct_update is False


def test_default_repositories_choose_parallel_mode():
    with patch.object(module, "watch_repositories_parallel", return_value=0) as parallel_watch, \
            patch.object(module, "run_repositories_parallel", return_value=0) as parallel_once:
        assert module.main(["--watch", "--apply"]) == 0
        assert parallel_watch.call_args.args[2] == module.DEFAULT_REPOS
        assert module.main([]) == 0
        assert parallel_once.call_args.args[2] == module.DEFAULT_REPOS


def test_default_one_pass_starts_repositories_in_parallel_with_separate_clients(capsys):
    barrier = threading.Barrier(2)
    clients = []

    def client_factory(config, domain):
        client = object()
        clients.append(client)
        return client

    def run_one(client, name, args):
        barrier.wait(timeout=3)
        return f"{name}: target reached"

    with patch.object(module, "PulpClient", side_effect=client_factory), patch.object(
        module, "run_one", side_effect=run_one,
    ):
        assert module.run_repositories_parallel(
            Path("/tmp/pulp-config.toml"), "test", ("source", "x86_64"), argparse.Namespace(),
        ) == 0
    assert len(clients) == 2 and clients[0] is not clients[1]
    output = capsys.readouterr().out
    assert "source: target reached" in output
    assert "x86_64: target reached" in output


def test_default_watch_starts_repositories_in_parallel_with_separate_clients():
    barrier = threading.Barrier(2)
    clients = []

    def client_factory(config, domain, stop_event):
        client = object()
        clients.append(client)
        return client

    def run_one(client, name, args):
        barrier.wait(timeout=3)
        return f"{name}: 6 versions, limit 5; target reached"

    args = argparse.Namespace(target=5, poll_seconds=60)
    with patch.object(module, "PulpClient", side_effect=client_factory), patch.object(
        module, "run_one", side_effect=run_one,
    ):
        assert module.watch_repositories_parallel(
            Path("/tmp/pulp-config.toml"), "test", ("source", "x86_64"), args,
        ) == 0
    assert len(clients) == 2 and clients[0] is not clients[1]


def test_parallel_watch_revisits_fast_repo_while_another_update_is_running():
    slow_started = threading.Event()
    fast_second_step = threading.Event()
    fast_calls = 0

    def run_one(client, name, args):
        nonlocal fast_calls
        if name == "source":
            slow_started.set()
            assert fast_second_step.wait(timeout=3)
            return "source: 6 versions, limit 5; target reached"
        assert slow_started.wait(timeout=3)
        fast_calls += 1
        if fast_calls == 1:
            return "x86_64: completed a retention step"
        fast_second_step.set()
        return "x86_64: 6 versions, limit 5; target reached"

    args = argparse.Namespace(target=5, poll_seconds=0.01)
    with patch.object(module, "PulpClient", side_effect=lambda config, domain, stop_event: object()), \
            patch.object(module, "run_one", side_effect=run_one):
        assert module.watch_repositories_parallel(
            Path("/tmp/pulp-config.toml"), "test", ("source", "x86_64"), args,
        ) == 0
    assert fast_calls == 2


def test_zero_cooldown_can_be_selected_for_one_pass():
    with patch.object(module, "PulpClient"), patch.object(
        module, "run_one", return_value="source: target reached",
    ) as one_pass:
        assert module.main(["--repos", "source", "--cooldown-hours", "0"]) == 0
    assert one_pass.call_args.args[2].cooldown_hours == 0
    assert one_pass.call_args.args[2].watch is False


def test_watch_status_deduplicates_cooldown_countdown():
    first = "source: 3796 versions; cooldown until 2026-11-03T22:24:11+00:00 (in 0hr 41m 2s)"
    later = "source: 3797 versions; cooldown until 2026-11-03T22:24:11+00:00 (in 0hr 40m 2s)"
    assert module.watch_status_key(first) == module.watch_status_key(later)


def test_slow_worker_delete_shortens_current_step_before_limit_update():
    client = PredeleteClient()
    with patch.object(module, "utc_now", return_value=NOW), patch.object(
        module.time, "monotonic", side_effect=[0, 1801, 1801, 1802]
    ):
        with pytest.raises(RuntimeError, match="stopping before other repositories"):
            module.run_one(client, "aarch64", predelete_args(step=2, apply=True))
    assert client.deleted == [1]
    assert client.limit == 9 and client.count == 10
    label = json.loads(client.labels[module.LABEL_KEY])
    assert label["state"] == "completed"
    assert label["to"] == 9
    assert label["deleted_versions"] == [1]


def test_new_content_during_deletes_reduces_limit_only_to_surviving_count():
    class AppendingClient(PredeleteClient):
        def delete_version(self, name, number):
            super().delete_version(name, number)
            if number == 1:
                self.latest_number += 1
                self.count += 1

    client = AppendingClient()
    with patch.object(module, "utc_now", return_value=NOW):
        result = module.run_one(client, "source", predelete_args(step=2, apply=True))
    assert "limit 10 -> 9" in result
    assert client.deleted == [1, 2] and client.count == 10
    assert json.loads(client.labels[module.LABEL_KEY])["to"] == 9


def test_reconcile_partial_deletes_after_concurrent_content_addition():
    client = PredeleteClient()
    client.deleted = [1, 2]
    client.latest_number = 12
    client.count = 10
    client.labels[module.LABEL_KEY] = json.dumps({
        "state": "pending", "from": 10, "to": 8,
        "before_count": 11, "started_at": "2026-11-03T15:00:00Z",
        "strategy": "predelete", "latest_version_href": "/repository/123/versions/11/",
        "planned_deletes": [1, 2, 3],
    }, separators=(",", ":"))
    with patch.object(module, "utc_now", return_value=NOW):
        assert "would close pending label" in module.reconcile_predelete_one(
            client, "source", predelete_args(),
        )
        result = module.reconcile_predelete_one(client, "source", predelete_args(apply=True))
    label = json.loads(client.labels[module.LABEL_KEY])
    assert "verified 2 planned deletes" in result
    assert label["state"] == "completed"
    assert label["from"] == label["to"] == 10
    assert label["deleted_versions"] == [1, 2]


def test_reconcile_pending_batch_before_first_delete():
    client = PredeleteClient()
    client.labels[module.LABEL_KEY] = json.dumps({
        "state": "pending", "from": 10, "to": 8,
        "before_count": 11, "started_at": "2026-11-03T15:00:00Z",
        "strategy": "predelete", "latest_version_href": "/repository/123/versions/11/",
        "planned_deletes": [1, 2, 3],
    }, separators=(",", ":"))
    with patch.object(module, "utc_now", return_value=NOW):
        assert "verified 0 planned deletes" in module.reconcile_predelete_one(
            client, "source", predelete_args(),
        )
        result = module.reconcile_predelete_one(client, "source", predelete_args(apply=True))
    label = json.loads(client.labels[module.LABEL_KEY])
    assert "pending label closed" in result
    assert label["state"] == "completed"
    assert label["from"] == label["to"] == 10
    assert label["deleted_versions"] == []
    assert label["interruption"] == "batch stopped before worker deletes"


def test_reconcile_zero_deletes_rejects_unexplained_version_loss():
    client = PredeleteClient()
    client.count = 10
    client.labels[module.LABEL_KEY] = json.dumps({
        "state": "pending", "from": 10, "to": 8,
        "before_count": 11, "strategy": "predelete",
        "latest_version_href": "/repository/123/versions/11/",
        "planned_deletes": [1, 2, 3],
    }, separators=(",", ":"))
    with pytest.raises(ValueError, match="differs from expected"):
        module.reconcile_predelete_one(client, "source", predelete_args(apply=True))
    assert json.loads(client.labels[module.LABEL_KEY])["state"] == "pending"


def test_reconcile_partial_deletes_rejects_unexplained_version_loss():
    client = PredeleteClient()
    client.deleted = [1, 2]
    client.count = 8
    client.labels[module.LABEL_KEY] = json.dumps({
        "state": "pending", "from": 10, "to": 8,
        "before_count": 11, "strategy": "predelete",
        "latest_version_href": "/repository/123/versions/11/",
        "planned_deletes": [1, 2, 3],
    }, separators=(",", ":"))
    with pytest.raises(ValueError, match="differs from expected"):
        module.reconcile_predelete_one(client, "source", predelete_args(apply=True))
    assert json.loads(client.labels[module.LABEL_KEY])["state"] == "pending"


def test_new_repository_below_target_needs_no_publication_to_set_limit():
    class InitialRepository(PredeleteClient):
        def __init__(self):
            super().__init__()
            self.limit = None
            self.count = 1

        def show(self, name):
            return {**super().show(name),
                    "latest_version_href": "/repository/123/versions/0/"}

        def simple_protected_latest(self, repository):
            raise AssertionError("publication check must be skipped when no pruning is possible")

        def update_limit(self, name, limit):
            self.limit = limit
            self.updated = True

    client = InitialRepository()
    args = predelete_args(target=100, step=1, adopt_existing=False, apply=True)
    with patch.object(module, "utc_now", return_value=NOW):
        result = module.run_one(client, "ppc64le", args)
    assert "1 version, limit None -> 100; delete 0 versions" in result
    assert client.updated and client.limit == 100 and client.count == 1
    assert json.loads(client.labels[module.LABEL_KEY])["state"] == "completed"
