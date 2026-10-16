"""Set RPMTEST_LIVE=1 to test published RPMs and services with local Podman."""

import json
import os
import subprocess

import pytest

from tmt_rpmtest import Companion, subject
from tmt_rpmtest.drivers.podman import Podman
from tmt_rpmtest.environment.lifecycle import finish, prepare

pytestmark = pytest.mark.skipif(os.environ.get("RPMTEST_LIVE") != "1", reason="Set RPMTEST_LIVE=1.")


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    work = tmp_path_factory.mktemp("rpm-features")
    directory = work / "root"
    with pytest.MonkeyPatch.context() as variables:
        variables.setenv("TMT_PLAN_ENVIRONMENT_FILE", str(work / "plan.env"))
        try:
            prepare(
                {
                    "packages": ["curl", "python3"],
                    "repositories": [
                        "https://packages.redhat.com/api/pulp-content/public-hummingbird/$basearch/"
                    ],
                },
                directory,
                work,
            )
            yield directory
        finally:
            finish(directory)


@pytest.fixture
def evidence(tmp_path, monkeypatch, root):
    monkeypatch.setenv("TMT_TEST_DATA", str(tmp_path))
    yield tmp_path
    owner = json.loads((root / "session.json").read_text())["owner"]
    assert not Podman()._command(
        "ps", "--all", "--quiet", "--filter", f"label=io.rpmtest.owner={owner}"
    )
    assert not Podman()._command("images", "--quiet", "--filter", f"label=io.rpmtest.owner={owner}")
    assert not Podman()._command(
        "network", "ls", "--quiet", "--filter", f"label=io.rpmtest.owner={owner}"
    )


APACHE = (
    "docker.io/library/httpd@sha256:"
    "1b766f17b84026429b7cb243317b142921b24432336e798bc881c43f45ed9567"
)


@pytest.mark.skipif(
    os.environ.get("RPMTEST_INTERNET_LIVE") != "1",
    reason="Set RPMTEST_INTERNET_LIVE=1 to contact public HTTPS services.",
)
def test_explicit_internet_scopes_and_offline_restoration(tmp_path, monkeypatch):
    root = tmp_path / "root"
    evidence = tmp_path / "evidence"
    monkeypatch.setenv("TMT_TEST_DATA", str(evidence))
    request = [
        "/usr/bin/curl",
        "--fail",
        "--silent",
        "--show-error",
        "--connect-timeout",
        "5",
        "--max-time",
        "15",
        "--output",
        "/dev/null",
        "--write-out",
        "%{ssl_verify_result}",
        "https://github.com/",
    ]
    try:
        prepare(
            {
                "packages": ["curl", "ca-certificates"],
                "repositories": [
                    "https://packages.redhat.com/api/pulp-content/public-hummingbird/$basearch/"
                ],
            },
            root,
            tmp_path,
            publish=False,
        )
        with subject(root) as target:
            assert target.run(request).returncode != 0
            with target.network(internet=True):
                response = target.run(request)
                assert response.returncode == 0, response.stderr
                assert response.stdout == "0"
            with target.companions({"web": Companion(APACHE)}, internet=True):
                response = target.run(request)
                assert response.returncode == 0, response.stderr
                assert response.stdout == "0"
                response = target.run(
                    [
                        "/usr/bin/curl",
                        "--fail",
                        "--retry",
                        "5",
                        "--retry-connrefused",
                        "http://web/",
                    ]
                )
                assert response.returncode == 0 and "It works!" in response.stdout, response
            assert target.run(request).returncode != 0
        for path in evidence.glob("rpmtest-*/*/network.json"):
            record = json.loads(path.read_text())
            assert record["internet"] is True and record["internal"] is False
            assert (
                Podman()
                ._process(["podman", "network", "exists", record["name"]], timeout=30)
                .returncode
                == 1
            )
    finally:
        finish(root)


def test_companion_apache_dns_persistent_subject_and_network_cleanup(root, evidence):
    with subject(root) as target:
        with target.companions({"web": Companion(APACHE, ready=("httpd", "-t"))}):
            request = [
                "/usr/bin/curl",
                "--retry",
                "5",
                "--retry-connrefused",
                "--fail",
                "http://web/",
            ]
            response = target.run(request)
            assert response.returncode == 0 and "It works!" in response.stdout, response
            network = json.loads(
                next(evidence.glob("rpmtest-*/*-companions/network.json")).read_text()
            )["name"]
            configuration = json.loads(Podman()._command("network", "inspect", network))[0]
            assert configuration["internal"] and configuration["dns_enabled"]
            with target.service(
                ["/usr/bin/python3", "-c", "import time; time.sleep(60)"],
                ready=["/usr/bin/python3", "-c", "pass"],
            ):
                assert "It works!" in target.run(request).stdout
                with pytest.raises(RuntimeError, match="before starting"):
                    with target.companions({"nested": Companion(APACHE)}):
                        pass
            target.run(
                ["/usr/bin/python3", "-c", "open('/tmp/network-state', 'w').write('saved')"]
            ).check_returncode()
        assert (
            target.run(
                ["/usr/bin/python3", "-c", "print(open('/tmp/network-state').read())"]
            ).stdout
            == "saved\n"
        )
        owner = json.loads((root / "session.json").read_text())["owner"]
        containers = (
            Podman()
            ._command("ps", "--all", "--quiet", "--filter", f"label=io.rpmtest.owner={owner}")
            .split()
        )
        assert len(containers) == 1
        assert (
            json.loads(Podman()._command("inspect", containers[0]))[0]["HostConfig"]["NetworkMode"]
            == "none"
        )
    logs = next(evidence.glob("rpmtest-*/*-companions/web/stdout.txt")).read_text()
    assert "GET / HTTP/1.1" in logs


