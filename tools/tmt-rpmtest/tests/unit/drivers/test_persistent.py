"""Persistent execution preserves one process and cleans up partial startup."""

import json
import subprocess

import pytest

from tmt_rpmtest import PrimitiveContext
from tmt_rpmtest.drivers.podman import Podman
from tmt_rpmtest.execution.subject import Subject

LIMITS = {"cpus": 1, "memory": 512 << 20, "pids": 256}


@pytest.mark.parametrize(
    "failure", [None, "start", "exit", "ready", "exec", "dies", "assertion", "stop"]
)
def test_service_readiness_commands_logs_and_cleanup(tmp_path, failure):
    calls = []
    state = "created"
    probes = 0

    class Runtime(Podman):
        def _command(self, *args, **kwargs):
            nonlocal state
            calls.append(args)
            if args[0] == "start":
                state = "running"
                if failure == "start":
                    raise subprocess.CalledProcessError(125, args)
                if failure == "exit":
                    state = "exited"
            if args[0] == "inspect":
                return json.dumps([{"State": {"Status": state}}])
            if args[0] == "stop":
                assert kwargs["interruptible"] is False
                state = "exited"
                if failure == "stop":
                    raise subprocess.CalledProcessError(125, args)
            return "a" * 64

        def _process(self, argv, **kwargs):
            nonlocal probes, state
            calls.append(tuple(argv[1:]))
            if argv[1] == "logs":
                kwargs["stderr"].write(b"service logs\n")
                return subprocess.CompletedProcess(argv, 0)
            assert argv[1] == "exec"
            if argv[-1] == "/ready":
                probes += 1
                if failure == "ready":
                    return subprocess.CompletedProcess(argv, 1)
                return subprocess.CompletedProcess(argv, int(probes < 2))
            if failure == "exec":
                raise subprocess.TimeoutExpired(argv, 0.1)
            if failure == "dies":
                state = "exited"
            kwargs["stdout"].write(b"result")
            return subprocess.CompletedProcess(argv, 37)

    def execute():
        with Runtime().session(tmp_path / "root.tar", LIMITS) as session:
            with PrimitiveContext(session, tmp_path, "unused") as context:
                target = Subject(context)
                with target.service(["/server"], ready=["/ready"], timeout=0.3, user="daemon"):
                    result = target.run(["/client"], environment={"LITERAL": "a; b"})
                    assert result.returncode == 37 and result.stdout == "result"
                    with pytest.raises(RuntimeError, match="nested"):
                        with target.service(["/other"], ready=["/ready"]):
                            pass
                    if failure == "assertion":
                        raise AssertionError("test failed")
                assert context._session is session

    errors = {
        "start": subprocess.CalledProcessError,
        "exit": RuntimeError,
        "ready": subprocess.TimeoutExpired,
        "exec": subprocess.TimeoutExpired,
        "dies": RuntimeError,
        "assertion": AssertionError,
        "stop": subprocess.CalledProcessError,
    }
    if failure:
        with pytest.raises(errors[failure]):
            execute()
    else:
        execute()
    assert sum(call[0] == "create" for call in calls) == 1
    created = next(call for call in calls if call[0] == "create")
    assert created[created.index("--user") + 1] == "daemon"
    assert not any(call[0] == "commit" for call in calls)
    assert any(call[0] == "stop" for call in calls)
    assert any(call[0] == "rm" for call in calls)
    assert any(call[0] == "rmi" for call in calls)
    assert next(tmp_path.glob("*-service/stderr.txt")).read_text() == "service logs\n"
    assert next(tmp_path.glob("*-service/state.json")).exists()
    if failure in (None, "assertion", "stop"):
        assert any(call[:3] == ("exec", "--env", "LITERAL=a; b") for call in calls)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_readiness_timeout_does_not_start_service(tmp_path, timeout):
    with PrimitiveContext(object(), tmp_path, "unused") as context:
        with pytest.raises(ValueError, match="positive"):
            with context.service(["/server"], ready=["/ready"], timeout=timeout):
                pass
    assert not list(tmp_path.iterdir())
