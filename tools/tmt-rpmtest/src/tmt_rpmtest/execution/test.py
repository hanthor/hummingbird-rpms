"""Prepare a selected test's RPM environment and run its Python assertions."""

import argparse
import os
import runpy
import signal
import subprocess
import uuid
from pathlib import Path

from tmt_rpmtest.environment.artifacts import verify as verify_artifacts
from tmt_rpmtest.environment.lifecycle import finish, prepare
from tmt_rpmtest.environment.plan import settings_from_plan
from tmt_rpmtest.execution.contract import check_contract
from tmt_rpmtest.execution.rollout import decision, report_skip


def execute(plan: Path, script: Path):
    if component := os.environ.get("IMAGE_NAME"):
        if not decision(component)["enabled"]:
            report_skip("Component has no enabled rpmtest suite or rpmtest is disabled.")
            return
    if os.environ.get("RPMTEST_NO_NATIVE") == "true":
        print("Build contains only RPMs for another architecture.")
        subprocess.run(["tmt-report-result", "/", "skip"], check=True, timeout=30)
        return
    # Local tmt plans already prepare their own environment through the plugin.
    if os.environ.get("RPMTEST_DIRECTORY"):
        check_contract(Path(os.environ["RPMTEST_DIRECTORY"]))
        runpy.run_path(str(script), run_name="__main__")
        return
    settings, root = settings_from_plan(plan)
    if artifacts := os.environ.get("RPMTEST_RPMS"):
        if catalog := os.environ.get("RPMTEST_ARTIFACT_MANIFEST"):
            verify_artifacts(Path(catalog), Path(artifacts))
        settings["artifacts"] = [str(Path(artifacts) / "*.rpm")]
    directory = Path(os.environ["TMT_PLAN_DATA"]) / "rpmtest" / f"test-{uuid.uuid4().hex}"
    try:
        prepare(
            settings,
            directory,
            root,
            publish=False,
            cache=Path(
                os.environ.get(
                    "RPMTEST_CACHE", str(Path(os.environ["TMT_PLAN_DATA"]) / "rpmtest-cache")
                )
            ),
        )
        os.environ["RPMTEST_DIRECTORY"] = str(directory)
        check_contract(directory)
        runpy.run_path(str(script), run_name="__main__")
    finally:
        os.environ.pop("RPMTEST_DIRECTORY", None)
        finish(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("script", type=Path)
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt("RPM test interrupted.")

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGHUP, interrupt)
    execute(args.plan, args.script)


if __name__ == "__main__":
    main()