@pytest.mark.parametrize("failure", ["start", "ready"])
def test_companion_partial_startup_removes_containers_and_network(root, evidence, failure):
    broken = (
        Companion(APACHE, command=("/missing-command",))
        if failure == "start"
        else Companion(APACHE, ready=("/bin/sh", "-c", "exit 1"))
    )
    expected = subprocess.CalledProcessError if failure == "start" else subprocess.TimeoutExpired
    with subject(root) as target:
        with pytest.raises(expected):
            with target.companions({"web": Companion(APACHE), "broken": broken}, timeout=0.5):
                raise AssertionError("A failed companion must prevent test execution.")
    assert next(evidence.glob("rpmtest-*/*-companions/web/state.json")).exists()


def test_rpm_contents_links_and_broken_runtime_dependency(root, evidence):
    with subject(root) as target:
        contents = target.packages.contents("curl", match=("/usr/bin/*",))
        assert any(item.path == "/usr/bin/curl" and item.info.isfile() for item in contents)
        links = target.packages.links("libcurl")
        assert links and all(item.resolved and item.error is None for item in links)
        checks = target.packages.runtime_links("curl")
        assert checks and all(item.resolved for item in checks)
        target.run(
            [
                "/usr/bin/python3",
                "-c",
                "import os; os.chmod('/usr/lib64', 0o755); os.unlink('/usr/lib64/libcurl.so.4')",
            ]
        ).check_returncode()
        broken = target.packages.runtime_links("curl")
        assert broken and any(not item.resolved for item in broken)
        assert any("libcurl.so.4" in item.result.stderr + item.result.stdout for item in broken)


def test_service_http_commands_state_and_post_shutdown_files(root, evidence, tmp_path):
    payload = tmp_path / "payload.txt"
    payload.write_text("service payload\n")
    url = "http://127.0.0.1:8000/payload.txt"
    with subject(root) as target:
        target.copy(payload, "/workspace/payload.txt")
        with target.service(
            [
                "/usr/bin/python3",
                "-m",
                "http.server",
                "8000",
                "--bind",
                "127.0.0.1",
                "--directory",
                "/workspace",
            ],
            ready=["/usr/bin/curl", "--fail", "--silent", url],
            timeout=20,
            user="65534:65534",
        ):
            assert (
                target.run(["/usr/bin/python3", "-c", "import os; print(os.getuid())"]).stdout
                == "65534\n"
            )
            for _ in range(2):
                result = target.run(["/usr/bin/curl", "--fail", "--silent", url])
                assert result.returncode == 0 and result.stdout == "service payload\n"
            target.run(
                ["/usr/bin/python3", "-c", "open('/tmp/state', 'w').write('saved')"]
            ).check_returncode()
            assert target.stat("/tmp/state").size == 5
            # Fixture staging and RPM inspection also operate on the running session.
            target.copy(payload, "/workspace/second.txt")
            assert "curl" in target.packages.installed()
        assert (
            target.run(
                [
                    "/usr/bin/python3",
                    "-c",
                    "import os; assert os.getuid() == 0; print(open('/tmp/state').read())",
                ]
            ).stdout
            == "saved\n"
        )
    logs = list(evidence.glob("rpmtest-*/*-service/stderr.txt"))
    assert len(logs) == 1 and "GET /payload.txt" in logs[0].read_text()
    with subject(root) as fresh:
        assert (
            fresh.run(
                ["/usr/bin/python3", "-c", "import os; assert not os.path.exists('/tmp/state')"]
            ).returncode
            == 0
        )


@pytest.mark.parametrize("failure", ["exit", "ready", "exec", "assertion"])
def test_service_failures_leave_logs_and_remove_containers(root, evidence, failure):
    with subject(root) as target:
        errors = {
            "exit": RuntimeError,
            "ready": subprocess.TimeoutExpired,
            "exec": subprocess.TimeoutExpired,
            "assertion": AssertionError,
        }
        with pytest.raises(errors[failure]):
            with target.service(
                [
                    "/usr/bin/python3",
                    "-u",
                    "-c",
                    "print('starting', flush=True); "
                    + (
                        "raise SystemExit(3)"
                        if failure == "exit"
                        else "import time; time.sleep(60)"
                    ),
                ],
                ready=[
                    "/usr/bin/python3",
                    "-c",
                    "raise SystemExit(1)" if failure == "ready" else "pass",
                ],
                timeout=1 if failure == "ready" else 10,
            ):
                if failure == "exec":
                    target.run(
                        ["/usr/bin/python3", "-c", "import time; time.sleep(30)"], timeout=0.2
                    )
                if failure == "assertion":
                    raise AssertionError("test failed")
    logs = list(evidence.glob("rpmtest-*/*-service/stdout.txt"))
    assert len(logs) == 1 and "starting" in logs[0].read_text()
