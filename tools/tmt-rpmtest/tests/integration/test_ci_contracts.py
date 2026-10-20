"""Run the shared CI entry point against a built binary/source pair on a local guest."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tmt.utils import to_yaml, yaml_to_dict, yaml_to_list

from tmt_rpmtest.drivers import LABEL
from tmt_rpmtest.drivers.podman import Podman
from tmt_rpmtest.environment.rpm import DEFAULT_BUILDER

REPO = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.skipif(os.environ.get("RPMTEST_LIVE") != "1", reason="Set RPMTEST_LIVE=1.")


@pytest.fixture(scope="module")
def fixture_artifacts(tmp_path_factory):
    work = tmp_path_factory.mktemp("ci-rpm-fixture")
    (work / "SOURCES").mkdir()
    (work / "SOURCES/hello").write_text("""#!/bin/sh
case "${1:-}" in
  --version) printf 'hello 1.0\\n' ;;
  --greeting=*) printf '%s\\n' "${1#--greeting=}" ;;
  *) printf 'Hello, world!\\n' ;;
esac
""")
    (work / "hello.spec").write_text("""Name: hello
Version: 1.0
Release: 1.hum1
Summary: Fixture for the complete RPM test plan
License: MIT
BuildArch: noarch
Source0: hello
Requires: filesystem
%description
A deterministic test fixture.
%install
install -D -m 0755 %{SOURCE0} %{buildroot}/usr/bin/hello
%files
/usr/bin/hello
""")
    runtime = Podman()
    try:
        runtime.pull_image(DEFAULT_BUILDER)
        shutil.copytree(REPO / "ci/repos", work / "repos")
        runtime.build(
            DEFAULT_BUILDER,
            work,
            "dnf5 --setopt=reposdir=/work/repos --setopt=install_weak_deps=False "
            "--no-gpgchecks -y install rpm-build >&2; "
            "rpmbuild -ba --define '_topdir /work' /work/hello.spec >&2",
            internet=True,
        )
    finally:
        runtime.cleanup()
    artifacts = work / "artifacts"
    artifacts.mkdir()
    for path in [*work.glob("RPMS/*/*.rpm"), *work.glob("SRPMS/*.rpm")]:
        shutil.copyfile(path, artifacts / path.name)
    assert len(list(artifacts.iterdir())) == 2
    return artifacts


@pytest.mark.parametrize(
    "case", ["healthy", "missing-candidate", "broken-functional", "timeout", "disabled"]
)
def test_ci_plan_includes_artifacts_contracts_and_native_tests(tmp_path, fixture_artifacts, case):
    root = tmp_path / "metadata"
    (root / ".fmf").mkdir(parents=True)
    (root / ".fmf/version").write_text("1\n")
    (root / "rpms/hello").mkdir(parents=True)
    shutil.copytree(REPO / "test/rpmtest", root / "test/rpmtest")
    shutil.copytree(REPO / "ci", root / "ci")
    shutil.copytree(
        REPO / "tools/tmt-rpmtest",
        root / "tools/tmt-rpmtest",
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
    )
    if case == "broken-functional":
        (root / "test/rpmtest/hello/tests/greeting/hello_greeting.py").write_text(
            "from tmt_rpmtest import subject\n"
            "with subject() as target:\n"
            "    result = target.run(['/usr/bin/hello'])\n"
            "    assert result.stdout == 'deliberately incorrect greeting\\n', result.stdout\n"
        )
    elif case == "timeout":
        greeting = root / "test/rpmtest/hello/tests/greeting/main.fmf"
        greeting.write_text(greeting.read_text().replace("duration: 30m", "duration: 1m"))
        (greeting.parent / "hello_greeting.py").write_text(
            "from tmt_rpmtest import subject\n"
            "with subject() as target:\n"
            "    target.run(['/bin/sh', '-c', 'while :; do :; done'], timeout=180)\n"
        )
    metadata = yaml_to_dict((root / "ci/run_tests_rpm.fmf").read_text())
    # Guest package installation is not allowed with local provisioning. Run the actual artifact
    # preparation and Python bootstrap, using the already installed host interpreter.
    metadata["prepare"] = [
        phase
        for phase in metadata["prepare"]
        if phase["how"] != "install" and "git submodule" not in phase.get("script", "")
    ]
    for phase in metadata["prepare"]:
        phase["script"] = phase["script"].replace("python3.11", shlex.quote(sys.executable))
    metadata["environment"] = {
        "RPMTEST_LOCAL_RPMS": str(fixture_artifacts),
        "RPMTEST_BOOTSTRAP_PYTHON": sys.executable,
        "RPMTEST_ROLLOUT": "off" if case == "disabled" else "adopted",
    }
    if case == "missing-candidate":
        metadata["prepare"].append({"how": "shell", "script": 'rm "$RPMTEST_RPMS"/*.rpm'})
    (root / "ci/run_tests_rpm.fmf").write_text(to_yaml(metadata))
    run = tmp_path / "run"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tmt",
            "--root",
            str(root),
            "--allow-unsafe-behavior=provision/local",
            "run",
            "--all",
            "--keep",
            "--id",
            str(run),
            "--environment",
            "IMAGE_NAME=hello-main",
            "plans",
            "--name",
            "^/ci/run_tests_rpm$",
            "provision",
            "--how",
            "local",
        ],
        capture_output=True,
        text=True,
        timeout=1200,
        env={
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "HOME",
                "PATH",
                "LANG",
                "USER",
                "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS",
                "CONTAINERS_CONF_OVERRIDE",
            }
        },
    )
    results = yaml_to_list((run / "ci/run_tests_rpm/execute/results.yaml").read_text())
    outcomes = {item["name"].rsplit("/", 1)[-1]: item["result"] for item in results}
    assert set(outcomes) == {"RPM checks", "Installed package contracts", "contract", "greeting"}
    if case == "missing-candidate":
        assert result.returncode != 0
        assert outcomes["Installed package contracts"] == "fail"
    elif case in {"broken-functional", "timeout"}:
        assert result.returncode != 0
        assert outcomes["RPM checks"] == "pass"
        assert outcomes["Installed package contracts"] == "pass"
        assert outcomes["contract"] == "pass"
        assert outcomes["greeting"] == ("error" if case == "timeout" else "fail")
    elif case == "disabled":
        assert result.returncode == 0, result.stdout + result.stderr
        assert outcomes["RPM checks"] == "pass"
        assert all(value == "skip" for name, value in outcomes.items() if name != "RPM checks")
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert set(outcomes.values()) == {"pass"}
        data = run / "ci/run_tests_rpm/data"
        profiles = list((data / "rpmtest/universal").glob("profile-*/manifest.json"))
        assert len(profiles) == 1
        # The universal request has Fedora fallbacks; the functional requests use Hummingbird only.
        # They share repository metadata and RPM downloads even across different repository sets.
        roots = [json.loads(path.read_text()) for path in profiles]
        roots += [
            json.loads(path.read_text()) for path in (data / "rpmtest").glob("test-*/manifest.json")
        ]
        assert len({manifest["resolution"]["key"] for manifest in roots}) == 1
        assert any(manifest["resolution"]["reused_rpms"] > 0 for manifest in roots)
    # All owners recorded by this plan must have been reclaimed, including failing requests.
    for owner_file in (run / "ci/run_tests_rpm/data/rpmtest").rglob(".*.owner"):
        remaining = subprocess.run(
            [
                "podman",
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label={LABEL}={owner_file.read_text().strip()}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert not remaining.stdout
        for command in (["podman", "images", "--quiet"], ["podman", "network", "ls", "--quiet"]):
            remaining = subprocess.run(
                [*command, "--filter", f"label={LABEL}={owner_file.read_text().strip()}"],
                check=True,
                capture_output=True,
                text=True,
            )
            assert not remaining.stdout
