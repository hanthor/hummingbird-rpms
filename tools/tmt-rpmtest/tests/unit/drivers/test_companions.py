"""Companion scopes own networking, partial startup, and cleanup."""

import json
import subprocess

import pytest

from tmt_rpmtest import Companion, PrimitiveContext
from tmt_rpmtest.drivers.podman import Podman
from tmt_rpmtest.execution.subject import Subject

IMAGE = "registry.example.test/web@sha256:" + "a" * 64
LIMITS = {"cpus": 1, "memory": 512 << 20, "pids": 256}


@pytest.mark.parametrize(
    "failure", [None, "network", "pull", "create", "ready", "died", "assertion", "commit", "logs"]
)
@pytest.mark.parametrize("internet", [False, True])
def test_companion_scope_lifecycle_and_partial_failures(tmp_path, failure, internet):
    calls, containers, probes = [], {}, {}
    pulls = 0

    class Runtime(Podman):
        def _command(self, *args, **kwargs):
            nonlocal pulls
            calls.append(args)
            if args[:2] == ("network", "create") and failure == "network":
                raise subprocess.CalledProcessError(125, args)
            if args[0] == "pull":
                pulls += 1
                if failure == "pull" and pulls == 2:
                    raise subprocess.CalledProcessError(125, args)
            if args[0] == "create":
                name = args[args.index("--name") + 1]
                if failure == "create" and "tmt-rpmtest-db-" in name:
                    raise subprocess.CalledProcessError(125, args)
                containers[name] = "created"
            if args[0] == "start":
                containers[args[-1]] = "running"
            if args[0] == "inspect":
                return json.dumps([{"State": {"Status": containers[args[-1]], "ExitCode": 0}}])
            if args[0] == "stop":
                assert kwargs["interruptible"] is False
                containers[args[-1]] = "exited"
            if args[0] == "commit" and failure == "commit":
                raise subprocess.CalledProcessError(125, args)
            return "image"

        def _process(self, argv, **kwargs):
            calls.append(tuple(argv[1:]))
            if argv[1:3] == ["network", "exists"]:
                return subprocess.CompletedProcess(argv, 0)
            if argv[1] == "logs":
                kwargs["stdout"].write(b"container logs")
                return subprocess.CompletedProcess(argv, 1 if failure == "logs" else 0)
            if argv[1] == "exec":
                # Dependent companions must all start before any readiness probe.
                assert len(containers) == 2
                assert all(state == "running" for state in containers.values())
                name = argv[-2]
                probes[name] = probes.get(name, 0) + 1
                status = int(probes[name] == 1 or failure == "ready" and "-db-" in name)
                return subprocess.CompletedProcess(argv, status)
            assert argv[1:3] == ["start", "--attach"]
            containers[argv[-1]] = "exited"
            kwargs["stdout"].write(b"response")
            return subprocess.CompletedProcess(argv, 0)

    def run():
        with Runtime().session(tmp_path / "root.tar", LIMITS) as session:
            with PrimitiveContext(session, tmp_path, "unused") as context:
                target = Subject(context)
                config = Companion(IMAGE, command=("/server",), ready=("/ready",), user="daemon")
                with target.companions(
                    {"web": config, "db": config}, timeout=0.25, internet=internet
                ):
                    assert target.run(["/client"]).stdout == "response"
                    with pytest.raises(RuntimeError, match="nested"):
                        with target.companions({"other": config}):
                            pass
                    if failure == "assertion":
                        raise AssertionError("failed test")
                    if failure == "died":
                        name = next(name for name in containers if "-db-" in name)
                        containers[name] = "exited"
                assert session.network == "none"
                assert target.run(["/offline"]).returncode == 0

    expected = {
        "network": subprocess.CalledProcessError,
        "pull": subprocess.CalledProcessError,
        "create": subprocess.CalledProcessError,
        "ready": subprocess.TimeoutExpired,
        "died": RuntimeError,
        "assertion": AssertionError,
        "commit": subprocess.CalledProcessError,
        "logs": subprocess.CalledProcessError,
    }
    if failure:
        with pytest.raises(expected[failure]):
            run()
    else:
        run()
    networks = [call for call in calls if call[:2] == ("network", "create")]
    assert len(networks) == 1
    assert ("--internal" in networks[0]) is (not internet)
    assert any(call[:2] == ("network", "rm") for call in calls)
    created = [call for call in calls if call[0] == "create"]
    assert all("--network" in call for call in created)
    for name in containers:
        assert any(call[0] == "rm" and name in call for call in calls)
    if failure is None:
        record = json.loads(next(tmp_path.glob("*-companions/network.json")).read_text())
        assert record["internet"] is internet
        assert record["internal"] is (not internet)
        assert any(
            call[call.index("--network") + 1] != "none" and "subject" in call for call in created
        )
        assert created[-1][created[-1].index("--network") + 1] == "none"
        assert len(list(tmp_path.glob("*-companions/*/state.json"))) == 2
        assert len(list(tmp_path.glob("*-companions/*/stdout.txt"))) == 2


