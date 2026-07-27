#!/usr/bin/env python3
"""Take bounded RPM repository-version retention steps.

Run once or use --watch to keep working toward the target. The script is
read-only unless --apply is given. Snapshot and file repositories are excluded.
"""

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import requests


DEFAULT_CONFIG = Path.home() / ".config/pulp/cli.toml"
DEFAULT_REPOS = ("source", "x86_64", "aarch64", "s390x", "ppc64le")
LABEL_KEY = "retention_step"
UPDATE_TASK_NAME = "pulpcore.app.tasks.base.ageneral_update"
IMMEDIATE_TIMEOUT_CODE = "PLP0024"
TASK_HEARTBEAT_SECONDS = 30
TASK_POLL_SECONDS = 1
TASK_DISPATCH_TIMEOUT_SECONDS = 60
TASK_REQUEST_TIMEOUT = (5, 10)
WATCH_HEARTBEAT_SECONDS = 600
OUTPUT_LOCK = threading.Lock()


class RepositoryBusy(ValueError):
    """A repository has active tasks, so a retention step should wait."""


class RepositoryChanged(ValueError):
    """Repository state changed before the retention step began."""


class StepWaitExceeded(RuntimeError):
    """A completed step exceeded the per-repository wait cutoff."""


class PulpTaskFailed(RuntimeError):
    """A dispatched Pulp task reached a failed terminal state."""

    def __init__(self, task: dict[str, Any], message: str):
        self.task = task
        super().__init__(message)


class PulpDispatchUncertain(RuntimeError):
    """The CLI did not report whether its background task was dispatched."""


@dataclass(frozen=True)
class Step:
    current_limit: int | None
    next_limit: int | None
    count: int
    reason: str


def progress(message: str) -> None:
    timestamp = utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")
    with OUTPUT_LOCK:
        print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)


def task_runtime_seconds(task: dict[str, Any] | None) -> float | None:
    """Return the server-reported task runtime when both timestamps are usable."""
    if not isinstance(task, dict):
        return None
    started = task.get("started_at")
    finished = task.get("finished_at")
    if not isinstance(started, str) or not isinstance(finished, str):
        return None
    try:
        start_time = datetime.fromisoformat(started.replace("Z", "+00:00"))
        finish_time = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    except ValueError:
        return None
    if start_time.tzinfo is None or finish_time.tzinfo is None:
        return None
    seconds = (finish_time - start_time).total_seconds()
    return seconds if seconds >= 0 else None


