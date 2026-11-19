"""Safety and scheduling tests for the staged Konflux backlog runner."""

import importlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

script_path = Path(__file__).resolve().parents[1] / "ci/konflux_backlog_runner.py"
spec = importlib.util.spec_from_file_location("konflux_backlog_runner", script_path)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)

NOW = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)
SHA1 = "a" * 40
SHA2 = "b" * 40
RPM = runner.MR("rpms", 123)
CONTAINER = runner.MR("containers", 456)


def plr(ref=RPM, sha=SHA1, created=NOW, succeeded="Unknown"):
    """Build a minimal PaC PipelineRun with the annotations this runner requires."""
    return {
        "metadata": {
            "name": "test-on-pull-request-abcde",
            "creationTimestamp": created.isoformat(),
            "annotations": {
                "build.appstudio.redhat.com/pull_request_number": str(ref.iid),
                "build.appstudio.redhat.com/commit_sha": sha,
                "build.appstudio.openshift.io/repo": (
                    f"https://gitlab.com/redhat/hummingbird/{ref.repo}/-/tree/{sha}"
                ),
            },
            "labels": {"pipelines.appstudio.openshift.io/type": "build"},
        },
        "status": {"conditions": [{"type": "Succeeded", "status": succeeded}]},
    }


class FakeBackend:
    def __init__(self):
        self.heads = {RPM: SHA1, CONTAINER: SHA2}
        self.states = {RPM: "opened", CONTAINER: "opened"}
        self.notes_by_mr = {RPM: [], CONTAINER: []}
        self.pipeline_runs = []
        self.triggered = []
        self.armed = []
        self.read_count = {}
        self.change_on_second_read = False
        self.approved = True
        self.commit_statuses = []
        self.target_branch = "main"
        self.armed_existing = False

    def mr(self, ref):
        base = runner.MR(ref.repo, ref.iid)
        self.read_count[base] = self.read_count.get(base, 0) + 1
        if self.change_on_second_read and self.read_count[base] == 2:
            self.heads[base] = SHA2
        return {
            "state": self.states[base],
            "sha": self.heads[base],
            "target_branch": self.target_branch,
            "draft": False,
            "has_conflicts": False,
            "blocking_discussions_resolved": True,
            "head_pipeline": {"status": "success"},
            "detailed_merge_status": "mergeable",
            "merge_when_pipeline_succeeds": self.armed_existing,
        }

    def notes(self, ref):
        return self.notes_by_mr[runner.MR(ref.repo, ref.iid)]

    def runs(self):
        return self.pipeline_runs

    def trigger(self, ref, sha):
        self.triggered.append((ref, sha))

    def approvals(self, ref):
        return {"approved": self.approved}

    def statuses(self, ref, sha):
        return self.commit_statuses

    def arm_auto_merge(self, ref, sha):
        self.armed.append((ref, sha))


def test_queue_accepts_only_unique_mrs_from_expected_repositories(tmp_path):
    path = tmp_path / "queue.json"
    path.write_text(
        json.dumps(
            [
                "rpms!123",
                "https://gitlab.com/redhat/hummingbird/containers/-/merge_requests/456",
            ]
        )
    )
    assert runner.parse_queue(path) == [RPM, CONTAINER]
    path.write_text(json.dumps(["rpms!123", "rpms!123"]))
    with pytest.raises(ValueError, match="duplicate"):
        runner.parse_queue(path)
    path.write_text(json.dumps(["https://gitlab.com/other/repo/-/merge_requests/1"]))
    with pytest.raises(ValueError, match="invalid"):
        runner.parse_queue(path)
    path.write_text(
        json.dumps([{"mr": "rpms!123", "expected_builds": ["test-on-pull-request"]}])
    )
    assert runner.parse_queue(path) == [
        runner.MR("rpms", 123, ("test-on-pull-request",))
    ]


