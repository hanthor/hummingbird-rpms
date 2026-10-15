"""Run with RPMTEST_CURL_RPM set to a local curl candidate RPM."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

CANDIDATE = os.environ.get("RPMTEST_CURL_RPM")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="Set RPMTEST_CURL_RPM for the live RPM test.")


def test_tmt_subject_fixtures_state_and_exact_candidate(tmp_path):
    from tmt.__main__ import import_cli_commands
    from tmt.cli._root import main
    from tmt.utils import to_yaml, yaml_to_list

    import_cli_commands()
    root = tmp_path / "metadata"
    (root / ".fmf").mkdir(parents=True)
    (root / ".fmf/version").write_text("1\n")
    (root / "plan.fmf").write_text(
        to_yaml(
            {
                "provision": {"how": "local"},
                "discover": {"how": "fmf"},
                "prepare": {
                    "how": "rpmtest",
                    "packages": ["curl"],
                    "repositories": [
                        "https://packages.redhat.com/api/pulp-content/public-hummingbird/$basearch/"
                    ],
                },
                "execute": {"how": "tmt"},
                "finish": {"how": "rpmtest"},
            }
        )
    )
    (root / "test.fmf").write_text(to_yaml({"test": '"$RPMTEST_PYTHON" test.py', "duration": "3m"}))
    (root / "payload.txt").write_text("exact fixture bytes\n")
    (root / "test.py").write_text("""from pathlib import Path
from tmt_rpmtest import subject
with subject() as target:
    target.copy(Path("payload.txt"), "/workspace/fixtures/payload.txt")
    copied = target.run(["/usr/bin/curl", "--fail", "--silent", "file:///workspace/fixtures/payload.txt"])
    assert copied.returncode == 0 and copied.stdout == "exact fixture bytes\\n", copied
    assert target.run(["/bin/sh", "-c", "printf saved >/tmp/state"]).returncode == 0
    assert target.run(["/usr/bin/cat", "/tmp/state"]).stdout == "saved"
    assert target.run(["/usr/bin/curl", "--fail", "--silent", "file:///missing"]).returncode == 37
    assert target.rpm("-V", "curl").returncode == 0
    assert target.rpm("-qf", "--qf", "%{NAME}", "/usr/bin/curl").stdout == "curl"
    assert target.stat("/usr/bin/curl").mode & 0o111
    assert target.run(["/bin/sh", "-c", "printf broken >/usr/bin/curl"]).returncode == 0
    assert target.rpm("-V", "curl").returncode != 0
with subject() as fresh:
    assert fresh.run(["/usr/bin/cat", "/tmp/state"]).returncode != 0
    import subprocess
    try:
        fresh.run(["/usr/bin/sleep", "30"], timeout=0.1)
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("The command exceeded its timeout.")
""")
    run = tmp_path / "run"
    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(root),
            "--allow-unsafe-behavior=provision/local",
            "run",
            "--all",
            "--keep",
            "--id",
            str(run),
            "prepare",
            "--how",
            "rpmtest",
            "--artifact",
            str(Path(CANDIDATE).resolve()),
        ],
    )
    assert result.exit_code == 0, result.output
    outcomes = yaml_to_list((run / "plan/execute/results.yaml").read_text())
    assert {item["name"]: item["result"] for item in outcomes} == {"/test": "pass"}
    manifest = json.loads((run / "plan/data/rpmtest/manifest.json").read_text())
    with Path(CANDIDATE).open("rb") as candidate:
        sha = hashlib.file_digest(candidate, "sha256").hexdigest()
    assert any(item["sha256"] == sha and item["selected"] for item in manifest["candidates"])
    owned = subprocess.check_output(["podman", "ps", "--all", "--format", "{{.Names}}"], text=True)
    assert not any(name.startswith("tmt-rpmtest-") for name in owned.splitlines())
