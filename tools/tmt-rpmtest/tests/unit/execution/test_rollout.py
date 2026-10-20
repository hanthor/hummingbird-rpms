"""CI adoption and explicit disablement do not change legacy execution."""

import pytest

from tmt_rpmtest.execution import rollout


@pytest.mark.parametrize(
    "component,enabled",
    [
        ("curl-main", True),
        ("hello-main", True),
        ("nats-server2-12-main", True),
        ("openssl-main", False),
    ],
)
def test_only_components_with_fmf_tests_are_enforced(component, enabled, monkeypatch):
    monkeypatch.delenv("RPMTEST_ROLLOUT", raising=False)
    assert rollout.decision(component)["enabled"] is enabled


@pytest.mark.parametrize("mode,enabled", [("off", False), ("adopted", False)])
def test_explicit_rollout_modes(mode, enabled, monkeypatch):
    monkeypatch.setenv("RPMTEST_ROLLOUT", mode)
    assert rollout.decision("future-main")["enabled"] is enabled


def test_invalid_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("RPMTEST_ROLLOUT", "unexpected")
    with pytest.raises(ValueError, match="Invalid rpmtest rollout mode"):
        rollout.decision("curl-main")


def test_disabled_functional_tests_never_prepare_or_execute(tmp_path, monkeypatch):
    from tmt_rpmtest.execution import test as execution

    monkeypatch.setenv("IMAGE_NAME", "unadopted-main")
    monkeypatch.delenv("RPMTEST_ROLLOUT", raising=False)
    monkeypatch.delenv("TMT_TEST_PIDFILE", raising=False)
    execution.execute(tmp_path / "absent-plan", tmp_path / "absent-script")


@pytest.mark.parametrize("enabled", [True, False])
def test_new_suite_controls_adoption_without_a_policy_edit(tmp_path, monkeypatch, enabled):
    monkeypatch.delenv("RPMTEST_ROLLOUT", raising=False)
    (tmp_path / ".fmf").mkdir()
    (tmp_path / ".fmf/version").write_text("1\n")
    (tmp_path / "new.fmf").write_text(
        "component: future-main\ntest: ./test.sh\ntag: functional\n"
        f"enabled: {str(enabled).lower()}\n"
    )
    assert rollout.decision("future-main", tmp_path)["enabled"] is enabled


def test_contract_only_suite_is_adopted_with_missing_functional_coverage(tmp_path, monkeypatch):
    monkeypatch.delenv("RPMTEST_ROLLOUT", raising=False)
    (tmp_path / ".fmf").mkdir()
    (tmp_path / ".fmf/version").write_text("1\n")
    (tmp_path / "new.fmf").write_text("component: future-main\ntest: ./test.sh\ntag: contract\n")
    assert rollout.decision("future-main", tmp_path)["enabled"]
    assert rollout.functional_coverage(tmp_path, "future-main")["result"] == "missing"


def test_missing_functional_coverage_blocks_component_before_preparation(tmp_path, monkeypatch):
    import json
    import sys

    from tmt_rpmtest.execution import universal

    monkeypatch.delenv("RPMTEST_ROLLOUT", raising=False)
    (tmp_path / ".fmf").mkdir()
    (tmp_path / ".fmf/version").write_text("1\n")
    (tmp_path / "new.fmf").write_text("component: future-main\ntest: ./test.sh\ntag: contract\n")
    result = tmp_path / "results"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "universal",
            "--artifacts",
            str(tmp_path / "absent"),
            "--directory",
            str(result),
            "--tests",
            str(tmp_path),
            "--component",
            "future-main",
        ],
    )
    with pytest.raises(universal.ContractFailure, match="No functional scenarios"):
        universal.main()
    assert json.loads((result / "coverage.json").read_text())["result"] == "missing"
    assert json.loads((result / "rollout.json").read_text())["enabled"]
