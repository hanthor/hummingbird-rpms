"""Run with RPMTEST_CURL_RPM set to a local curl candidate RPM."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

CANDIDATE = os.environ.get("RPMTEST_CURL_RPM")
SSH_PORT = os.environ.get("RPMTEST_SSH_PORT")
SSH_KEY = os.environ.get("RPMTEST_SSH_KEY")

pytestmark = pytest.mark.skipif(not CANDIDATE, reason="Set RPMTEST_CURL_RPM for the live RPM test.")


@pytest.mark.parametrize("remote", [False, True], ids=["local", "ssh"])
def test_tmt_subject_fixtures_state_and_exact_candidate(tmp_path, monkeypatch, remote):
    if remote and not (SSH_PORT and SSH_KEY):
        pytest.skip("Set RPMTEST_SSH_PORT and RPMTEST_SSH_KEY for a localhost SSH guest.")
    if remote:
        from tmt.guest import GuestSsh

        # The localhost SSH fixture must not install tmt helpers into the host system.
        monkeypatch.setattr(GuestSsh, "install_scripts", lambda self, scripts: None)
    provision = (
        {
            "how": "connect",
            "guest": "127.0.0.1",
            "user": os.environ.get("USER", "admin"),
            "port": int(SSH_PORT),
            "key": [SSH_KEY],
        }
        if remote
        else {"how": "local"}
    )
    # Keep ambient credentials out of subprocess environments and tmt evidence.
    keep = {
        "HOME",
        "PATH",
        "LANG",
        "USER",
        "LOGNAME",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
    }
    for name in set(os.environ) - keep:
        monkeypatch.delenv(name)
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
                "provision": provision,
                "discover": {"how": "fmf"},
                "prepare": {
                    "how": "rpmtest",
                    "python": sys.executable,
                    "packages": ["curl", "bash", "coreutils"],
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
    (root / "test.py").write_text("""import subprocess
from pathlib import Path
from tmt_rpmtest import subject
from tmt_rpmtest.primitives import Files

class Binaries:
    def __init__(self, context):
        self.files = Files(context)

    def executable(self, path):
        info = self.files.stat(path)
        return info.isfile() and bool(info.mode & 0o111)

with subject() as target:
    binaries = target.use(Binaries)
    assert "curl" in target.packages.installed()
    assert target.packages.owners("/usr/bin/curl") == {"curl"}
    assert "curl" in target.packages.metadata("curl", "provides")
    assert target.packages.metadata("curl", "requires")
    assert "/usr/bin/curl" in target.packages.metadata("curl", "files")
    assert target.packages.metadata("curl", "nevra")[0].startswith("curl-")
    contents = target.packages.contents("curl", match=("/usr/bin/*",))
    assert any(item.path == "/usr/bin/curl" and item.info.isfile() for item in contents)
    links = target.packages.links("libcurl")
    assert links and all(item.resolved for item in links)
    dependencies = target.packages.runtime_links("curl")
    assert dependencies and all(item.resolved for item in dependencies)
    try:
        target.packages.owners("/missing")
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("An unowned path must raise a query error.")
    target.copy(Path("payload.txt"), "/workspace/fixtures/payload.txt")
    copied = target.run(["/usr/bin/curl", "--fail", "--silent", "file:///workspace/fixtures/payload.txt"])
    assert copied.returncode == 0 and copied.stdout == "exact fixture bytes\\n", copied
    assert target.run(["/bin/sh", "-c", "printf saved >/tmp/state"]).returncode == 0
    assert target.run(["/usr/bin/cat", "/tmp/state"]).stdout == "saved"
    assert target.run(["/usr/bin/curl", "--fail", "--silent", "file:///missing"]).returncode == 37
    assert target.packages.verify("curl").returncode == 0
    assert target.rpm("-qf", "--qf", "%{NAME}", "/usr/bin/curl").stdout == "curl"
    assert binaries.executable("/usr/bin/curl")
    assert target.run(["/bin/sh", "-c", "printf broken >/usr/bin/curl"]).returncode == 0
    assert target.packages.verify("curl").returncode != 0
try:
    binaries.executable("/usr/bin/curl")
except RuntimeError as error:
    assert "closed" in str(error)
else:
    raise AssertionError("A primitive must not operate after its subject closes.")
with subject() as fresh:
    assert fresh.run(["/usr/bin/cat", "/tmp/state"]).returncode != 0
    with fresh.service(
        ["/usr/bin/sleep", "30"], ready=["/bin/sh", "-c", "kill -0 1"], timeout=10
    ):
        assert fresh.run(["/bin/sh", "-c", "kill -0 1"]).returncode == 0
        assert fresh.run(["/usr/bin/curl", "--version"]).returncode == 0
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
    manifest = json.loads((run / "plan/data/rpmtest-default-0/root/manifest.json").read_text())
    with Path(CANDIDATE).open("rb") as candidate:
        sha = hashlib.file_digest(candidate, "sha256").hexdigest()
    assert any(item["sha256"] == sha and item["selected"] for item in manifest["candidates"])
    owner = json.loads((run / "plan/data/rpmtest-default-0/root/session.json").read_text())["owner"]
    for command in (["ps", "--all"], ["images"]):
        owned = subprocess.check_output(
            ["podman", *command, "--quiet", "--filter", f"label=io.rpmtest.owner={owner}"],
            text=True,
        )
        assert not owned.strip()