def test_queue_config_does_not_match_rpm_pac_paths():
    manifest = (
        Path(__file__).resolve().parents[1]
        / ".tekton/trigger-manifest-pull-request.json"
    )
    data = json.loads(manifest.read_text())
    path = "config/konflux-backlog-queue.json"
    assert not any(
        re.match(pattern, path)
        for components in data["applications"].values()
        for patterns in components.values()
        for pattern in patterns
    )


def test_dry_run_obeys_capacity_without_posting():
    backend = FakeBackend()
    states = runner.cycle(
        [RPM, CONTAINER], backend, limit=1, apply=False, admission_timeout=900, now=NOW
    )
    assert states == {RPM: "would trigger", CONTAINER: "waiting for capacity"}
    assert backend.triggered == []


def test_apply_rechecks_sha_before_posting():
    backend = FakeBackend()
    backend.change_on_second_read = True
    states = runner.cycle(
        [RPM], backend, limit=1, apply=True, admission_timeout=900, now=NOW
    )
    assert states[RPM] == "changed; retry next cycle"
    assert backend.triggered == []


def test_non_main_mr_cannot_trigger():
    backend = FakeBackend()
    backend.target_branch = "release"
    states = runner.cycle(
        [RPM], backend, limit=1, apply=True, admission_timeout=900, now=NOW
    )
    assert states[RPM] == "unsupported target branch"
    assert backend.triggered == []


def test_closed_mr_without_verified_run_stops_queue():
    backend = FakeBackend()
    backend.states[RPM] = "merged"
    states = runner.cycle(
        [RPM, CONTAINER],
        backend,
        limit=1,
        apply=True,
        admission_timeout=900,
        now=NOW,
    )
    assert states[RPM] == "closed before verified run"
    assert states[CONTAINER] == "waiting for capacity"
    assert backend.triggered == []


def test_existing_comment_holds_slot_and_prevents_duplicate_trigger():
    backend = FakeBackend()
    backend.notes_by_mr[RPM] = [
        {
            "id": 7,
            "body": runner.note_body(RPM, SHA1),
            "created_at": (NOW - timedelta(minutes=1)).isoformat(),
        }
    ]
    states = runner.cycle(
        [RPM, CONTAINER], backend, limit=1, apply=True, admission_timeout=900, now=NOW
    )
    assert states == {RPM: "admitting", CONTAINER: "waiting for capacity"}
    assert backend.triggered == []


def test_only_matching_new_run_completes_attempt():
    attempt = runner.Attempt(SHA1, NOW - timedelta(minutes=5), 7)
    assert not runner.run_matches(plr(sha=SHA2), RPM, attempt)
    assert not runner.run_matches(plr(ref=CONTAINER), RPM, attempt)
    assert not runner.run_matches(plr(created=NOW - timedelta(minutes=6)), RPM, attempt)
    assert (
        runner.attempt_state(
            [plr(succeeded="True")], RPM, attempt, NOW + timedelta(minutes=3), 900
        )
        == "passed"
    )
    assert (
        runner.attempt_state(
            [plr(succeeded="False")], RPM, attempt, NOW + timedelta(minutes=3), 900
        )
        == "failed"
    )


def test_stalled_command_stops_instead_of_releasing_slot():
    backend = FakeBackend()
    backend.notes_by_mr[RPM] = [
        {
            "id": 7,
            "body": runner.note_body(RPM, SHA1),
            "created_at": (NOW - timedelta(minutes=16)).isoformat(),
        }
    ]
    states = runner.cycle(
        [RPM, CONTAINER], backend, limit=1, apply=True, admission_timeout=900, now=NOW
    )
    assert states[RPM] == "stalled"
    assert states[CONTAINER] == "waiting for capacity"
    assert backend.triggered == []


