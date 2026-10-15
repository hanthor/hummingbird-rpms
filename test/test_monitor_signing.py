"""Tests for the read-only cross-cluster signing monitor."""

import json
from pathlib import Path
import types

import pytest


@pytest.fixture
def monitor_module():
    script = Path(__file__).parent.parent / "ci" / "monitor_signing.py"
    module = types.ModuleType("monitor_signing")
    exec(compile(script.read_text(), str(script), "exec"), module.__dict__)
    return module


def test_raw_metrics_query_does_not_append_output_format(monitor_module, monkeypatch):
    calls = []

    def fake_oc(context, *args):
        calls.append((context, args))
        return '{"containers": []}', None

    monkeypatch.setattr(monitor_module, "oc", fake_oc)
    result, error = monitor_module.get_json("signing", "get", "--raw", "/apis/metrics.k8s.io/v1beta1/pods")
    assert error is None
    assert result == {"containers": []}
    assert calls == [("signing", ("get", "--raw", "/apis/metrics.k8s.io/v1beta1/pods"))]


def test_capture_uses_managed_uid_to_find_direct_run(monitor_module, monkeypatch, tmp_path):
    calls = []
    release = {
        "metadata": {"name": "release-abc"},
        "status": {"managedProcessing": {"pipelineRun": "rhtap-releng-tenant/managed-abc"}},
    }
    managed = {"metadata": {"uid": "managed-uid"}}
    direct = {"metadata": {"name": "internalrequest-abc"}, "status": {"conditions": []}}

    def fake_get_json(context, *args):
        calls.append((context, args))
        if args[1] == "release":
            return release, None
        if args[1] == "pipelinerun":
            return managed, None
        if args[1] == "pipelineruns":
            return {"items": [direct]}, None
        return {"items": []}, None

    monkeypatch.setattr(monitor_module, "get_json", fake_get_json)
    args = types.SimpleNamespace(
        rh03_context="rh03", signing_context="signing", release="release-abc",
        output=tmp_path, once=True,
    )
    monitor_module.capture(args)

    selector = "internal-services.appstudio.openshift.io/pipelinerun-uid=managed-uid"
    assert (
        "signing",
        ("get", "pipelineruns", "-n", "internal-services", "-l",
         f"{selector},tekton.dev/pipeline=direct-rpm-signing"),
    ) in calls
    assert (
        "rh03",
        ("get", "internalrequests", "-n", "rhtap-releng-tenant", "-l", selector),
    ) in calls
    saved = json.loads((tmp_path / "internalrequest-abc" / "pipelinerun.json").read_text())
    assert saved["metadata"]["name"] == "internalrequest-abc"
