#!/usr/bin/env python3
"""Capture an RPM Release and its direct signing runs across two clusters."""

import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import time

RELEASE_NAMESPACE = "hummingbird-tenant"
INTERNAL_NAMESPACE = "internal-services"
OWNER_LABEL = "internal-services.appstudio.openshift.io/pipelinerun-uid"
LOG_STEPS = (
    "step-filter-artifact-json",
    "step-download",
    "step-sign-and-verify-rpms",
    "step-create-trusted-artifact",
    "step-merge-signed-rpms",
    "step-upload-signed-rpms-to-pulp",
)
MEMORY_STEPS = ("step-download", "step-sign-and-verify-rpms", "step-create-trusted-artifact")


def stamp():
    return datetime.datetime.now(datetime.UTC).isoformat()


def say(message):
    print(f"{stamp()} {message}", flush=True)


def oc(context, *args, timeout=25):
    try:
        result = subprocess.run(
            ["oc", "--context", context, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, str(error)
    if result.returncode:
        return None, result.stderr.strip() or f"oc exited {result.returncode}"
    return result.stdout, None


def get_json(context, *args):
    options = args if "--raw" in args else (*args, "-o", "json")
    output, error = oc(context, *options)
    if output is None:
        return None, error
    try:
        return json.loads(output), None
    except json.JSONDecodeError as error:
        return None, str(error)


def save(path, content):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content)
    temporary.chmod(0o600)
    temporary.replace(path)


def save_json(path, value):
    save(path, json.dumps(value, indent=2) + "\n")


def append_sample(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a") as output:
        output.write(json.dumps(value) + "\n")


def condition(resource, kind="Succeeded"):
    return next(
        (item for item in resource.get("status", {}).get("conditions", []) if item.get("type") == kind),
        {},
    )


def capture_pod(args, target, pod):
    name = pod["metadata"]["name"]
    pod_dir = target / name
    save_json(pod_dir / "pod.json", pod)
    events, _ = get_json(
        args.signing_context,
        "get", "events", "-n", INTERNAL_NAMESPACE,
        "--field-selector", f"involvedObject.name={name}",
    )
    if events is not None:
        save_json(pod_dir / "events.json", events)

    names = {container["name"] for container in pod.get("spec", {}).get("containers", [])}
    states = {item["name"]: item.get("state", {}) for item in pod.get("status", {}).get("containerStatuses", [])}
    metrics, _ = get_json(
        args.signing_context, "get", "--raw",
        f"/apis/metrics.k8s.io/v1beta1/namespaces/{INTERNAL_NAMESPACE}/pods/{name}",
    )
    if metrics is not None:
        append_sample(pod_dir / "metrics.jsonl", {"capturedAt": stamp(), "metrics": metrics})

    for step in MEMORY_STEPS:
        if "running" not in states.get(step, {}):
            continue
        output, _ = oc(
            args.signing_context, "exec", f"pod/{name}", "-n", INTERNAL_NAMESPACE,
            "-c", step, "--", "cat", "/sys/fs/cgroup/memory.current",
            "/sys/fs/cgroup/memory.peak", timeout=12,
        )
        if output is None:
            continue
        values = output.splitlines()
        if len(values) == 2 and all(value.isdecimal() for value in values):
            append_sample(
                pod_dir / "memory.jsonl",
                {"capturedAt": stamp(), "step": step, "currentBytes": int(values[0]),
                 "peakBytes": int(values[1])},
            )

    for step in LOG_STEPS:
        if step not in names:
            continue
        output, _ = oc(
            args.signing_context, "logs", f"pod/{name}", "-n", INTERNAL_NAMESPACE,
            "-c", step, "--timestamps=true", timeout=15,
        )
        if output is not None:
            log_path = pod_dir / f"{step}.log"
            if output and (not log_path.exists() or len(output) >= log_path.stat().st_size):
                save(log_path, output)


def capture(args):
    start = time.monotonic()
    completion_seen = None
    last_state = None
    managed_uid = None
    final_captures = {}
    while True:
        release, error = get_json(
            args.rh03_context, "get", "release", args.release, "-n", RELEASE_NAMESPACE,
        )
        if release is None:
            state = f"waiting for Release: {error}"
        else:
            save_json(args.output / "release.json", release)
            managed_ref = (release.get("status", {}).get("managedProcessing") or {}).get("pipelineRun", "")
            state = f"Release {condition(release, 'Released').get('status', 'Unknown')}; managed {managed_ref or 'pending'}"
            if managed_ref and "/" in managed_ref:
                managed_namespace, managed_name = managed_ref.split("/", 1)
                managed, error = get_json(
                    args.rh03_context, "get", "pipelinerun", managed_name, "-n", managed_namespace,
                )
                if managed is not None:
                    save_json(args.output / "managed-pipelinerun.json", managed)
                    managed_uid = managed["metadata"]["uid"]
                elif error:
                    state += f"; managed lookup: {error}"
                if managed_uid:
                    selector = f"{OWNER_LABEL}={managed_uid}"
                    requests, _ = get_json(
                        args.rh03_context, "get", "internalrequests", "-n", managed_namespace,
                        "-l", selector,
                    )
                    if requests is not None:
                        save_json(args.output / "internalrequests.json", requests)
                    direct_runs, error = get_json(
                        args.signing_context, "get", "pipelineruns", "-n", INTERNAL_NAMESPACE,
                        "-l", f"{selector},tekton.dev/pipeline=direct-rpm-signing",
                    )
                    if direct_runs is not None:
                        for run in direct_runs.get("items", []):
                            name = run["metadata"]["name"]
                            target = args.output / name
                            save_json(target / "pipelinerun.json", run)
                            state += f"; {name}={condition(run).get('reason', 'Running')}"
                            final = condition(run).get("status") in ("True", "False")
                            if final and final_captures.get(name, 0) >= 2:
                                continue
                            events, _ = get_json(
                                args.signing_context, "get", "events", "-n", INTERNAL_NAMESPACE,
                                "--field-selector", f"involvedObject.name={name}",
                            )
                            if events is not None:
                                save_json(target / "events.json", events)
                            taskruns, _ = get_json(
                                args.signing_context, "get", "taskruns", "-n", INTERNAL_NAMESPACE,
                                "-l", f"tekton.dev/pipelineRun={name}",
                            )
                            if taskruns is not None:
                                save_json(target / "taskruns.json", taskruns)
                            pods, _ = get_json(
                                args.signing_context, "get", "pods", "-n", INTERNAL_NAMESPACE,
                                "-l", f"tekton.dev/pipelineRun={name}",
                            )
                            if pods is not None:
                                for pod in pods.get("items", []):
                                    capture_pod(args, target, pod)
                            if final:
                                final_captures[name] = final_captures.get(name, 0) + 1
                    elif error:
                        state += f"; direct lookup: {error}"

            if release.get("status", {}).get("completionTime"):
                completion_seen = completion_seen or time.monotonic()
            else:
                completion_seen = None
        if state != last_state:
            say(state)
            last_state = state
        if args.once:
            return
        if completion_seen and time.monotonic() - completion_seen >= args.post_completion:
            say("Release completed; final evidence captured")
            return
        if time.monotonic() - start >= args.timeout:
            say("Monitor timeout reached")
            return
        time.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", help="rh03 Release name")
    parser.add_argument("--rh03-context", required=True, help="oc context for rh03")
    parser.add_argument("--signing-context", required=True, help="oc context for the signing cluster")
    parser.add_argument("--output", type=Path, required=True, help="private evidence directory")
    parser.add_argument("--interval", type=int, default=10, help="poll interval in seconds")
    parser.add_argument("--timeout", type=int, default=10800, help="maximum runtime in seconds")
    parser.add_argument("--post-completion", type=int, default=60, help="seconds to capture after completion")
    parser.add_argument("--once", action="store_true", help="take one snapshot and exit")
    args = parser.parse_args()
    if args.interval < 1 or args.timeout < 1 or args.post_completion < 0:
        parser.error("interval and timeout must be positive; post-completion must be nonnegative")
    os.umask(0o077)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.output.chmod(0o700)
    capture(args)


if __name__ == "__main__":
    main()
