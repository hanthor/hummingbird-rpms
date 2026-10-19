"""Measure overlapping Make recipes rather than merely trusting an expanded macro."""

import json
import os
from pathlib import Path
import sys
import time


def main() -> None:
    if sys.argv[1] == "worker":
        start = time.monotonic_ns()
        time.sleep(1)
        end = time.monotonic_ns()
        Path(f"{sys.argv[2]}.json").write_text(json.dumps([start, end]))
        return

    expected, macro, environment = map(int, sys.argv[2:])
    intervals = [json.loads(path.read_text()) for path in Path.cwd().glob("job-*.json")]
    if len(intervals) != 8:
        raise RuntimeError(f"Expected 8 completed recipes, found {len(intervals)}")
    events = sorted((timestamp, delta) for start, end in intervals
                    for timestamp, delta in [(start, 1), (end, -1)])
    active = peak = 0
    for _, delta in events:
        active += delta
        peak = max(peak, active)
    report = {
        "expected_jobs": expected,
        "rpm_macro_jobs": macro,
        "rpm_build_ncpus": environment,
        "affinity_cpus": len(os.sched_getaffinity(0)),
        "observed_peak_recipes": peak,
        "completed_recipes": len(intervals),
    }
    print(json.dumps(report, indent=2))
    if (macro, environment, peak) != (expected, expected, expected):
        raise RuntimeError(f"Parallelism override did not take effect: {report}")


if __name__ == "__main__":
    main()
