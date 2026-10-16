"""Remove runtime resources after tmt finishes the tests."""

from pathlib import Path

import tmt.steps
from tmt.container import container
from tmt.steps.finish import FinishPlugin, FinishStepData

from tmt_rpmtest.plugins.guest import execute


@container
class RPMFinishData(FinishStepData):
    @property
    def is_bare(self):
        # Cleanup has work to do even without additional configuration.
        return False


@tmt.steps.provides_method("rpmtest")
class FinishRPM(FinishPlugin[RPMFinishData]):
    """Remove resources left by interrupted host-side tests."""

    _data_class = RPMFinishData

    def go(self, *, guest, environment=None, logger):
        outcome = super().go(guest=guest, environment=environment, logger=logger)
        if self.is_dry_run:
            return outcome
        work = Path(self.step.plan.data_directory) / f"rpmtest-{guest.safe_name}"
        if (work / "python").exists():
            execute(guest, work, (work / "python").read_text(), "finish")
        return outcome
