"""Bounded readiness polling through a caller's recorded command interface."""

import subprocess
import time


def wait_ready(run, argv, timeout):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        if run(argv, timeout=remaining).returncode == 0:
            return
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
