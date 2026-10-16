"""Prepare a selected test's RPM environment and run its Python assertions."""

import argparse
import os
import runpy
import signal
import uuid
from pathlib import Path

from tmt_rpmtest.environment.lifecycle import finish, prepare
from tmt_rpmtest.environment.plan import settings_from_plan


def execute(plan: Path, script: Path):
    # Local tmt plans already prepare their own environment through the plugin.
    if os.environ.get("RPMTEST_DIRECTORY"):
        runpy.run_path(str(script), run_name="__main__")
        return
    settings, root = settings_from_plan(plan)
    if artifacts := os.environ.get("RPMTEST_RPMS"):
        settings["artifacts"] = [str(Path(artifacts) / "*.rpm")]
    directory = Path(os.environ["TMT_PLAN_DATA"]) / "rpmtest" / f"test-{uuid.uuid4().hex}"
    try:
        prepare(
            settings,
            directory,
            root,
            publish=False,
            cache=Path(os.environ["TMT_PLAN_DATA"]) / "rpmtest-cache",
        )
        os.environ["RPMTEST_DIRECTORY"] = str(directory)
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