def prune_metrics(before_count: int, after_count: int,
                  task: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """Describe verified pruning using the full update task as a timing proxy."""
    removed = before_count - after_count
    metrics: dict[str, Any] = {"pruned_count": removed}
    phrase = f"removed {version_phrase(removed)}"
    runtime = task_runtime_seconds(task)
    if runtime is not None:
        metrics["pulp_task_seconds"] = round(runtime, 3)
        phrase += f"; Pulp update task {runtime:.1f}s"
        if removed > 0 and runtime > 0:
            phrase += f" ({runtime / removed:.2f}s/version, {removed / runtime:.2f} versions/s)"
    return phrase, metrics


def completed_step_phrase(marker: dict[str, Any]) -> str:
    phrase = f"last step {marker.get('from')} -> {marker['to']} at {marker['completed_at']}"
    deleted = marker.get("deleted_versions")
    if isinstance(deleted, list):
        phrase += f", deleted {version_phrase(len(deleted))}"
    elif isinstance(marker.get("planned_prune_count"), int):
        phrase += f", planned prune {version_phrase(marker['planned_prune_count'])}"
    if marker.get("interruption"):
        phrase += f", {marker['interruption']}"
    return phrase


def parse_marker(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        marker = json.loads(raw)
        if not isinstance(marker, dict):
            raise ValueError("marker is not an object")
        if marker.get("state") == "pending":
            if not isinstance(marker.get("to"), int) or isinstance(marker["to"], bool) or marker["to"] < 1:
                raise ValueError("pending marker has invalid 'to' limit")
            return marker
        if marker.get("state") != "completed":
            raise ValueError("marker has unknown state")
        limit = marker["to"]
        completed = marker["completed_at"]
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("marker has invalid 'to' limit")
        if not isinstance(completed, str):
            raise ValueError("marker has invalid completion time")
        timestamp = datetime.fromisoformat(completed.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("marker completion time lacks a timezone")
        marker["completed_at_datetime"] = timestamp.astimezone(timezone.utc)
        return marker
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {LABEL_KEY} label: {exc}") from exc


def plan_step(
    current_limit: int | None,
    count: int,
    marker: dict[str, Any] | None,
    now: datetime,
    *,
    target: int,
    step_size: int,
    cooldown: timedelta,
    adopt_existing: bool = False,
) -> Step:
    if marker is None:
        if current_limit is not None and not adopt_existing:
            raise ValueError("retention is already set but the completion label is missing")
    elif marker["state"] == "pending":
        raise ValueError("a previous retention step is pending; inspect its task before retrying")
    elif current_limit != marker["to"]:
        raise ValueError("retention limit differs from the last completed labeled step")

    if current_limit is not None and current_limit <= target:
        return Step(current_limit, None, count, "target reached")

    if marker is not None:
        next_allowed = marker["completed_at_datetime"] + cooldown
        if now < next_allowed:
            seconds = math.ceil((next_allowed - now).total_seconds())
            hours, remainder = divmod(seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            remaining = f"{hours}hr {minutes}m {seconds}s"
            return Step(
                current_limit, None, count,
                f"cooldown until {next_allowed.isoformat()} (in {remaining})",
            )

    # The supported topology protects the latest published version, so the
    # retention limit applies to the other versions. Step counts deletions,
    # even when the current limit is higher than the current version count.
    eligible_count = max(0, count - 1)
    if current_limit is not None and eligible_count > current_limit:
        raise ValueError("eligible versions already exceed the current retention limit")
    next_limit = max(target, eligible_count - step_size)
    if current_limit == next_limit:
        return Step(current_limit, None, count, "no reduction needed")
    return Step(current_limit, next_limit, count, "ready")


class PulpClient:
    def __init__(self, config: Path, domain: str,
                 stop_event: threading.Event | None = None):
        with config.open("rb") as stream:
            settings = tomllib.load(stream)["cli"]
        self.config = config
        self.domain = domain
        self.base_url = settings["base_url"].rstrip("/")
        self.session = requests.Session()
        self.session.auth = (settings["username"], settings["password"])
        self.session.verify = settings.get("verify_ssl", True)
        self.stop_event = stop_event

    def wait_for_task(self, task_href: str, activity: str) -> dict[str, Any]:
        """Poll a dispatched task independently of the CLI process and network connection."""
        if not task_href.startswith(f"/api/pulp/{self.domain}/api/v3/tasks/"):
            raise RuntimeError(f"{activity}: unexpected task URL {task_href}")
        task_id = task_href.rstrip("/").rsplit("/", 1)[-1]
        started = time.monotonic()
        last_heartbeat = started
        disconnected = False
        while True:
            stop_event = getattr(self, "stop_event", None)
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError(f"{activity}: watch interrupted; pending label needs review")
            try:
                response = self.session.get(
                    self.base_url + task_href, timeout=TASK_REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                task = response.json()
            except requests.RequestException as exc:
                status = (
                    exc.response.status_code
                    if isinstance(exc, requests.HTTPError) and exc.response is not None else None
                )
                if status is not None and status < 500 and status not in (408, 429):
                    raise RuntimeError(
                        f"{activity}: task {task_id} query failed with HTTP {status}"
                    ) from exc
                now = time.monotonic()
                if not disconnected or now - last_heartbeat >= TASK_HEARTBEAT_SECONDS:
                    progress(
                        f"{activity}: cannot query task {task_id} ({type(exc).__name__}); "
                        "retrying after the connection returns"
                    )
                    last_heartbeat = now
                disconnected = True
                time.sleep(TASK_POLL_SECONDS)
                continue
            if not isinstance(task, dict) or task.get("pulp_href") != task_href:
                raise RuntimeError(f"{activity}: task {task_id} returned an invalid response")
            state = task.get("state")
            if disconnected:
                progress(f"{activity}: connection restored; task {task_id} is {state}")
                disconnected = False
            if state == "completed":
                return task
            if state in ("failed", "canceled", "skipped"):
                error = task.get("error") or {}
                detail = error.get("description") or error.get("reason") or state
                raise PulpTaskFailed(task, f"{activity}: task {task_id} {state}: {detail}")
            if state not in ("waiting", "running", "canceling"):
                raise RuntimeError(f"{activity}: task {task_id} has unknown state {state!r}")
            now = time.monotonic()
            if now - last_heartbeat >= TASK_HEARTBEAT_SECONDS:
                progress(f"{activity}: task {task_id} still {state} after {round(now - started)}s")
                last_heartbeat = now
            time.sleep(TASK_POLL_SECONDS)

    def cli(self, *args: str, wait: bool = False, activity: str | None = None) -> Any:
        command = [
            "pulp", "--config", str(self.config), "--domain", self.domain,
            "--format", "json",
        ]
        if wait:
            command.append("--background")
        command += list(args)
        if wait:
            with tempfile.TemporaryDirectory() as directory:
                output_path = Path(directory) / "stdout"
                error_path = Path(directory) / "stderr"
                with output_path.open("w+t") as output_file, error_path.open("w+t") as error_file, \
                        error_path.open("r") as error_reader, \
                        subprocess.Popen(command, stdout=output_file, stderr=error_file,
                                      text=True) as process:
                    started = time.monotonic()
                    task_href = None
                    last_heartbeat = started
                    while True:
                        stop_event = getattr(self, "stop_event", None)
                        if stop_event is not None and stop_event.is_set():
                            process.kill()
                            process.wait()
                            raise RuntimeError(
                                f"{activity or 'Pulp task'}: watch interrupted during dispatch; "
                                "pending label needs review"
                            )
                        try:
                            process.wait(timeout=TASK_POLL_SECONDS)
                            finished = True
                        except subprocess.TimeoutExpired:
                            finished = False
                        error_reader.seek(0)
                        error_text = error_reader.read()
                        match = re.search(r"Started background task (\S+)", error_text)
                        if match and task_href is None:
                            task_href = match.group(1)
                            task_id = task_href.rstrip("/").rsplit("/", 1)[-1]
                            progress(f"{activity or 'Pulp task'}: task ID {task_id} ({task_href})")
                        if finished:
                            break
                        now = time.monotonic()
                        if now - started >= TASK_DISPATCH_TIMEOUT_SECONDS:
                            process.kill()
                            process.wait()
                            if task_href is None:
                                raise PulpDispatchUncertain(
                                    f"{activity or 'Pulp task'}: CLI dispatch timed out without a "
                                    "task ID; outcome is uncertain and the pending label needs review"
                                )
                            progress(
                                f"{activity or 'Pulp task'}: CLI dispatch timed out; "
                                f"checking task {task_id} directly"
                            )
                            break
                        if now - last_heartbeat >= TASK_HEARTBEAT_SECONDS:
                            elapsed = round(now - started)
                            progress(f"{activity or 'Pulp task'}: CLI dispatch still running after {elapsed}s")
                            last_heartbeat = now
                    output_file.seek(0)
                    result = subprocess.CompletedProcess(
                        command, process.returncode, output_file.read(), error_text,
                    )
            if task_href is not None:
                return self.wait_for_task(task_href, activity or "Pulp task")
        else:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            message = result.stderr.strip().splitlines()
            raise RuntimeError(f"pulp {' '.join(args[:3])} failed: {message[-1] if message else result.returncode}")
        if not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"pulp {' '.join(args[:3])} returned invalid JSON") from exc

    def show(self, name: str) -> dict[str, Any]:
        result = self.cli("rpm", "repository", "show", "--name", name)
        if not isinstance(result, dict):
            raise RuntimeError(f"pulp rpm repository show returned no object for {name}")
        return result

    def version_count(self, repository: dict[str, Any]) -> int:
        url = self.base_url + repository["versions_href"]
        response = self.session.get(url, params={"limit": 1}, timeout=30)
        response.raise_for_status()
        return int(response.json()["count"])

    def update_limit(self, name: str, limit: int) -> dict[str, Any] | None:
        return self.cli(
            "rpm", "repository", "update", "--name", name,
            "--retain-repo-versions", str(limit), wait=True,
            activity=f"{name}: setting retention limit to {limit}",
        )

    def delete_version(self, name: str, number: int) -> None:
        self.cli(
            "rpm", "repository", "version", "destroy", "--repository", name,
            "--version", str(number), wait=True,
            activity=f"{name}: deleting version {number}",
        )

    def oldest_versions(self, name: str, count: int) -> list[int]:
        result = self.cli(
            "rpm", "repository", "version", "list", "--repository", name,
            "--ordering", "number", "--limit", str(count),
        )
        if not isinstance(result, list) or len(result) != count:
            raise RuntimeError(f"Pulp returned an incomplete version list for {name}")
        numbers = [version["number"] for version in result]
        if numbers != sorted(set(numbers)):
            raise RuntimeError(f"Pulp returned unordered or duplicate versions for {name}")
        return numbers

    def simple_protected_latest(self, repository: dict[str, Any]) -> bool:
        """Only support the topology where the latest RPM version is protected."""
        distributions = self.cli("distribution", "list", "--limit", "1000")
        if not isinstance(distributions, list) or len(distributions) >= 1000:
            raise RuntimeError("Pulp returned an incomplete distribution list")
        if any(d.get("publication") or d.get("repository_version") or d.get("checkpoint")
               for d in distributions):
            raise ValueError("direct or checkpoint distributions require manual protection review")
        matching = [d for d in distributions if d.get("repository") == repository["pulp_href"]]
        if len(matching) != 1 or matching[0].get("name") != repository["name"]:
            raise ValueError("expected exactly one matching RPM repository distribution")
        publications = self.cli(
            "rpm", "publication", "list", "--repository", repository["name"],
            "--ordering", "-pulp_created", "--limit", "1",
        )
        if (not isinstance(publications, list) or len(publications) != 1 or
                publications[0].get("repository_version") != repository.get("latest_version_href")):
            raise ValueError("latest repository version is not the latest RPM publication")
        return True

    def set_marker(self, name: str, marker: dict[str, Any]) -> None:
        self.cli(
            "rpm", "repository", "label", "set", "--repository", name,
            "--key", LABEL_KEY, "--value", json.dumps(marker, separators=(",", ":")),
        )

    def task(self, uuid: str) -> dict[str, Any]:
        return self.cli("task", "show", "--uuid", uuid)

    def repository_tasks(self, name: str, *args: str) -> list[dict[str, Any]]:
        result = self.cli("rpm", "repository", "task", "list", "--repository", name, *args)
        if not isinstance(result, list):
            raise RuntimeError(f"Pulp returned an invalid task list for {name}")
        return result


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def version_phrase(count: int) -> str:
    return f"{count} version{'s' if count != 1 else ''}"


def active_repository_tasks(client: PulpClient, name: str) -> list[dict[str, Any]]:
    return client.repository_tasks(
        name, "--limit", "100", "--state-in", "waiting", "--state-in", "running",
        "--state-in", "canceling",
    )


def require_repository_idle(client: PulpClient, name: str) -> None:
    active = active_repository_tasks(client, name)
    if active:
        task = active[0]
        task_id = task.get("pulp_href", "unknown").rstrip("/").rsplit("/", 1)[-1]
        raise RepositoryBusy(
            f"{len(active)} active repository task(s); first {task_id} "
            f"({task.get('state', 'unknown')})"
        )


def find_dispatched_update(client: PulpClient, name: str, repository_prn: str,
                           pending_raw: str, started_at: str) -> str:
    """Recover an update task ID only when the post-marker task is unambiguous."""
    current = client.show(name)
    if current.get("pulp_labels", {}).get(LABEL_KEY) != pending_raw:
        raise RuntimeError(f"{name}: pending label changed during dispatch; review before retrying")
    marker_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    tasks = client.repository_tasks(name, "--limit", "100", "--ordering", "-pulp_created")
    recent = []
    for task in tasks:
        created = task.get("pulp_created")
        if not isinstance(created, str):
            raise RuntimeError(f"{name}: repository task has no creation time; pending label needs review")
        try:
            created_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError(f"{name}: repository task has an invalid creation time") from exc
        if created_at.tzinfo is None:
            raise RuntimeError(f"{name}: repository task creation time lacks a timezone")
        if created_at >= marker_time:
            recent.append(task)
    if len(recent) != 1:
        raise RuntimeError(
            f"{name}: found {len(recent)} repository tasks since the pending marker; "
            "cannot identify the dispatched update; pending label needs review"
        )
    task = recent[0]
    href = task.get("pulp_href")
    if (task.get("name") != UPDATE_TASK_NAME or
            repository_prn not in (task.get("reserved_resources_record") or []) or
            not isinstance(href, str) or
            not href.startswith(f"/api/pulp/{client.domain}/api/v3/tasks/")):
        raise RuntimeError(f"{name}: the recent task is not this repository update; pending label needs review")
    task_id = href.rstrip("/").rsplit("/", 1)[-1]
    progress(f"{name}: recovered update task ID {task_id} ({href}) from the repository task list")
    return href


def wait_for_repository_idle(client: PulpClient, name: str, poll_seconds: int) -> None:
    """Pause retention verification while another task uses this repository."""
    last_report = 0.0
    paused = False
    while active := active_repository_tasks(client, name):
        now = time.monotonic()
        if not paused or now - last_report >= WATCH_HEARTBEAT_SECONDS:
            task = active[0]
            task_id = task.get("pulp_href", "unknown").rstrip("/").rsplit("/", 1)[-1]
            progress(
                f"{name}: waiting for {len(active)} active repository task(s); "
                f"first {task_id} ({task.get('name', 'unknown')}, {task.get('state', 'unknown')})"
            )
            last_report = now
        paused = True
        stop_event = getattr(client, "stop_event", None)
        if stop_event is not None:
            if stop_event.wait(poll_seconds):
                raise RuntimeError(f"{name}: watch interrupted while waiting for repository tasks")
        else:
            time.sleep(poll_seconds)
    if paused:
        progress(f"{name}: repository task queue is empty; resuming retention")


def predelete_candidates(client: PulpClient, repository: dict[str, Any], count: int,
                         next_limit: int) -> list[int]:
    """Find versions Pulp would prune, for the supported one-distribution topology."""
    name = repository["name"]
    require_repository_idle(client, name)
    if count <= next_limit:
        # Even if no publication exists yet, setting this limit cannot prune.
        return []
    client.simple_protected_latest(repository)
    eligible_count = count - 1  # the latest published version is protected
    current_limit = repository["retain_repo_versions"]
    if current_limit is not None and eligible_count > current_limit:
        raise ValueError(f"{name}: eligible versions already exceed the current limit")
    delete_count = max(0, eligible_count - next_limit)
    if delete_count == 0:
        return []
    numbers = client.oldest_versions(name, delete_count)
    latest_number = int(repository["latest_version_href"].rstrip("/").rsplit("/", 1)[-1])
    if numbers[-1] >= latest_number:
        raise ValueError(f"{name}: deletion plan includes the latest version")
    return numbers


def version_number(href: str) -> int:
    return int(href.rstrip("/").rsplit("/", 1)[-1])


def verify_predelete_progress(
    client: PulpClient, name: str, *, original: dict[str, Any],
    pending_raw: str, before_count: int, candidates: list[int], deleted_count: int,
) -> tuple[dict[str, Any], int]:
    """Accept appended versions while rejecting unexplained count or oldest-version changes."""
    current = client.show(name)
    if (current["retain_repo_versions"] != original["retain_repo_versions"] or
            current.get("pulp_labels", {}).get(LABEL_KEY) != pending_raw):
        raise ValueError(f"{name}: retention limit or pending label changed during cleanup")
    original_latest = version_number(original["latest_version_href"])
    current_latest = version_number(current["latest_version_href"])
    if current_latest < original_latest:
        raise ValueError(f"{name}: latest repository version moved backward")
    count = client.version_count(current)
    expected = before_count - deleted_count + current_latest - original_latest
    if count != expected:
        raise ValueError(f"{name}: version count {count} differs from expected {expected} during cleanup")
    if deleted_count < len(candidates):
        oldest = client.oldest_versions(name, 1)[0]
        if oldest != candidates[deleted_count]:
            raise ValueError(
                f"{name}: oldest version {oldest} differs from planned version "
                f"{candidates[deleted_count]} during cleanup"
            )
    return current, count


def verify_predelete_when_idle(
    client: PulpClient, name: str, *, poll_seconds: int,
    original: dict[str, Any], pending_raw: str, before_count: int,
    candidates: list[int], deleted_count: int,
) -> tuple[dict[str, Any], int]:
    """Wait for other repository tasks, then validate the deletion plan."""
    while True:
        wait_for_repository_idle(client, name, poll_seconds)
        try:
            current, count = verify_predelete_progress(
                client, name, original=original, pending_raw=pending_raw,
                before_count=before_count, candidates=candidates, deleted_count=deleted_count,
            )
        except ValueError:
            if active_repository_tasks(client, name):
                continue
            raise
        if not active_repository_tasks(client, name):
            return current, count


def reconcile_pending_watch_update(client: PulpClient, name: str,
                                   repository: dict[str, Any], raw_marker: str,
                                   marker: dict[str, Any]) -> str:
    """Close a pending direct update only when its PLP0024 task is proven safe."""
    expected_count = marker.get("expected_after_count")
    started_at = marker.get("started_at")
    if (marker.get("strategy") != "limit_update" or
            type(expected_count) is not int or expected_count < 0 or
            not isinstance(started_at, str)):
        raise ValueError(f"{name}: pending step needs manual reconciliation")
    try:
        task_href = find_dispatched_update(
            client, name, repository["prn"], raw_marker, started_at,
        )
    except RuntimeError as exc:
        raise ValueError(f"{name}: pending update task cannot be identified: {exc}") from exc
    task_id = task_href.rstrip("/").rsplit("/", 1)[-1]
    try:
        client.wait_for_task(task_href, f"{name}: checking pending retention update")
    except PulpTaskFailed as exc:
        task = exc.task
        error = task.get("error") or {}
        if (task.get("state") != "failed" or not isinstance(error, dict) or
                error.get("error_code") != IMMEDIATE_TIMEOUT_CODE):
            raise ValueError(
                f"{name}: pending update task {task_id} failed for a different reason"
            ) from exc
        result = reconcile_one(client, name, argparse.Namespace(
            target=marker["to"], expected_count=expected_count,
            before_count=None, task_uuid=task_id, apply=True,
        ))
        progress(f"{name}: automatically reconciled pending update task {task_id}")
        return result
    raise ValueError(f"{name}: completed update task {task_id} needs manual reconciliation")


def run_direct_one(client: PulpClient, name: str, args: argparse.Namespace) -> str:
    """Ask Pulp to prune at most one planned count step in a repository update."""
    repository = client.show(name)
    if repository["name"] != name:
        raise ValueError(f"Pulp returned {repository['name']!r} for {name!r}")
    current_limit = repository["retain_repo_versions"]
    raw_marker = repository.get("pulp_labels", {}).get(LABEL_KEY)
    marker = parse_marker(raw_marker)
    if raw_marker is not None and marker is not None and marker["state"] == "pending" and getattr(args, "watch", False):
        return reconcile_pending_watch_update(client, name, repository, raw_marker, marker)
    count = client.version_count(repository)
    step = plan_step(
        current_limit, count, marker, utc_now(), target=args.target,
        step_size=args.step, cooldown=timedelta(hours=args.cooldown_hours),
        adopt_existing=args.adopt_existing,
    )
    if step.next_limit is None:
        summary = f"{name}: {version_phrase(count)}, limit {current_limit}; {step.reason}"
        if marker is not None and step.reason.startswith("cooldown"):
            summary += f"; {completed_step_phrase(marker)}"
        return summary

    planned_prune_count = max(0, count - 1 - step.next_limit)
    if planned_prune_count:
        client.simple_protected_latest(repository)
    expected_after_count = count - planned_prune_count
    summary = (
        f"{name}: {version_phrase(count)}, limit {current_limit} -> {step.next_limit}; "
        f"prune {version_phrase(planned_prune_count)} in the repository update"
    )
    if not args.apply:
        return summary + " (dry run)"

    fresh = client.show(name)
    if (fresh["retain_repo_versions"] != current_limit or
            fresh.get("pulp_labels", {}).get(LABEL_KEY) != repository.get("pulp_labels", {}).get(LABEL_KEY) or
            fresh.get("latest_version_href") != repository.get("latest_version_href") or
            client.version_count(fresh) != count):
        raise RepositoryChanged("retention state changed during planning")
    require_repository_idle(client, name)
    pending = {
        "state": "pending", "strategy": "limit_update",
        "from": current_limit, "to": step.next_limit,
        "before_count": count, "planned_prune_count": planned_prune_count,
        "expected_after_count": expected_after_count,
        "started_at": utc_now().isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    client.set_marker(name, pending)
    pending_raw = json.dumps(pending, separators=(",", ":"))
    marked = client.show(name)
    if (marked["retain_repo_versions"] != current_limit or
            marked.get("pulp_labels", {}).get(LABEL_KEY) != pending_raw):
        raise RuntimeError(f"{name}: pending marker could not be verified")
    progress(f"{name}: {summary}; expected {version_phrase(expected_after_count)} afterward")

    started = time.monotonic()
    try:
        try:
            task = client.update_limit(name, step.next_limit)
        except PulpDispatchUncertain:
            task_href = find_dispatched_update(
                client, name, fresh["prn"], pending_raw, pending["started_at"],
            )
            task = client.wait_for_task(task_href, f"{name}: setting retention limit to {step.next_limit}")
    except PulpTaskFailed as exc:
        task = exc.task
        error = task.get("error") or {}
        if task.get("state") != "failed" or error.get("error_code") != IMMEDIATE_TIMEOUT_CODE:
            raise
        task_id = task["pulp_href"].rstrip("/").rsplit("/", 1)[-1]
        progress(f"{name}: task {task_id} timed out; verifying the durable limit and version count")
        result = reconcile_one(client, name, argparse.Namespace(
            target=step.next_limit, expected_count=expected_after_count,
            before_count=None, task_uuid=task_id, apply=True,
        ))
        total_wait = round(time.monotonic() - started, 1)
        metrics_phrase, _ = prune_metrics(count, expected_after_count, task)
        progress(f"{name}: prune metrics: {metrics_phrase}; local wait {total_wait}s")
        progress(f"{name}: failed task {task_id} reconciled after {total_wait}s")
        if total_wait > args.max_wait_seconds:
            raise StepWaitExceeded(
                f"{name}: retention step completed in {total_wait}s, exceeding the "
                f"{args.max_wait_seconds}s wait limit; stopping before other repositories"
            )
        return summary + f"; now {version_phrase(expected_after_count)}; {result}"

    wait_for_repository_idle(client, name, getattr(args, "poll_seconds", 60))
    updated = client.show(name)
    if (updated["retain_repo_versions"] != step.next_limit or
            updated.get("pulp_labels", {}).get(LABEL_KEY) != pending_raw):
        raise RuntimeError(f"{name}: update completed but the limit or pending label changed")
    after_count = client.version_count(updated)
    if after_count != expected_after_count:
        raise ValueError(
            f"{name}: update left {after_count} versions, expected {expected_after_count}; "
            "pending label needs review"
        )
    total_wait = round(time.monotonic() - started, 1)
    metrics_phrase, metrics = prune_metrics(count, after_count, task)
    progress(f"{name}: prune metrics: {metrics_phrase}; local wait {total_wait}s")
    completed_at = utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")
    completed = {
        "state": "completed", "strategy": "limit_update",
        "from": current_limit, "to": step.next_limit,
        "before_count": count, "after_count": after_count,
        "planned_prune_count": planned_prune_count,
        "completed_at": completed_at, "update_wait_seconds": total_wait,
    }
    completed.update(metrics)
    client.set_marker(name, completed)
    if client.show(name).get("pulp_labels", {}).get(LABEL_KEY) != json.dumps(completed, separators=(",", ":")):
        raise RuntimeError(f"{name}: completion label could not be verified")
    progress(f"{name}: completion label recorded at {completed_at}")
    if total_wait > args.max_wait_seconds:
        raise StepWaitExceeded(
            f"{name}: retention step completed in {total_wait}s, exceeding the "
            f"{args.max_wait_seconds}s wait limit; stopping before other repositories"
        )
    return (summary + f"; now {version_phrase(after_count)}; {metrics_phrase}; "
            f"wait {total_wait}s; completed {completed_at}")


def run_worker_one(client: PulpClient, name: str, args: argparse.Namespace) -> str:
    repository = client.show(name)
    if repository["name"] != name:
        raise ValueError(f"Pulp returned {repository['name']!r} for {name!r}")
    current_limit = repository["retain_repo_versions"]
    count = client.version_count(repository)
    marker = parse_marker(repository.get("pulp_labels", {}).get(LABEL_KEY))
    step = plan_step(
        current_limit, count, marker, utc_now(), target=args.target,
        step_size=args.step, cooldown=timedelta(hours=args.cooldown_hours),
        adopt_existing=args.adopt_existing,
    )
    if step.next_limit is None:
        summary = f"{name}: {version_phrase(count)}, limit {current_limit}; {step.reason}"
        if marker is not None and step.reason.startswith("cooldown"):
            summary += f"; {completed_step_phrase(marker)}"
        return summary
    summary = f"{name}: {version_phrase(count)}, limit {current_limit} -> {step.next_limit}"
    candidates = predelete_candidates(client, repository, count, step.next_limit)
    summary += f"; delete {version_phrase(len(candidates))} (oldest) via worker tasks first"
    if not args.apply:
        return summary + " (dry run)"

    # Recheck before dispatch. A concurrent administrator can still race this
    # operation, so only one retention controller should manage these repos.
    fresh = client.show(name)
    if (fresh["retain_repo_versions"] != current_limit or
            fresh.get("pulp_labels", {}).get(LABEL_KEY) != repository.get("pulp_labels", {}).get(LABEL_KEY) or
            fresh.get("latest_version_href") != repository.get("latest_version_href") or
            client.version_count(fresh) != count):
        raise RepositoryChanged("retention state changed during planning")
    require_repository_idle(client, name)

    pending = {
        "state": "pending",
        "from": current_limit,
        "to": step.next_limit,
        "before_count": count,
        "started_at": utc_now().isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    pending.update({
        "strategy": "predelete",
        "latest_version_href": repository["latest_version_href"],
        "planned_deletes": candidates,
    })
    client.set_marker(name, pending)
    pending_raw = json.dumps(pending, separators=(",", ":"))
    pending_repo = client.show(name)
    if pending_repo["retain_repo_versions"] != current_limit or pending_repo.get("pulp_labels", {}).get(LABEL_KEY) != pending_raw:
        raise RuntimeError(f"{name}: pending marker could not be verified")
    progress(
        f"{name}: starting {version_phrase(count)}, limit {current_limit} -> {step.next_limit}; "
        f"{version_phrase(len(candidates))} to delete via worker tasks"
    )

    delete_wait_seconds = 0.0
    remaining_count = count
    deleted_versions: list[int] = []
    applied_limit = step.next_limit
    wait_cutoff = False
    for index, number in enumerate(candidates, start=1):
        if getattr(args, "watch", False):
            _, before_delete_count = verify_predelete_when_idle(
                client, name, poll_seconds=args.poll_seconds, original=repository,
                pending_raw=pending_raw, before_count=count, candidates=candidates,
                deleted_count=len(deleted_versions),
            )
        else:
            _, before_delete_count = verify_predelete_progress(
                client, name, original=repository, pending_raw=pending_raw,
                before_count=count, candidates=candidates, deleted_count=len(deleted_versions),
            )
        progress(
            f"{name}: deleting version {number} ({index}/{len(candidates)}); "
            f"{version_phrase(before_delete_count)} remain before delete"
        )
        started = time.monotonic()
        client.delete_version(name, number)
        task_seconds = time.monotonic() - started
        delete_wait_seconds += task_seconds
        deleted_versions.append(number)
        if getattr(args, "watch", False):
            after_delete, remaining_count = verify_predelete_when_idle(
                client, name, poll_seconds=args.poll_seconds, original=repository,
                pending_raw=pending_raw, before_count=count, candidates=candidates,
                deleted_count=len(deleted_versions),
            )
        else:
            after_delete, remaining_count = verify_predelete_progress(
                client, name, original=repository, pending_raw=pending_raw,
                before_count=count, candidates=candidates, deleted_count=len(deleted_versions),
            )
        added_count = version_number(after_delete["latest_version_href"]) - version_number(repository["latest_version_href"])
        progress(
            f"{name}: deleted version {number} ({index}/{len(candidates)}) in {task_seconds:.1f}s; "
            f"{version_phrase(remaining_count)} now, {added_count} new since planning, "
            f"{delete_wait_seconds:.1f}s total delete wait"
        )
        if delete_wait_seconds > args.max_wait_seconds and len(deleted_versions) < len(candidates):
            wait_cutoff = True
            progress(f"{name}: wait cutoff reached; finishing with {len(deleted_versions)} deletes")
            applied_limit = max(step.next_limit, remaining_count - 1)
            if applied_limit > current_limit:
                raise RuntimeError(f"{name}: new versions outnumbered deletions; pending label needs review")
            pending["to"] = applied_limit
            pending["planned_deletes"] = deleted_versions
            client.set_marker(name, pending)
            pending_raw = json.dumps(pending, separators=(",", ":"))
            revised = client.show(name)
            if revised.get("pulp_labels", {}).get(LABEL_KEY) != pending_raw:
                raise RuntimeError(f"{name}: shortened pending marker could not be verified")
            break
    if getattr(args, "watch", False):
        current, remaining_count = verify_predelete_when_idle(
            client, name, poll_seconds=args.poll_seconds, original=repository,
            pending_raw=pending_raw, before_count=count, candidates=candidates,
            deleted_count=len(deleted_versions),
        )
    else:
        current, remaining_count = verify_predelete_progress(
            client, name, original=repository, pending_raw=pending_raw,
            before_count=count, candidates=candidates, deleted_count=len(deleted_versions),
        )
    if candidates:
        client.simple_protected_latest(current)
    applied_limit = max(applied_limit, remaining_count - 1)
    if current_limit is not None and applied_limit > current_limit:
        raise RuntimeError(f"{name}: new versions outnumbered deletions; pending label needs review")
    if pending["to"] != applied_limit:
        pending["to"] = applied_limit
        pending["planned_deletes"] = deleted_versions
        client.set_marker(name, pending)
        pending_raw = json.dumps(pending, separators=(",", ":"))
        if client.show(name).get("pulp_labels", {}).get(LABEL_KEY) != pending_raw:
            raise RuntimeError(f"{name}: revised pending marker could not be verified")

    started = time.monotonic()
    if applied_limit != current_limit:
        progress(
            f"{name}: setting retention limit {current_limit} -> {applied_limit}; "
            f"{version_phrase(remaining_count)} currently exist"
        )
        client.update_limit(name, applied_limit)
    else:
        progress(f"{name}: retention limit stays {current_limit}; no update task needed")
    duration_seconds = round(time.monotonic() - started, 1)
    updated = client.show(name)
    if updated["retain_repo_versions"] != applied_limit:
        raise RuntimeError(f"{name}: update completed but the new limit did not match")
    after_count = client.version_count(updated)
    completed_at = utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")
    label = {
        "state": "completed",
        "from": current_limit,
        "to": applied_limit,
        "before_count": count,
        "after_count": after_count,
        "completed_at": completed_at,
        "update_wait_seconds": duration_seconds,
    }
    label["deleted_versions"] = deleted_versions
    label["delete_wait_seconds"] = round(delete_wait_seconds, 1)
    client.set_marker(name, label)
    confirmed = client.show(name)
    if confirmed.get("pulp_labels", {}).get(LABEL_KEY) != json.dumps(label, separators=(",", ":")):
        raise RuntimeError(f"{name}: completion label could not be verified")
    progress(f"{name}: completion label recorded at {completed_at}")
    total_wait = round(delete_wait_seconds + duration_seconds, 1)
    if total_wait > args.max_wait_seconds:
        raise StepWaitExceeded(
            f"{name}: retention step completed in {total_wait}s, exceeding the "
            f"{args.max_wait_seconds}s wait limit; stopping before other repositories"
        )
    if applied_limit != step.next_limit:
        summary = f"{name}: {version_phrase(count)}, limit {current_limit} -> {applied_limit}; "
        summary += f"deleted {version_phrase(len(deleted_versions))}"
        summary += "; stopped batch after wait cutoff" if wait_cutoff else "; adjusted for new versions"
    return summary + f"; now {version_phrase(after_count)}; wait {total_wait}s; completed {completed_at}"


def run_one(client: PulpClient, name: str, args: argparse.Namespace) -> str:
    if args.direct_update:
        return run_direct_one(client, name, args)
    return run_worker_one(client, name, args)


def reconcile_predelete_one(client: PulpClient, name: str, args: argparse.Namespace) -> str:
    """Close a partial worker-delete step after verifying its effects."""
    repository = client.show(name)
    raw = repository.get("pulp_labels", {}).get(LABEL_KEY)
    marker = parse_marker(raw)
    if marker is None or marker["state"] != "pending" or marker.get("strategy") != "predelete":
        raise ValueError(f"{name}: there is no pending worker-delete step")
    limit = repository["retain_repo_versions"]
    if type(limit) is not int or limit != marker.get("from"):
        raise ValueError(f"{name}: current limit differs from the pending step's starting limit")
    before_count = marker.get("before_count")
    candidates = marker.get("planned_deletes")
    original_latest = marker.get("latest_version_href")
    if (type(before_count) is not int or before_count < 1 or
            not isinstance(candidates, list) or not candidates or
            any(type(number) is not int or number < 0 for number in candidates) or
            candidates != sorted(set(candidates)) or
            not isinstance(original_latest, str) or
            not original_latest.startswith(repository["versions_href"])):
        raise ValueError(f"{name}: pending worker-delete plan is invalid")
    if client.repository_tasks(
        name, "--limit", "100", "--state-in", "waiting", "--state-in", "running",
        "--state-in", "canceling",
    ):
        raise ValueError(f"{name}: repository tasks are still active")
    oldest = client.oldest_versions(name, 1)[0]
    deleted_count = sum(number < oldest for number in candidates)
    if deleted_count < len(candidates) and candidates[deleted_count] != oldest:
        raise ValueError(f"{name}: oldest version does not match the planned delete prefix")
    original = {**repository, "latest_version_href": original_latest}
    current, count = verify_predelete_progress(
        client, name, original=original, pending_raw=raw,
        before_count=before_count, candidates=candidates, deleted_count=deleted_count,
    )
    client.simple_protected_latest(current)
    summary = (
        f"{name}: verified {deleted_count} planned deletes, {before_count} -> {count} versions; "
        f"limit remains {limit}"
    )
    if not args.apply:
        return summary + "; would close pending label (dry run)"
    if client.repository_tasks(
        name, "--limit", "100", "--state-in", "waiting", "--state-in", "running",
        "--state-in", "canceling",
    ):
        raise ValueError(f"{name}: repository tasks became active")
    current, count = verify_predelete_progress(
        client, name, original=original, pending_raw=raw,
        before_count=before_count, candidates=candidates, deleted_count=deleted_count,
    )
    client.simple_protected_latest(current)
    completed_at = utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")
    completed = {
        "state": "completed", "from": limit, "to": limit,
        "before_count": before_count, "after_count": count,
        "completed_at": completed_at,
        "deleted_versions": candidates[:deleted_count],
        "interruption": (
            "batch stopped before worker deletes" if deleted_count == 0
            else "repository changed during worker deletes"
        ),
    }
    client.set_marker(name, completed)
    confirmed = client.show(name)
    if confirmed.get("pulp_labels", {}).get(LABEL_KEY) != json.dumps(completed, separators=(",", ":")):
        raise RuntimeError(f"{name}: reconciliation label could not be verified")
    return summary + f"; pending label closed at {completed_at}"


def reconcile_one(client: PulpClient, name: str, args: argparse.Namespace) -> str:
    """Reconcile a timed-out update only after checking its durable effects."""
    repository = client.show(name)
    raw_marker = repository.get("pulp_labels", {}).get(LABEL_KEY)
    marker = parse_marker(raw_marker)
    if marker is None or marker["state"] != "pending":
        raise ValueError(f"{name}: there is no pending retention step")
    previous_limit = marker.get("from")
    if previous_limit is not None and (type(previous_limit) is not int or previous_limit < 1):
        raise ValueError(f"{name}: pending marker has an invalid previous limit")
    try:
        started_at = datetime.fromisoformat(marker["started_at"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError) as exc:
        raise ValueError(f"{name}: pending marker has an invalid start time") from exc
    if started_at.tzinfo is None:
        raise ValueError(f"{name}: pending marker start time lacks a timezone")
    if repository["retain_repo_versions"] != marker["to"] or marker["to"] != args.target:
        raise ValueError(f"{name}: current limit, pending limit, and target must match")

    before_count = marker.get("before_count", args.before_count)
    if before_count is None or type(before_count) is not int or before_count < 0:
        raise ValueError(f"{name}: provide --before-count for a marker without one")
    if args.before_count is not None and before_count != args.before_count:
        raise ValueError(f"{name}: --before-count disagrees with the pending marker")
    count = client.version_count(repository)
    if count != args.expected_count:
        raise ValueError(f"{name}: version count {count} differs from --expected-count {args.expected_count}")

    task = client.task(args.task_uuid)
    if task.get("name") != UPDATE_TASK_NAME or task.get("state") != "failed":
        raise ValueError(f"{name}: task is not a failed repository update")
    error = task.get("error") or {}
    if not isinstance(error, dict) or error.get("error_code") != IMMEDIATE_TIMEOUT_CODE:
        raise ValueError(f"{name}: task did not fail with {IMMEDIATE_TIMEOUT_CODE}")
    if repository.get("prn") not in task.get("reserved_resources_record", []):
        raise ValueError(f"{name}: task did not reserve this repository")
    try:
        created_at = datetime.fromisoformat(task["pulp_created"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError) as exc:
        raise ValueError(f"{name}: task has an invalid creation time") from exc
    if created_at.tzinfo is None:
        raise ValueError(f"{name}: task creation time lacks a timezone")
    if created_at < started_at:
        raise ValueError(f"{name}: task predates the pending marker")
    if not task.get("finished_at"):
        raise ValueError(f"{name}: failed task has no finish time")
    latest = client.repository_tasks(name, "--limit", "1", "--ordering", "-pulp_created")
    if not latest or latest[0].get("pulp_href") != task.get("pulp_href"):
        raise ValueError(f"{name}: a newer repository task exists")
    active = client.repository_tasks(
        name, "--limit", "100", "--state-in", "waiting", "--state-in", "running",
        "--state-in", "canceling",
    )
    if active:
        raise ValueError(f"{name}: repository tasks are still active")

    summary = (
        f"{name}: verified limit {marker['to']}, {before_count} -> {count} versions; "
        f"failed task {args.task_uuid} ({IMMEDIATE_TIMEOUT_CODE})"
    )
    if marker.get("strategy") == "limit_update":
        metrics_phrase, metrics = prune_metrics(before_count, count, task)
        summary += f"; {metrics_phrase}"
    if not args.apply:
        return summary + "; would reconcile pending label (dry run)"

    fresh = client.show(name)
    if (fresh["retain_repo_versions"] != marker["to"] or
            fresh.get("pulp_labels", {}).get(LABEL_KEY) != raw_marker or
            client.version_count(fresh) != count):
        raise ValueError(f"{name}: retention state changed during reconciliation")
    latest_again = client.repository_tasks(name, "--limit", "1", "--ordering", "-pulp_created")
    if not latest_again or latest_again[0].get("pulp_href") != task["pulp_href"]:
        raise ValueError(f"{name}: a newer repository task appeared during reconciliation")
    if client.repository_tasks(
        name, "--limit", "100", "--state-in", "waiting", "--state-in", "running",
        "--state-in", "canceling",
    ):
        raise ValueError(f"{name}: a repository task became active during reconciliation")
    reconciled_at = utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")
    completed = {
        "state": "completed",
        "from": previous_limit,
        "to": marker["to"],
        "before_count": before_count,
        "after_count": count,
        "completed_at": reconciled_at,
        "reconciliation": {
            "task_href": task["pulp_href"],
            "task_state": "failed",
            "error_code": IMMEDIATE_TIMEOUT_CODE,
            "task_finished_at": task.get("finished_at"),
        },
    }
    if marker.get("strategy") == "limit_update":
        completed["strategy"] = "limit_update"
        completed["planned_prune_count"] = marker.get("planned_prune_count")
        completed.update(metrics)
    client.set_marker(name, completed)
    confirmed = client.show(name)
    if confirmed.get("pulp_labels", {}).get(LABEL_KEY) != json.dumps(completed, separators=(",", ":")):
        raise RuntimeError(f"{name}: reconciliation label could not be verified")
    return summary + f"; label reconciled at {reconciled_at}"


def watch_status_key(message: str) -> str:
    match = re.search(r"cooldown until (\S+)", message)
    return f"cooldown:{match.group(1)}" if match else message


@dataclass(frozen=True)
class WatchOutcome:
    message: str | None = None
    blocked_reason: str | None = None
    unblocked: bool = True


def watch_attempt(client: PulpClient, name: str, args: argparse.Namespace) -> WatchOutcome:
    try:
        return WatchOutcome(message=run_one(client, name, args))
    except RepositoryBusy as exc:
        return WatchOutcome(message=f"{name}: busy: {exc}; retrying when idle")
    except RepositoryChanged as exc:
        return WatchOutcome(message=f"{name}: {exc}; retrying with a fresh plan")
    except StepWaitExceeded as exc:
        return WatchOutcome(message=str(exc).replace(
            "; stopping before other repositories",
            "; continuing with other repositories in watch mode",
        ))
    except (OSError, requests.RequestException, RuntimeError) as exc:
        return WatchOutcome(message=f"{name}: retrying after error: {exc}", unblocked=False)
    except (KeyError, ValueError) as exc:
        return WatchOutcome(blocked_reason=str(exc), unblocked=False)


def record_watch_outcome(name: str, outcome: WatchOutcome, args: argparse.Namespace,
                         completed: set[str], blocked: dict[str, str],
                         last_status: dict[str, str]) -> None:
    if outcome.blocked_reason is not None:
        if blocked.get(name) != outcome.blocked_reason:
            progress(f"{name}: blocked: {outcome.blocked_reason}; rechecking in {args.poll_seconds}s")
        blocked[name] = outcome.blocked_reason
        return
    if outcome.unblocked and name in blocked:
        del blocked[name]
        progress(f"{name}: block cleared; resuming retention")
    if outcome.message is None:
        return
    key = watch_status_key(outcome.message)
    if key != last_status.get(name):
        with OUTPUT_LOCK:
            print(outcome.message, flush=True)
        last_status[name] = key
    if outcome.message.endswith("; target reached"):
        completed.add(name)


def report_watch_status(repos: tuple[str, ...], completed: set[str],
                        blocked: dict[str, str]) -> None:
    status = (
        f"watch status: {len(completed)} target reached, {len(blocked)} blocked, "
        f"{len(repos) - len(completed) - len(blocked)} still active"
    )
    if blocked:
        status += f"; blocked: {', '.join(sorted(blocked))}"
    progress(status)


def watch_repositories(client: PulpClient, repos: tuple[str, ...], args: argparse.Namespace) -> int:
    """Revisit idle repositories until their limits reach the target."""
    completed: set[str] = set()
    blocked: dict[str, str] = {}
    last_status: dict[str, str] = {}
    last_heartbeat = time.monotonic()
    progress(
        f"watching {', '.join(repos)} toward limit {args.target}; "
        f"checking every {args.poll_seconds}s"
    )
    while len(completed) < len(repos):
        for name in repos:
            if name in completed:
                continue
            record_watch_outcome(
                name, watch_attempt(client, name, args), args, completed, blocked, last_status,
            )
        if len(completed) == len(repos):
            break
        now = time.monotonic()
        if now - last_heartbeat >= WATCH_HEARTBEAT_SECONDS:
            report_watch_status(repos, completed, blocked)
            last_heartbeat = now
        time.sleep(args.poll_seconds)
    progress("watch complete: all repository limits reached the target")
    return 0


def watch_repositories_parallel(config: Path, domain: str, repos: tuple[str, ...],
                                args: argparse.Namespace) -> int:
    """Keep independent repository steps running without waiting on slower peers."""
    stop_event = threading.Event()
    clients = {name: PulpClient(config, domain, stop_event) for name in repos}
    completed: set[str] = set()
    blocked: dict[str, str] = {}
    last_status: dict[str, str] = {}
    next_check = {name: time.monotonic() for name in repos}
    running: dict[Future[WatchOutcome], str] = {}
    last_heartbeat = time.monotonic()
    progress(
        f"watching {', '.join(repos)} in parallel toward limit {args.target}; "
        f"checking each repository every {args.poll_seconds}s"
    )
    with ThreadPoolExecutor(max_workers=len(repos)) as executor:
        try:
            while len(completed) < len(repos):
                now = time.monotonic()
                running_names = set(running.values())
                for name in repos:
                    if name not in completed and name not in running_names and now >= next_check[name]:
                        running[executor.submit(watch_attempt, clients[name], name, args)] = name
                waiting = [next_check[name] - now for name in repos
                           if name not in completed and name not in running.values()]
                timeout = min(max(0.0, last_heartbeat + WATCH_HEARTBEAT_SECONDS - now),
                              max(0.0, min(waiting, default=float("inf"))))
                if running:
                    done, _ = wait(running, timeout=timeout, return_when=FIRST_COMPLETED)
                    for future in done:
                        name = running.pop(future)
                        record_watch_outcome(name, future.result(), args, completed, blocked, last_status)
                        next_check[name] = time.monotonic() + args.poll_seconds
                elif timeout != float("inf"):
                    time.sleep(timeout)
                now = time.monotonic()
                if len(completed) < len(repos) and now - last_heartbeat >= WATCH_HEARTBEAT_SECONDS:
                    report_watch_status(repos, completed, blocked)
                    last_heartbeat = now
        except KeyboardInterrupt:
            stop_event.set()
            raise
    progress("watch complete: all repository limits reached the target")
    return 0


def run_repositories_parallel(config: Path, domain: str, repos: tuple[str, ...],
                              args: argparse.Namespace) -> int:
    """Take one step per default repository concurrently."""
    clients = {name: PulpClient(config, domain) for name in repos}
    failed = False
    with ThreadPoolExecutor(max_workers=len(repos)) as executor:
        futures = {executor.submit(run_one, clients[name], name, args): name for name in repos}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                with OUTPUT_LOCK:
                    print(result, flush=True)
            except (KeyError, ValueError, RuntimeError, OSError, requests.RequestException) as exc:
                with OUTPUT_LOCK:
                    print(f"{name}: stopped: {exc}", file=sys.stderr, flush=True)
                failed = True
    return int(failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--domain", default="public-hummingbird")
    parser.add_argument("--repos", help="comma-separated RPM repositories; omitted defaults run in parallel")
    parser.add_argument("--target", type=int, default=100)
    parser.add_argument("--step", type=int, default=10,
                        help="maximum number of existing versions to prune per repository update")
    parser.add_argument("--cooldown-hours", type=int,
                        help="minimum hours between steps (default: 24 for one pass, 0 for watch)")
    parser.add_argument("--watch", action="store_true",
                        help="keep checking idle repositories until every limit reaches the target")
    parser.add_argument("--poll-seconds", type=int, default=60,
                        help="seconds between checks in --watch mode (default: 60)")
    parser.add_argument("--max-wait-seconds", type=int, default=1800,
                        help="stop after a slow completed step; worker mode can shorten a delete batch")
    parser.add_argument("--apply", action="store_true", help="perform retention updates; default is read-only")
    strategy = parser.add_mutually_exclusive_group()
    strategy.add_argument("--direct-update", action="store_true",
                          help="use Pulp retention pruning in the update (the default)")
    strategy.add_argument("--worker-predelete", dest="direct_update", action="store_false",
                          help="use the previous one-worker-task-per-version strategy")
    parser.set_defaults(direct_update=True)
    parser.add_argument("--adopt-existing", action="store_true",
                        help="allow an existing finite limit without a prior retention_step label")
    parser.add_argument("--reconcile-pending", action="store_true",
                        help="verify a timed-out update and mark its pending label completed")
    parser.add_argument("--reconcile-predelete", action="store_true",
                        help="verify partial worker deletes and close their pending label")
    parser.add_argument("--task-uuid", help="failed Pulp update task UUID for reconciliation")
    parser.add_argument("--expected-count", type=int,
                        help="verified current repository-version count for reconciliation")
    parser.add_argument("--before-count", type=int,
                        help="version count before update, for older pending labels")
    args = parser.parse_args(argv)
    if args.cooldown_hours is None:
        args.cooldown_hours = 0 if args.watch else 24
    if (args.target < 1 or args.step < 1 or args.cooldown_hours < 0 or
            args.max_wait_seconds < 1 or args.poll_seconds < 1):
        parser.error("target, step, max-wait-seconds, and poll-seconds must be positive; cooldown-hours must be nonnegative")
    repos = (DEFAULT_REPOS if args.repos is None else
             tuple(name.strip() for name in args.repos.split(",") if name.strip()))
    if not repos or len(repos) != len(set(repos)):
        parser.error("repos must be a nonempty list without duplicates")
    if args.reconcile_pending and args.reconcile_predelete:
        parser.error("choose only one reconciliation mode")
    if args.watch and (not args.apply or args.reconcile_pending or args.reconcile_predelete):
        parser.error("--watch requires --apply and cannot be combined with reconciliation")
    if args.reconcile_predelete:
        if len(repos) != 1 or args.task_uuid or args.expected_count is not None or args.before_count is not None:
            parser.error("worker-delete reconciliation requires one repo and no task/count arguments")
    elif args.reconcile_pending:
        if len(repos) != 1 or not args.task_uuid or args.expected_count is None:
            parser.error("reconciliation requires one repo, --task-uuid, and --expected-count")
        if args.adopt_existing:
            parser.error("--adopt-existing cannot be used with --reconcile-pending")
        try:
            UUID(args.task_uuid)
        except ValueError as exc:
            parser.error(f"invalid --task-uuid: {exc}")
        if args.expected_count < 0 or (args.before_count is not None and args.before_count < 0):
            parser.error("version counts must be nonnegative")
    elif args.task_uuid or args.expected_count is not None or args.before_count is not None:
        parser.error("task-uuid, expected-count, and before-count require --reconcile-pending")
    config = args.config.expanduser()
    if args.watch:
        try:
            if args.repos is None:
                return watch_repositories_parallel(config, args.domain, repos, args)
            client = PulpClient(config, args.domain)
            return watch_repositories(client, repos, args)
        except KeyboardInterrupt:
            progress("watch interrupted; inspect any pending retention label before restarting")
            return 130
    if args.repos is None:
        return run_repositories_parallel(config, args.domain, repos, args)
    client = PulpClient(config, args.domain)
    for name in repos:
        try:
            if args.reconcile_predelete:
                result = reconcile_predelete_one(client, name, args)
            elif args.reconcile_pending:
                result = reconcile_one(client, name, args)
            else:
                result = run_one(client, name, args)
            print(result, flush=True)
        except (KeyError, ValueError, RuntimeError, OSError, requests.RequestException) as exc:
            print(f"{name}: stopped: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
