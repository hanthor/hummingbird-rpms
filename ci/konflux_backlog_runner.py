#!/usr/bin/env python3
"""Trigger an ordered Konflux MR backlog with bounded MR concurrency."""

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECTS = {
    "rpms": "redhat%2Fhummingbird%2Frpms",
    "containers": "redhat%2Fhummingbird%2Fcontainers",
}
KONFLUX_STATUS_PREFIXES = ("Konflux kflux-prd-rh03 /", "Red Hat Konflux /")
QUEUE_MARKER = "HUM-13088 queue runner"
MR_URL = re.compile(
    r"https://gitlab\.com/redhat/hummingbird/(rpms|containers)/-/merge_requests/(\d+)/?"
)
MR_SHORT = re.compile(r"(rpms|containers)!(\d+)")
NOTE_BODY = re.compile(
    r"/test\n\nHUM-13088 queue runner: (rpms|containers)!(\d+) @ ([0-9a-f]{40})"
)
SHA = re.compile(r"[0-9a-f]{40}")
PIPELINE_NAME = re.compile(r"[a-z0-9][a-z0-9.-]*")


@dataclass(frozen=True)
class MR:
    repo: str
    iid: int
    expected_builds: tuple[str, ...] = ()

    @property
    def project(self) -> str:
        return PROJECTS[self.repo]

    def __str__(self) -> str:
        return f"{self.repo}!{self.iid}"


@dataclass(frozen=True)
class Attempt:
    sha: str
    created: datetime
    note_id: int


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_queue(path: Path) -> list[MR]:
    raw: Any = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError("queue must be a JSON list")
    result: list[MR] = []
    seen: set[tuple[str, int]] = set()
    for entry in raw:
        expected: tuple[str, ...] = ()
        if isinstance(entry, dict):
            names = entry.get("expected_builds")
            if (
                set(entry) != {"mr", "expected_builds"}
                or not isinstance(names, list)
                or not names
                or any(
                    not isinstance(name, str) or PIPELINE_NAME.fullmatch(name) is None
                    for name in names
                )
                or len(names) != len(set(names))
            ):
                raise ValueError(f"invalid expected_builds: {entry!r}")
            expected = tuple(names)
            entry = entry["mr"]
        if not isinstance(entry, str):
            raise ValueError(f"queue entry must contain an MR reference: {entry!r}")
        match = MR_SHORT.fullmatch(entry) or MR_URL.fullmatch(entry)
        if match is None or int(match[2]) < 1:
            raise ValueError(f"invalid MR reference: {entry!r}")
        mr = MR(match[1], int(match[2]), expected)
        if (mr.repo, mr.iid) in seen:
            raise ValueError(f"duplicate MR: {mr}")
        seen.add((mr.repo, mr.iid))
        result.append(mr)
    return result


def note_body(mr: MR, sha: str) -> str:
    return f"/test\n\n{QUEUE_MARKER}: {mr} @ {sha}"


def latest_attempt(notes: list[dict[str, Any]], mr: MR) -> Attempt | None:
    attempts: list[Attempt] = []
    for note in notes:
        match = NOTE_BODY.fullmatch(str(note.get("body", "")))
        if match is not None and match[1] == mr.repo and int(match[2]) == mr.iid:
            attempts.append(
                Attempt(match[3], parse_time(note["created_at"]), note["id"])
            )
    return max(attempts, key=lambda item: (item.created, item.note_id), default=None)


def run_matches(run: dict[str, Any], mr: MR, attempt: Attempt) -> bool:
    meta = run.get("metadata", {})
    annotations = meta.get("annotations", {})
    labels = meta.get("labels", {})
    repo_url = f"https://gitlab.com/redhat/hummingbird/{mr.repo}/-/tree/{attempt.sha}"
    return (
        annotations.get("build.appstudio.redhat.com/pull_request_number") == str(mr.iid)
        and annotations.get("build.appstudio.redhat.com/commit_sha") == attempt.sha
        and annotations.get("build.appstudio.openshift.io/repo") == repo_url
        and labels.get("pipelines.appstudio.openshift.io/type") == "build"
        and parse_time(meta["creationTimestamp"]) >= attempt.created
    )


def run_state(run: dict[str, Any]) -> str:
    for condition in run.get("status", {}).get("conditions", []):
        if condition.get("type") == "Succeeded":
            return {"True": "passed", "False": "failed"}.get(
                condition.get("status"), "running"
            )
    return "running"


