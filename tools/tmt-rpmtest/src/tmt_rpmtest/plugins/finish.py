"""Remove runtime resources after tmt finishes the tests."""

import json
from pathlib import Path

import tmt.steps
from tmt.container import container
from tmt.steps.finish import FinishPlugin, FinishStepData

from tmt_rpmtest.drivers.podman import Podman


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
        state = Path(self.step.plan.data_directory) / "rpmtest/session.json"
        if state.exists():
            Podman(owner=json.loads(state.read_text())["owner"]).cleanup()
        return outcome
