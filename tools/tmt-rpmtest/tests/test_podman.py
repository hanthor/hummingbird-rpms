import json
import subprocess
import sys
import threading

import pytest

from tmt_rpmtest.podman import Podman

LIMITS = {"cpus": 1, "memory": 512 << 20, "pids": 256}


def test_interruption_stops_process_but_allows_cleanup():
    cancelled = threading.Event()
    runtime = Podman(executable=sys.executable, cancelled=cancelled)
    timer = threading.Timer(0.05, cancelled.set)
    timer.start()
    try:
        with pytest.raises(InterruptedError):
            runtime._command("-c", "import time; time.sleep(30)", timeout=2)
        assert runtime._command("-c", "print('clean')", interruptible=False).strip() == "clean"
    finally:
        timer.cancel()
        timer.join()


@pytest.mark.parametrize("failure", ["create", "timeout", "cancel", "cleanup", None])
def test_session_cleanup_and_guest_exit_125(tmp_path, monkeypatch, failure):
    removed = []

    class Runtime(Podman):
        def _command(self, *args, **kwargs):
            if args[0] == "create" and failure == "create":
                raise subprocess.CalledProcessError(125, args, stderr="cannot create")
            if args[0] == "inspect":
                return json.dumps([{"State": {"Status": "exited", "ExitCode": 125}}])
            if args[0] in ("rm", "rmi"):
                assert kwargs["interruptible"] is False
                removed.append(args)
                if failure == "cleanup":
                    raise subprocess.CalledProcessError(1, args, stderr="cannot remove")
            return "a" * 64

    def attach(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], 1)
        if failure == "cancel":
            raise InterruptedError("cancelled")
        return subprocess.CompletedProcess(args[0], 125)

    monkeypatch.setattr(Runtime, "_process", attach)

    def run():
        with Runtime().session(tmp_path / "root.tar", LIMITS) as session:
            result = session.run(
                ["/probe", "literal;argument"], timeout=1, environment={}, directory=tmp_path
            )
            assert result.returncode == 125

    expected = {
        "create": subprocess.CalledProcessError,
        "timeout": subprocess.TimeoutExpired,
        "cancel": InterruptedError,
        "cleanup": ExceptionGroup,
    }
    if failure:
        with pytest.raises(expected[failure]):
            run()
    else:
        run()
    assert [args[0] for args in removed] == ["rm", "rmi"]
