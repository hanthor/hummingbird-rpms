"""Prepare RPM subjects. Use tmt's standard executor for host-side tests."""

import json
import math
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

import tmt.steps
import tmt.utils
from tmt.container import container, field
from tmt.steps.prepare import PreparePlugin, PrepareStepData
from tmt.steps.provision.local import GuestLocal
from tmt.utils.signals import INTERRUPT_PENDING

from tmt_rpmtest.drivers.podman import Podman
from tmt_rpmtest.environment.rpm import DEFAULT_BUILDER, RPMBuilder


@container
class RPMData(PrepareStepData):
    packages: list[str] = field(
        default_factory=list,
        option=("--package", "packages"),
        multiple=True,
        normalize=tmt.utils.normalize_string_list,
        help="Packages to install in the minimal root.",
    )
    repositories: list[str] = field(
        default_factory=list,
        option=("--repository", "repositories"),
        multiple=True,
        normalize=tmt.utils.normalize_string_list,
        help="HTTPS repository base URLs.",
    )
    artifacts: list[str] = field(
        default_factory=list,
        option=("--artifact", "artifacts"),
        multiple=True,
        normalize=tmt.utils.normalize_string_list,
        help="Local candidate RPM paths or glob patterns.",
    )
    builder: str = field(
        default=DEFAULT_BUILDER, option="--builder", help="Digest-pinned RPM builder image."
    )
    cpus: float = field(default=2.0, option="--cpus", help="CPU limit per subject.")
    memory: int = field(default=1 << 30, option="--memory", help="Memory limit in bytes.")
    pids: int = field(default=512, option="--pids", help="Process limit per subject.")


@tmt.steps.provides_method("rpmtest")
class PrepareRPM(PreparePlugin[RPMData]):
    """Build a minimal RPM root for ordinary tmt tests on the local runner."""

    _data_class = RPMData

    def go(self, *, guest, environment=None, logger):
        outcome = super().go(guest=guest, environment=environment, logger=logger)
        if not isinstance(guest, GuestLocal):
            raise tmt.utils.PrepareError("RPM preparation requires provision how: local.")
        if self.is_dry_run:
            return outcome
        limits = {"cpus": self.data.cpus, "memory": self.data.memory, "pids": self.data.pids}
        if not all(math.isfinite(value) and value > 0 for value in limits.values()):
            raise tmt.utils.PrepareError("Subject resource limits must be positive and finite.")
        directory = Path(self.step.plan.data_directory) / "rpmtest"
        owner = uuid.uuid4().hex
        runtime = Podman(cancelled=INTERRUPT_PENDING, owner=owner)
        try:
            RPMBuilder(runtime, self.data.builder).prepare(
                self.data.packages,
                self.data.repositories,
                self.data.artifacts,
                Path(self.step.plan.node.root),
                directory,
            )
            configuration = {"owner": owner, "limits": limits, "python": sys.executable}
            (directory / "session.json").write_text(json.dumps(configuration) + "\n")
            # Tests use the same Python environment in which tmt loaded this plugin.
            environment_file = guest.plan_environment_path
            if environment_file is None:
                raise tmt.utils.PrepareError("The local guest has no plan environment file.")
            with environment_file.open("a") as output:
                output.write(f"RPMTEST_PYTHON={shlex.quote(sys.executable)}\n")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            runtime.cleanup()
            raise tmt.utils.PrepareError(f"RPM preparation failed: {error}") from error
        return outcome
