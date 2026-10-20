"""Explicit CI adoption policy; standalone local tests remain unrestricted."""

import os
import subprocess
from pathlib import Path

from tmt_rpmtest.execution.coverage import functional_coverage


def decision(component: str, tests: Path | None = None) -> dict:
    mode = os.environ.get("RPMTEST_ROLLOUT", "adopted")
    if mode not in {"off", "adopted"}:
        raise ValueError(f"Invalid rpmtest rollout mode: {mode!r}")
    tree = tests or Path(os.environ.get("TMT_TREE", ".")) / "test/rpmtest"
    return {
        "component": component,
        "mode": mode,
        "enabled": mode != "off" and bool(functional_coverage(tree, component)["tests"]),
    }


def report_skip(reason: str) -> None:
    print(reason, flush=True)
    if os.environ.get("TMT_TEST_PIDFILE"):
        subprocess.run(["tmt-report-result", "/", "skip"], check=True, timeout=30)
