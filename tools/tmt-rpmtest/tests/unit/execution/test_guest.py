"""Guest routing and artifact identity at the controller-to-guest seam."""

from types import SimpleNamespace

import pytest
from tmt.utils.environment import Environment

from tmt_rpmtest.environment.lifecycle import prepare
from tmt_rpmtest.plugins.guest import execute, stage


def test_guest_transfer_detects_changed_artifact_before_assembly(tmp_path):
    artifact = tmp_path / "candidate.rpm"
    artifact.write_bytes(b"candidate bytes")
    transfers = []
    guest = SimpleNamespace(push=lambda **kwargs: transfers.append(kwargs))
    settings = {}
    work = tmp_path / "guest"
    stage(guest, work, [str(artifact)], tmp_path, settings, python="/guest/python")
    assert (work / "python").read_text() == "/guest/python"
    assert transfers[0]["destination"] == work
    incoming = next((work / "incoming").iterdir())
    incoming.write_bytes(b"replacement bytes")
    with pytest.raises(ValueError, match="guest transfer"):
        prepare(settings, work / "root", tmp_path)
    assert not (work / "root").exists()


def test_guest_execution_uses_guest_environment_and_python(tmp_path):
    calls = []
    guest = SimpleNamespace(
        environment=Environment({"GUEST_VALUE": "guest"}),
        plan_environment=Environment({"PLAN_VALUE": "plan"}),
        parent=SimpleNamespace(plan=SimpleNamespace(environment=Environment())),
        plan_environment_path=tmp_path / "plan.env",
        execute=lambda command, **kwargs: calls.append((str(command), kwargs)),
    )
    execute(guest, tmp_path, "/guest/python", "prepare", settings={"packages": ["hello"]})
    command, arguments = calls[0]
    assert "/guest/python -m tmt_rpmtest.environment prepare" in command
    assert arguments["environment"]["GUEST_VALUE"] == "guest"
    assert arguments["environment"]["PLAN_VALUE"] == "plan"
    assert arguments["environment"]["PYTHONPATH"] == str(tmp_path / "payload")
