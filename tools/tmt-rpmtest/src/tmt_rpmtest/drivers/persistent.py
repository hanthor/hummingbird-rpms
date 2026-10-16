"""Container process execution, readiness, and shutdown."""

import json
import math
import subprocess

from tmt_rpmtest.execution.readiness import wait_ready


def validate_command(argv, timeout):
    if not argv or not argv[0] or any("\0" in arg for arg in argv):
        raise ValueError("Specify an executable and valid arguments.")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("The command duration must be positive and finite.")


class ContainerProcess:
    def __init__(self, runtime, container, directory):
        self.runtime = runtime
        self.container = container
        self.directory = directory

    def require_running(self):
        state = json.loads(self.runtime._command("inspect", self.container))[0]["State"]
        if state["Status"] != "running" or state.get("Error") or state.get("OOMKilled"):
            raise RuntimeError(f"Container process is not running: {state}")

    def wait_ready(self, argv, timeout):
        attempt = 0

        def probe(argv, *, timeout):
            nonlocal attempt
            attempt += 1
            evidence = self.directory / f"ready-{attempt:03}"
            evidence.mkdir()
            result = self.run(argv, timeout=timeout, environment={}, directory=evidence)
            (evidence / "execution.json").write_text(
                json.dumps({"argv": argv, "exit_code": result.returncode}) + "\n"
            )
            return result

        wait_ready(probe, argv, timeout)

    def run(self, argv, *, timeout, environment, directory):
        validate_command(argv, timeout)
        self.require_running()
        options = [self.runtime.executable, "exec"]
        for key, value in environment.items():
            options += ["--env", f"{key}={value}"]
        with (
            (directory / "stdout.txt").open("wb") as stdout,
            (directory / "stderr.txt").open("wb") as stderr,
        ):
            try:
                result = self.runtime._process(
                    [*options, self.container, *argv],
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout,
                )
            except (subprocess.TimeoutExpired, InterruptedError):
                # Killing the client alone leaves the exec process inside the container.
                self.runtime._command("stop", "--time=0", self.container, interruptible=False)
                raise
        self.require_running()
        return subprocess.CompletedProcess(
            argv,
            result.returncode,
            (directory / "stdout.txt").read_text(errors="replace"),
            (directory / "stderr.txt").read_text(errors="replace"),
        )

    def close(self):
        try:
            state = self.runtime._command("inspect", self.container, interruptible=False)
            (self.directory / "state.json").write_text(state)
        finally:
            try:
                self.runtime._command(
                    "stop",
                    "--ignore",
                    "--time=5",
                    self.container,
                    timeout=15,
                    interruptible=False,
                )
            finally:
                with (
                    (self.directory / "stdout.txt").open("wb") as stdout,
                    (self.directory / "stderr.txt").open("wb") as stderr,
                ):
                    result = self.runtime._process(
                        [self.runtime.executable, "logs", self.container],
                        stdout=stdout,
                        stderr=stderr,
                        timeout=15,
                        interruptible=False,
                    )
                    result.check_returncode()


class PersistentSession(ContainerProcess):
    def __init__(self, session, directory):
        super().__init__(session.runtime, session.container, directory)
        self.session = session

    def copy(self, archive):
        self.require_running()
        self.session.copy(archive)

    def snapshot(self, destination):
        self.require_running()
        self.session.snapshot(destination)