def attempt_state(
    runs: list[dict[str, Any]],
    mr: MR,
    attempt: Attempt,
    now: datetime,
    admission_timeout: int,
) -> str:
    matching = [run for run in runs if run_matches(run, mr, attempt)]
    missing = [
        name
        for name in mr.expected_builds
        if not any(
            re.fullmatch(
                re.escape(name) + r"-[a-z0-9]{5}", str(run["metadata"]["name"])
            )
            is not None
            for run in matching
        )
    ]
    if not matching or missing:
        if now - attempt.created >= timedelta(seconds=admission_timeout):
            return "stalled"
        return "admitting"
    if any(run_state(run) == "running" for run in matching):
        return "running"
    # Allow PaC time to create every matching PipelineRun before freeing capacity.
    newest = max(parse_time(run["metadata"]["creationTimestamp"]) for run in matching)
    if now - newest < timedelta(seconds=120):
        return "settling"
    if any(run_state(run) == "failed" for run in matching):
        return "failed"
    return "passed"


def _command_json(command: list[str]) -> Any:
    result = subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=120
    )
    return json.loads(result.stdout)


class Backend:
    def __init__(self, context: str, namespace: str):
        self.context = context
        self.namespace = namespace

    def mr(self, ref: MR) -> dict[str, Any]:
        return _command_json(
            ["glab", "api", f"projects/{ref.project}/merge_requests/{ref.iid}"]
        )

    def notes(self, ref: MR) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page = 1
        while True:
            notes = _command_json(
                [
                    "glab",
                    "api",
                    f"projects/{ref.project}/merge_requests/{ref.iid}/notes?per_page=100&page={page}",
                ]
            )
            result.extend(notes)
            if len(notes) < 100:
                return result
            page += 1

    def runs(self) -> list[dict[str, Any]]:
        data = _command_json(
            [
                "kubectl",
                "--context",
                self.context,
                "-n",
                self.namespace,
                "get",
                "pipelineruns",
                "-o",
                "json",
            ]
        )
        return data["items"]

    def trigger(self, ref: MR, sha: str) -> None:
        _command_json(
            [
                "glab",
                "api",
                "-X",
                "POST",
                f"projects/{ref.project}/merge_requests/{ref.iid}/notes",
                "-f",
                f"body={note_body(ref, sha)}",
            ]
        )

    def approvals(self, ref: MR) -> dict[str, Any]:
        return _command_json(
            [
                "glab",
                "api",
                f"projects/{ref.project}/merge_requests/{ref.iid}/approvals",
            ]
        )

    def statuses(self, ref: MR, sha: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page = 1
        while True:
            statuses = _command_json(
                [
                    "glab",
                    "api",
                    f"projects/{ref.project}/repository/commits/{sha}/statuses?per_page=100&page={page}",
                ]
            )
            result.extend(statuses)
            if len(statuses) < 100:
                return result
            page += 1

    def arm_auto_merge(self, ref: MR, sha: str) -> None:
        # GitLab refuses the merge if the source branch changed after our final read.
        _command_json(
            [
                "glab",
                "api",
                "-X",
                "PUT",
                f"projects/{ref.project}/merge_requests/{ref.iid}/merge",
                "-f",
                "auto_merge=true",
                "-f",
                f"sha={sha}",
            ]
        )


def auto_merge_readiness(
    ref: MR,
    mr: dict[str, Any],
    approvals: dict[str, Any],
    statuses: list[dict[str, Any]],
    attempt: Attempt,
) -> str | None:
    """Return a hold reason, or None when all observable merge gates are satisfied."""
    if (
        mr.get("draft")
        or mr.get("has_conflicts")
        or not mr.get("blocking_discussions_resolved")
    ):
        return "draft, conflicts, or unresolved discussion"
    if not approvals.get("approved"):
        return "approval"
    if (mr.get("head_pipeline") or {}).get("status") != "success":
        return "GitLab CI"
    konflux = [
        status
        for status in statuses
        if str(status.get("name", "")).startswith(KONFLUX_STATUS_PREFIXES)
    ]
    if not konflux or any(
        status.get("status") != "success"
        or not status.get("created_at")
        or parse_time(status["created_at"]) < attempt.created
        for status in konflux
    ):
        return "fresh Konflux statuses"
    if any(
        not any(str(status["name"]).endswith(" / " + name) for status in konflux)
        for name in ref.expected_builds
    ):
        return "expected build statuses"
    if mr.get("detailed_merge_status") != "mergeable":
        return "GitLab mergeability"
    return None


def cycle(
    queue: list[MR],
    backend: Backend,
    *,
    limit: int,
    apply: bool,
    admission_timeout: int,
    now: datetime,
    arm_auto_merge: bool = False,
) -> dict[MR, str]:
    if arm_auto_merge and any(not ref.expected_builds for ref in queue):
        raise ValueError("auto-merge requires expected_builds for every MR")
    runs = backend.runs()
    # Existing tenant load also occupies slots. A single MR can create multiple runs.
    active = sum(run_state(run) == "running" for run in runs)
    states: dict[MR, str] = {}
    for ref in queue:
        mr = backend.mr(ref)
        if mr["state"] != "opened":
            attempt = latest_attempt(backend.notes(ref), ref)
            if (
                attempt is not None
                and attempt.sha == mr.get("sha")
                and attempt_state(runs, ref, attempt, now, admission_timeout)
                == "passed"
            ):
                states[ref] = "closed"
            else:
                states[ref] = "closed before verified run"
                active = max(active, limit)
            continue
        if mr.get("target_branch") != "main":
            states[ref] = "unsupported target branch"
            active = max(active, limit)
            continue
        sha = mr["sha"]
        if not isinstance(sha, str) or SHA.fullmatch(sha) is None:
            raise ValueError(f"{ref}: GitLab returned an invalid SHA")
        attempt = latest_attempt(backend.notes(ref), ref)
        if attempt is not None and attempt.sha == sha:
            state = attempt_state(runs, ref, attempt, now, admission_timeout)
            if state == "passed" and arm_auto_merge:
                current = backend.mr(ref)
                if current["state"] != "opened" or current["sha"] != sha:
                    state = "changed; retry next cycle"
                else:
                    hold = auto_merge_readiness(
                        ref,
                        current,
                        backend.approvals(ref),
                        backend.statuses(ref, sha),
                        attempt,
                    )
                    if hold is not None:
                        state = (
                            "auto-merge armed before checks passed"
                            if current.get("merge_when_pipeline_succeeds")
                            else f"awaiting {hold}"
                        )
                    elif current.get("merge_when_pipeline_succeeds"):
                        state = "auto-merge armed"
                    elif apply:
                        backend.arm_auto_merge(ref, sha)
                        state = "auto-merge armed"
                    else:
                        state = "would arm auto-merge"
            states[ref] = state
            if state in {"admitting", "settling"}:
                active += 1
            elif state in {
                "stalled",
                "unsupported target branch",
                "auto-merge armed before checks passed",
                "closed before verified run",
            } or (
                arm_auto_merge
                and (state.startswith("awaiting ") or state == "auto-merge armed")
            ):
                active = max(active, limit)
            continue
        if active >= limit:
            states[ref] = "waiting for capacity"
            continue
        # Re-read immediately before posting: a push can change the selected SHA.
        current = backend.mr(ref)
        if current["state"] != "opened" or current["sha"] != sha:
            states[ref] = "changed; retry next cycle"
            continue
        if apply:
            backend.trigger(ref, sha)
            states[ref] = "triggered"
        else:
            states[ref] = "would trigger"
        active += 1
    return states


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queue", type=Path, default=Path("config/konflux-backlog-queue.json")
    )
    parser.add_argument(
        "--context", required=True, help="explicit rh03 kubectl context"
    )
    parser.add_argument("--namespace", default="hummingbird-tenant")
    parser.add_argument(
        "--max-in-flight", type=int, default=1, help="maximum concurrent MR attempts"
    )
    parser.add_argument("--admission-timeout", type=int, default=900)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument(
        "--watch", action="store_true", help="continue until all MRs finish"
    )
    parser.add_argument(
        "--apply", action="store_true", help="post /test comments; default is dry run"
    )
    parser.add_argument(
        "--arm-auto-merge",
        action="store_true",
        help="after fresh successful checks and approval, arm GitLab auto-merge (may merge immediately)",
    )
    args = parser.parse_args()
    if args.max_in_flight < 1 or args.admission_timeout < 1 or args.poll_seconds < 1:
        parser.error("limits and timeouts must be positive")
    try:
        queue = parse_queue(args.queue)
        if not queue:
            print(f"Queue is empty: {args.queue}")
            return 0
        if args.arm_auto_merge and any(not ref.expected_builds for ref in queue):
            parser.error("--arm-auto-merge requires expected_builds for every MR")
        backend = Backend(args.context, args.namespace)
        while True:
            states = cycle(
                queue,
                backend,
                limit=args.max_in_flight,
                apply=args.apply,
                arm_auto_merge=args.arm_auto_merge,
                admission_timeout=args.admission_timeout,
                now=datetime.now(timezone.utc),
            )
            for ref, state in states.items():
                print(f"{ref}: {state}", flush=True)
            if any(
                state
                in {
                    "stalled",
                    "unsupported target branch",
                    "auto-merge armed before checks passed",
                    "closed before verified run",
                }
                for state in states.values()
            ):
                print(
                    "Queue stopped on an unsafe or stalled MR; investigate before retrying.",
                    file=sys.stderr,
                )
                return 1
            if not args.watch or not args.apply:
                return 0
            if all(
                state in {"passed", "failed", "closed", "auto-merge armed"}
                for state in states.values()
            ):
                return int(any(state == "failed" for state in states.values()))
            time.sleep(args.poll_seconds)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
