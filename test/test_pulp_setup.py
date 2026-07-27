"""Behavior of retention configuration during Pulp repository setup."""

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "ci/pulp-setup/create-pulp-resources.sh"


@pytest.fixture
def pulp_stub(tmp_path):
    """Record setup commands and emulate one Pulp repository."""
    executable = tmp_path / "pulp"
    executable.write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        import json
        import os
        import sys
        from pathlib import Path

        state_path = Path(os.environ["PULP_TEST_STATE"])
        state = json.loads(state_path.read_text())
        args = sys.argv[1:]
        state["commands"].append(args)
        if args[:2] == ["domain", "show"]:
            pass
        elif "repository" in args:
            action = args[args.index("repository") + 1]
            if action == "show":
                if not state["exists"]:
                    state_path.write_text(json.dumps(state))
                    sys.exit(1)
                if "--format" in args:
                    print(json.dumps({"retain_repo_versions": state["limit"]}))
            elif action == "create":
                state["exists"] = True
            elif action == "update":
                if "--retain-repo-versions" in args:
                    state["limit"] = int(args[args.index("--retain-repo-versions") + 1])
        elif "distribution" in args:
            pass
        else:
            raise SystemExit(f"Unexpected Pulp command: {args}")
        state_path.write_text(json.dumps(state))
        '''))
    executable.chmod(0o755)
    state_path = tmp_path / "state.json"
    environment = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
                       PULP_TEST_STATE=str(state_path))

    def run(*args, exists=True, limit=None):
        state_path.write_text(json.dumps({"exists": exists, "limit": limit, "commands": []}))
        result = subprocess.run(
            ["bash", str(SCRIPT), "--domain", "test", "--repos", "source", *args],
            env=environment, capture_output=True, text=True, check=False,
        )
        return result, json.loads(state_path.read_text())

    return run


def updates(state):
    return [args for args in state["commands"] if "repository" in args
            and args[args.index("repository") + 1] == "update"]


def test_existing_rpm_without_flag_keeps_unlimited_retention(pulp_stub):
    result, state = pulp_stub()
    assert result.returncode == 0, result.stderr
    assert state["limit"] is None
    assert updates(state) == []


def test_new_rpm_explicit_limit_sets_autopublish_and_retention(pulp_stub):
    result, state = pulp_stub("--retain-repo-versions", "100", exists=False)
    assert result.returncode == 0, result.stderr
    assert state["limit"] == 100
    assert len(updates(state)) == 1
    assert "--autopublish" in updates(state)[0]
    assert updates(state)[0][-2:] == ["--retain-repo-versions", "100"]


def test_existing_rpm_different_limit_requires_separate_change(pulp_stub):
    result, state = pulp_stub("--retain-repo-versions", "100", limit=3000)
    assert result.returncode != 0
    assert "Change existing RPM retention in bounded steps" in result.stdout
    assert state["limit"] == 3000
    assert updates(state) == []


def test_existing_rpm_same_limit_is_idempotent(pulp_stub):
    result, state = pulp_stub("--retain-repo-versions", "100", limit=100)
    assert result.returncode == 0, result.stderr
    assert updates(state) == []


def test_existing_file_repo_still_defaults_to_one(pulp_stub):
    result, state = pulp_stub("--type", "file", limit=10)
    assert result.returncode == 0, result.stderr
    assert state["limit"] == 1
    assert len(updates(state)) == 1