@pytest.mark.parametrize(
    "services,timeout",
    [
        ({}, 1),
        ({"subject": Companion(IMAGE)}, 1),
        ({"bad/name": Companion(IMAGE)}, 1),
        ({"web": Companion("image:latest")}, 1),
        ({"web": Companion(IMAGE)}, float("inf")),
        ({"web": Companion(IMAGE)}, 0),
    ],
)
def test_invalid_configuration_does_not_allocate_resources(tmp_path, services, timeout):
    class Runtime(Podman):
        def _command(self, *args, **kwargs):
            raise AssertionError("No runtime calls expected")

    from tmt_rpmtest.drivers.podman import Session

    with PrimitiveContext(Session(Runtime(), LIMITS), tmp_path, "unused") as context:
        with pytest.raises(ValueError):
            with context.companions(services, timeout=timeout):
                pass


@pytest.mark.parametrize("leftovers", [False, True])
def test_finish_recovers_only_owned_resources_including_networks(leftovers):
    calls = []

    class Runtime(Podman):
        def _command(self, *args, **kwargs):
            assert kwargs["interruptible"] is False
            calls.append(args)
            if "--filter" in args:
                assert args[args.index("--filter") + 1] == "label=io.rpmtest.owner=test-owner"
                return "owned-resource" if leftovers else ""
            assert args[-1] == "owned-resource"
            return ""

    Runtime(owner="test-owner").cleanup()
    if leftovers:
        removed = [args for args in calls if "--filter" not in args]
        assert [args[0] for args in removed] == ["rm", "rmi", "network"]
        assert removed[-1][:2] == ("network", "rm")
    else:
        assert all("--filter" in args for args in calls)


def test_companion_commands_record_evidence_and_expire(tmp_path):
    from contextlib import contextmanager

    class Process:
        directory = tmp_path / "client"

        def run(self, argv, *, timeout, environment, directory):
            assert argv == ["client", "request"]
            assert timeout == 3 and environment == {"TOKEN": "test"}
            (directory / "stdout.txt").write_text("reply")
            return subprocess.CompletedProcess(argv, 0, "reply", "")

    class Session:
        @contextmanager
        def companions(self, services, *, directory, timeout, internet=False):
            assert internet is False
            Process.directory.mkdir()
            yield {"client": Process()}

    with PrimitiveContext(Session(), tmp_path, "unused") as context:
        with Subject(context).companions({"client": Companion(IMAGE)}) as peers:
            client = peers["client"]
            assert (
                client.run(["client", "request"], timeout=3, environment={"TOKEN": "test"}).stdout
                == "reply"
            )
        with pytest.raises(RuntimeError, match="closed"):
            client.run(["client", "request"])
    record = json.loads((Process.directory / "001-command/execution.json").read_text())
    assert record == {"argv": ["client", "request"], "exit_code": 0}


@pytest.mark.parametrize("internet", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_subject_network_scope_restores_offline_execution(tmp_path, internet, failure):
    from tmt_rpmtest.drivers.podman import Session

    calls = []

    class Runtime(Podman):
        def _command(self, *args, **kwargs):
            calls.append(args)
            return "image"

        def _process(self, argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0)

    session = Session(Runtime(), LIMITS)
    with PrimitiveContext(session, tmp_path, "unused") as context:
        target = Subject(context)

        def online():
            with target.network(internet=internet):
                assert session.network != "none"
                session._create(["/usr/bin/curl", "https://github.com/"])
                with pytest.raises(RuntimeError, match="nested"):
                    with target.companions({"web": Companion(IMAGE)}):
                        pass
                if failure:
                    raise AssertionError("failed request")

        if failure:
            with pytest.raises(AssertionError, match="failed request"):
                online()
        else:
            online()
        assert session.network == "none"
        session._create(["/offline"])
        session.close()

    network = next(call for call in calls if call[:2] == ("network", "create"))
    assert ("--internal" in network) is (not internet)
    creates = [call for call in calls if call[0] == "create"]
    assert creates[0][creates[0].index("--network") + 1] != "none"
    assert creates[-1][creates[-1].index("--network") + 1] == "none"
    assert any(call[:2] == ("network", "rm") for call in calls)


@pytest.mark.parametrize("internet", ["false", "true", 1, None])
def test_network_opt_in_requires_boolean(tmp_path, internet):
    from tmt_rpmtest.drivers.podman import Session

    class Runtime(Podman):
        def _command(self, *args, **kwargs):
            raise AssertionError("Invalid opt-in must not allocate resources")

    with PrimitiveContext(Session(Runtime(), LIMITS), tmp_path, "unused") as context:
        with pytest.raises(ValueError, match="True or False"):
            with Subject(context).network(internet=internet):
                pass
