#!/usr/bin/env python3
"""Run Mock with a standard CPU variance profile, preserving RPM resource limits."""

import argparse
import json
import os
from pathlib import Path
import socket


def cpu_profile(available: list[int], profile: str) -> tuple[list[int], int | None]:
    """Select real allowed CPU IDs, which need not start at zero or be contiguous."""
    if not available:
        raise ValueError("The build process has no available CPUs")
    available = sorted(available)
    if profile == "normal":
        return available, None
    if profile != "varied":
        raise ValueError(f"Unknown variance profile: {profile}")
    count = max(1, len(available) * 7 // 8)
    return available[:count], count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["normal", "varied"], required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or command[0] != "mock":
        parser.error("provide a Mock command after --")
    available = sorted(os.sched_getaffinity(0))
    selected, cap = cpu_profile(available, args.profile)
    if cap is not None:
        os.sched_setaffinity(0, selected)
        command += ["--define", f"_smp_ncpus_max {cap}"]
    report = {
        "policy_version": 1,
        "profile": args.profile,
        "hostname": socket.gethostname(),
        "available_cpu_ids": available,
        "effective_cpu_ids": sorted(os.sched_getaffinity(0)),
        "rpm_cpu_cap": cap,
        "cpu_affinity_varied": selected != available,
        "command": command,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Build variance: {args.profile}, {len(available)} -> {len(selected)} CPUs; "
          f"RPM cap: {cap}", flush=True)
    # Mock and its build processes inherit this affinity. This is not CPU masking
    # or a cgroup boundary: privileged descendants can change their own affinity.
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
