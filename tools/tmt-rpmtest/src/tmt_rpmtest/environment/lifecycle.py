"""Assemble guest-local roots and retain ownership for cleanup after preparation failures."""

import json
import math
import os
import shlex
import sys
import uuid
from pathlib import Path

from tmt_rpmtest.drivers.podman import Podman
from tmt_rpmtest.environment.rpm import DEFAULT_BUILDER, RPMBuilder, digest


def owner_file(directory: Path) -> Path:
    return directory.parent / f".{directory.name}.owner"


def finish(directory: Path):
    state = owner_file(directory)
    if state.exists():
        Podman(owner=state.read_text().strip()).cleanup()


def prepare(settings: dict, directory: Path, root: Path):
    limits = {
        key: settings.get(key, default)
        for key, default in {"cpus": 2.0, "memory": 1 << 30, "pids": 512}.items()
    }
    if not all(math.isfinite(value) and value > 0 for value in limits.values()):
        raise ValueError("Subject resource limits must be positive and finite.")
    for path, expected in settings.get("expected_artifacts", {}).items():
        if digest(Path(path)) != expected:
            raise ValueError(f"An RPM artifact changed during guest transfer: {path}")
    if directory.exists():
        raise FileExistsError(f"RPM environment already exists: {directory}")
    owner = uuid.uuid4().hex
    directory.parent.mkdir(parents=True, exist_ok=True)
    owner_file(directory).write_text(owner + "\n")
    runtime = Podman(owner=owner)
    try:
        RPMBuilder(runtime, settings.get("builder", DEFAULT_BUILDER)).prepare(
            settings.get("packages", []),
            settings.get("repositories", []),
            settings.get("artifacts", []),
            root,
            directory,
        )
        configuration = {"owner": owner, "limits": limits, "python": sys.executable}
        (directory / "session.json").write_text(json.dumps(configuration) + "\n")
        variables = {"RPMTEST_PYTHON": sys.executable, "RPMTEST_DIRECTORY": str(directory)}
        if os.environ.get("PYTHONPATH"):
            variables["PYTHONPATH"] = os.environ["PYTHONPATH"]
        with Path(os.environ["TMT_PLAN_ENVIRONMENT_FILE"]).open("a") as output:
            for name, value in variables.items():
                output.write(f"{name}={shlex.quote(value)}\n")
    except BaseException:
        runtime.cleanup()
        raise
