"""The central baseline rejects broken roots before functional assertions run."""

import json
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tmt_rpmtest.execution import contract
from tmt_rpmtest.primitives.packages import RuntimeLink

IDENTITY = {
    "name": "hello",
    "nevra": "hello-1-1.hum1.x86_64",
    "header_sha256": "a" * 64,
    "payload_sha256": "b" * 64,
}


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"packages": ["hello"], "installed": [IDENTITY]})
    )
    inventory = "\t".join(IDENTITY.values()) + "\n"
    target = SimpleNamespace(
        rpm=lambda *args: subprocess.CompletedProcess(args, 0, inventory, ""),
        packages=SimpleNamespace(
            verify=lambda package: subprocess.CompletedProcess(["rpm", "-V", package], 0, "", ""),
            runtime_links=lambda package: (),
        ),
    )

    @contextmanager
    def subject(directory):
        assert directory == tmp_path
        yield target

    monkeypatch.setattr(contract, "subject", subject)
    return tmp_path, target


def test_baseline_reports_static_package_without_claiming_dynamic_coverage(root):
    directory, _ = root
    contract.check_contract(directory)
    results = json.loads((directory / "contract.json").read_text())
    assert [(item["check"], item["result"]) for item in results] == [
        ("U05", "pass"),
        ("U06", "pass"),
        ("U07", "not-applicable"),
    ]


def test_replaced_inventory_fails_even_if_files_verify(root):
    directory, target = root
    target.rpm = lambda *args: subprocess.CompletedProcess(
        args, 0, "\t".join({**IDENTITY, "payload_sha256": "c" * 64}.values()), ""
    )
    with pytest.raises(contract.ContractFailure, match="U05"):
        contract.check_contract(directory)


def test_content_and_linkage_failures_are_both_retained(root):
    directory, target = root
    target.packages.verify = lambda package: subprocess.CompletedProcess(
        ["rpm"], 1, "missing /usr/bin/hello", ""
    )
    target.packages.runtime_links = lambda package: (
        RuntimeLink(
            "/usr/bin/hello",
            "/loader",
            subprocess.CompletedProcess(["/loader"], 127, "", "Missing dynamic loader"),
        ),
    )
    with pytest.raises(contract.ContractFailure):
        contract.check_contract(directory)
    results = json.loads((directory / "contract.json").read_text())
    assert [item["check"] for item in results if item["result"] == "fail"] == ["U06", "U07"]


def test_inspection_error_retains_evidence_and_propagates(root):
    directory, target = root

    def unsupported(package):
        raise ValueError("Unsupported loader")

    target.packages.runtime_links = unsupported
    with pytest.raises(ValueError, match="Unsupported loader"):
        contract.check_contract(directory)
    results = json.loads((directory / "contract.json").read_text())
    assert results[-1]["result"] == "error"
    assert results[-1]["check"] == "U07"


def test_broken_baseline_prevents_functional_assertions_and_cleans_up(tmp_path, monkeypatch):
    from tmt_rpmtest.execution import test as execution

    monkeypatch.delenv("RPMTEST_DIRECTORY", raising=False)
    monkeypatch.setenv("TMT_PLAN_DATA", str(tmp_path))
    monkeypatch.setattr(execution, "settings_from_plan", lambda plan: ({}, tmp_path))
    calls = []
    monkeypatch.setattr(execution, "prepare", lambda *args, **kwargs: None)
    monkeypatch.setattr(execution, "finish", lambda directory: calls.append("cleanup"))
    monkeypatch.setattr(execution.runpy, "run_path", lambda *args, **kwargs: calls.append("script"))

    def broken(directory):
        raise contract.ContractFailure("broken package")

    monkeypatch.setattr(execution, "check_contract", broken)
    with pytest.raises(contract.ContractFailure):
        execution.execute(tmp_path / "plan.fmf", tmp_path / "test.py")
    assert calls == ["cleanup"]
