"""Prepare RPM subjects. Use tmt's standard executor for host-side tests."""

from pathlib import Path

import tmt.steps
import tmt.utils
from tmt.container import container, field
from tmt.steps.prepare import PreparePlugin, PrepareStepData

from tmt_rpmtest.environment.rpm import DEFAULT_BUILDER
from tmt_rpmtest.plugins.guest import execute, stage


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
    python: str = field(default="python3", option="--python", help="Python 3.11+ on the guest.")
    cpus: float = field(default=2.0, option="--cpus", help="CPU limit per subject.")
    memory: int = field(default=1 << 30, option="--memory", help="Memory limit in bytes.")
    pids: int = field(default=512, option="--pids", help="Process limit per subject.")


@tmt.steps.provides_method("rpmtest")
class PrepareRPM(PreparePlugin[RPMData]):
    """Build a minimal RPM root for ordinary tmt tests on the provisioned guest."""

    _data_class = RPMData

    def go(self, *, guest, environment=None, logger):
        outcome = super().go(guest=guest, environment=environment, logger=logger)
        if self.is_dry_run:
            return outcome
        work = Path(self.step.plan.data_directory) / f"rpmtest-{guest.safe_name}"
        settings = {
            key: getattr(self.data, key)
            for key in ("packages", "repositories", "builder", "cpus", "memory", "pids")
        }
        stage(
            guest,
            work,
            self.data.artifacts,
            Path(self.step.plan.node.root),
            settings,
            python=self.data.python,
        )
        execute(guest, work, self.data.python, "prepare", settings=settings)
        return outcome
