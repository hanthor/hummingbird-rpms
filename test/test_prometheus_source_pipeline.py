"""Regression tests for Prometheus module-only source vendoring."""

import tomllib
from pathlib import Path

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT_DIR / "rpms" / "prometheus3.13"


def _pre_commands() -> list[list[str]]:
    config = tomllib.loads((PACKAGE_DIR / "go-vendor-tools.toml").read_text())
    return config["archive"]["pre_commands"]


def test_module_only_vendoring_has_no_workspace_pre_commands() -> None:
    pipeline = yaml.safe_load(
        (ROOT_DIR / "metadata" / "prometheus3.13.source-pipeline.yaml").read_text()
    )
    vendor_steps = [step for step in pipeline["fetch"] if step["type"] == "vendor"]
    assert len(vendor_steps) == 1
    assert vendor_steps[0]["modules"] == [{"path": ".", "use_workspace": False}]
    # Gorget sets GOWORK=off for every pre-command in module-only mode.
    assert not any(command[:2] == ["go", "work"] for command in _pre_commands())


@pytest.mark.parametrize(
    "prefix",
    [
        ["go", "get"],
        ["go", "-C", "documentation/examples/remote_storage", "get"],
    ],
)
def test_security_bump_is_retained_in_both_modules(prefix: list[str]) -> None:
    commands = [command for command in _pre_commands() if command[:len(prefix)] == prefix]
    assert len(commands) == 1
    assert "github.com/go-openapi/swag@v0.27.1" in commands[0][len(prefix):]
    assert "github.com/go-openapi/swag/pools@v0.27.1" in commands[0][len(prefix):]