def test_missing_expected_build_holds_then_stalls():
    ref = runner.MR("rpms", 123, ("another-on-pull-request",))
    attempt = runner.Attempt(SHA1, NOW - timedelta(minutes=5), 7)
    assert (
        runner.attempt_state([plr(succeeded="True")], ref, attempt, NOW, 900)
        == "admitting"
    )
    assert (
        runner.attempt_state(
            [plr(succeeded="True")], ref, attempt, NOW + timedelta(minutes=11), 900
        )
        == "stalled"
    )
    prefix_collision = plr(succeeded="True")
    prefix_collision["metadata"]["name"] = "another-on-pull-request-extra-abcde"
    assert (
        runner.attempt_state([prefix_collision], ref, attempt, NOW, 900) == "admitting"
    )


def test_auto_merge_requires_expected_builds():
    with pytest.raises(ValueError, match="expected_builds"):
        runner.cycle(
            [RPM],
            FakeBackend(),
            limit=1,
            apply=True,
            arm_auto_merge=True,
            admission_timeout=900,
            now=NOW,
        )


def test_unrelated_live_pipeline_consumes_capacity():
    backend = FakeBackend()
    backend.pipeline_runs = [plr(ref=CONTAINER, succeeded="Unknown")]
    states = runner.cycle(
        [RPM], backend, limit=1, apply=True, admission_timeout=900, now=NOW
    )
    assert states[RPM] == "waiting for capacity"
    assert backend.triggered == []


def test_new_sha_allows_new_attempt_after_old_run_finishes():
    backend = FakeBackend()
    backend.notes_by_mr[RPM] = [
        {
            "id": 7,
            "body": runner.note_body(RPM, SHA2),
            "created_at": (NOW - timedelta(minutes=20)).isoformat(),
        }
    ]
    backend.pipeline_runs = [plr(sha=SHA2, succeeded="True")]
    states = runner.cycle(
        [RPM], backend, limit=1, apply=True, admission_timeout=900, now=NOW
    )
    assert states[RPM] == "triggered"
    assert backend.triggered == [(RPM, SHA1)]


def test_auto_merge_waits_for_fresh_external_status_and_approval():
    backend = FakeBackend()
    expected = runner.MR("rpms", 123, ("test-on-pull-request",))
    backend.notes_by_mr[RPM] = [
        {
            "id": 7,
            "body": runner.note_body(RPM, SHA1),
            "created_at": (NOW - timedelta(minutes=5)).isoformat(),
        }
    ]
    backend.pipeline_runs = [plr(succeeded="True")]
    backend.commit_statuses = [
        {
            "name": "Konflux kflux-prd-rh03 / test-on-pull-request",
            "status": "success",
            "created_at": (NOW - timedelta(minutes=6)).isoformat(),
        }
    ]
    states = runner.cycle(
        [expected],
        backend,
        limit=1,
        apply=True,
        arm_auto_merge=True,
        admission_timeout=900,
        now=NOW + timedelta(minutes=3),
    )
    assert states[expected] == "awaiting fresh Konflux statuses"
    assert backend.armed == []
    backend.commit_statuses[0]["created_at"] = (NOW - timedelta(minutes=4)).isoformat()
    backend.approved = False
    states = runner.cycle(
        [expected],
        backend,
        limit=1,
        apply=True,
        arm_auto_merge=True,
        admission_timeout=900,
        now=NOW + timedelta(minutes=3),
    )
    assert states[expected] == "awaiting approval"
    backend.approved = True
    backend.armed_existing = True
    backend.commit_statuses[0]["status"] = "pending"
    states = runner.cycle(
        [expected],
        backend,
        limit=1,
        apply=True,
        arm_auto_merge=True,
        admission_timeout=900,
        now=NOW + timedelta(minutes=3),
    )
    assert states[expected] == "auto-merge armed before checks passed"
    backend.armed_existing = False
    backend.commit_statuses[0]["status"] = "success"
    states = runner.cycle(
        [expected],
        backend,
        limit=1,
        apply=True,
        arm_auto_merge=True,
        admission_timeout=900,
        now=NOW + timedelta(minutes=3),
    )
    assert states[expected] == "auto-merge armed"
    assert backend.armed == [(expected, SHA1)]
