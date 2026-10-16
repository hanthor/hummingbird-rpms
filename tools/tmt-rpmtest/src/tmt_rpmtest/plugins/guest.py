"""Transfer plugin code and exact candidates, then execute through tmt's guest interface."""

import glob
import json
import shutil
from pathlib import Path

from tmt.utils import Command, ShellScript
from tmt.utils import Path as TmtPath
from tmt.utils.environment import Environment, EnvVarValue

from tmt_rpmtest.environment.rpm import digest


def stage(guest, work: Path, artifacts: list[str], root: Path, settings: dict, *, python="python3"):
    work.mkdir(parents=True)
    payload = work / "payload" / "tmt_rpmtest"
    shutil.copytree(
        Path(__file__).resolve().parents[1],
        payload,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    incoming = work / "incoming"
    incoming.mkdir()
    expected = {}
    for pattern in artifacts:
        matches = sorted(glob.glob(str(root / pattern)))
        if not matches:
            raise ValueError(f"No RPM artifacts match {pattern!r}.")
        for source in map(Path, matches):
            sha = digest(source)
            destination = incoming / f"{sha}.rpm"
            shutil.copyfile(source, destination)
            expected[str(destination)] = sha
    settings["artifacts"] = list(expected)
    settings["expected_artifacts"] = expected
    (work / "python").write_text(python)
    guest.push(source=TmtPath(work), destination=TmtPath(work))


def execute(
    guest, work: Path, python: str, action: str, *, settings=None, cache: Path | None = None
):
    environment = Environment()
    environment.update(guest.environment, guest.plan_environment, guest.parent.plan.environment)
    paths = [str(work / "payload"), *environment.get("PYTHONPATH", "").split(":")]
    environment["PYTHONPATH"] = EnvVarValue(":".join(dict.fromkeys(path for path in paths if path)))
    if guest.plan_environment_path:
        environment["TMT_PLAN_ENVIRONMENT_FILE"] = EnvVarValue(str(guest.plan_environment_path))
    command = [python, "-m", "tmt_rpmtest.environment", action, "--directory", str(work / "root")]
    if settings is not None:
        command += ["--settings", json.dumps(settings)]
    if cache is not None:
        command += ["--cache", str(cache)]
    guest.execute(
        ShellScript("export PATH") + Command(*command).to_script(), environment=environment
    )
