"""Podman transport for RPM builders and persistent subject filesystems."""

import json
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from tmt_rpmtest.drivers import LABEL
from tmt_rpmtest.drivers.companions import companions
from tmt_rpmtest.drivers.persistent import PersistentSession, validate_command


class Podman:
    def __init__(self, executable="podman", cancelled: threading.Event | None = None, owner=None):
        self.executable = executable
        self.cancelled = cancelled
        self.owner = owner or uuid.uuid4().hex

    def _process(self, argv, *, timeout, interruptible=True, **streams):
        deadline = time.monotonic() + timeout
        with subprocess.Popen(argv, **streams) as process:
            try:
                while True:
                    if interruptible and self.cancelled and self.cancelled.is_set():
                        raise InterruptedError("RPM testing was interrupted.")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    try:
                        stdout, stderr = process.communicate(timeout=min(remaining, 0.25))
                        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
                    except subprocess.TimeoutExpired:
                        continue
            except BaseException:
                process.kill()
                try:
                    process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                raise

    def _command(self, *args: str, timeout: float = 60, interruptible: bool = True) -> str:
        completed = self._process(
            [self.executable, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            interruptible=interruptible,
        )
        completed.check_returncode()
        return completed.stdout

    def pull_image(self, image: str) -> None:
        self._command("pull", "--quiet", "--policy=missing", image, timeout=300)

    def build(
        self, image: str, work: Path, script: str, *args: str, internet=False, check=True
    ) -> subprocess.CompletedProcess[str]:
        name = f"tmt-rpmtest-builder-{uuid.uuid4().hex}"
        command = [
            self.executable,
            "run",
            "--name",
            name,
            "--pull=never",
            "--user=0",
            "--label",
            f"{LABEL}={self.owner}",
            "--privileged",
            "--pids-limit=2048",
            "--memory=4g",
            "--cpus=2",
            "--volume",
            f"{work}:/work:Z",
            "--entrypoint=/bin/sh",
        ]
        if not internet:
            command.append("--network=none")
        try:
            result = self._process(
                [*command, image, "-c", script, "rpmtest", *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600,
            )
            if check and result.returncode:
                (work / "build-error.txt").write_text(result.stderr)
                raise RuntimeError(f"RPM builder failed: {result.stderr[-2000:]}")
            return result
        finally:
            self._command("rm", "--force", "--ignore", name, timeout=30, interruptible=False)

    @contextmanager
    def session(self, archive: Path, limits: dict):
        session = Session(self, limits)
        try:
            tag = session.image_tag()
            session.image = self._command(
                "import", "--change", f"LABEL {LABEL}={self.owner}", str(archive), tag
            ).strip()
            yield session
        finally:
            session.close()

    def cleanup(self):
        containers = self._command(
            "ps", "--all", "--quiet", "--filter", f"label={LABEL}={self.owner}", interruptible=False
        ).split()
        if containers:
            self._command("rm", "--force", "--ignore", *containers, interruptible=False)
        images = self._command(
            "images", "--quiet", "--filter", f"label={LABEL}={self.owner}", interruptible=False
        ).split()
        if images:
            self._command("rmi", "--force", "--ignore", *dict.fromkeys(images), interruptible=False)
        networks = self._command(
            "network",
            "ls",
            "--quiet",
            "--filter",
            f"label={LABEL}={self.owner}",
            interruptible=False,
        ).split()
        if networks:
            self._command("network", "rm", "--force", "--time=0", *networks, interruptible=False)


class Session:
    """Preserve filesystem changes between commands. Do not preserve processes."""

    def __init__(self, runtime: Podman, limits: dict):
        self.runtime = runtime
        self.limits = limits
        self.image = ""
        self.container = None
        self.images = []
        self.network = "none"

    def image_tag(self):
        tag = f"localhost/tmt-rpmtest-{uuid.uuid4().hex}"
        self.images.append(tag)
        return tag

    def _checkpoint(self):
        if self.container:
            self.image = self.runtime._command("commit", self.container, self.image_tag()).strip()
            self.runtime._command("rm", "--force", "--ignore", self.container)
            self.container = None

    def _create(self, argv, environment=None, user="0"):
        self._checkpoint()
        self.container = f"tmt-rpmtest-{uuid.uuid4().hex}"
        options = [
            "create",
            "--name",
            self.container,
            "--pull=never",
            "--user",
            user,
            "--network",
            self.network,
            "--label",
            f"{LABEL}={self.runtime.owner}",
            "--image-volume=ignore",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--unsetenv-all",
            "--env",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "--pids-limit",
            str(self.limits["pids"]),
            "--memory",
            str(self.limits["memory"]),
            "--cpus",
            str(self.limits["cpus"]),
            "--entrypoint",
            argv[0],
        ]
        for key, value in (environment or {}).items():
            options += ["--env", f"{key}={value}"]
        if self.network != "none":
            options += ["--network-alias", "subject"]
        self.runtime._command(*options, self.image, *argv[1:])

    def copy(self, archive: Path):
        if not self.container:
            self._create(["/__rpmtest_inspection_only"])
        with archive.open("rb") as source:
            result = self.runtime._process(
                [self.runtime.executable, "cp", "-", f"{self.container}:/"],
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
        result.check_returncode()

    def snapshot(self, destination: Path):
        if not self.container:
            self._create(["/__rpmtest_inspection_only"])
        self.runtime._command("export", "--output", str(destination), self.container)

    def run(self, argv, *, timeout, environment, directory):
        self.validate_command(argv, timeout)
        self._create(argv, environment)
        args = [self.runtime.executable, "start", "--attach", self.container]
        with (
            (directory / "stdout.txt").open("wb") as stdout,
            (directory / "stderr.txt").open("wb") as stderr,
        ):
            self.runtime._process(args, stdout=stdout, stderr=stderr, timeout=timeout)
        state = json.loads(self.runtime._command("inspect", self.container))[0]["State"]
        if state["Status"] != "exited" or state.get("Error") or state.get("OOMKilled"):
            raise RuntimeError(f"Subject command did not finish normally: {state}")
        return subprocess.CompletedProcess(
            argv,
            int(state["ExitCode"]),
            (directory / "stdout.txt").read_text(errors="replace"),
            (directory / "stderr.txt").read_text(errors="replace"),
        )

    validate_command = staticmethod(validate_command)

    @contextmanager
    def companions(self, services, *, directory, timeout, internet=False):
        if self.network != "none":
            raise RuntimeError("Network scopes cannot be nested.")
        with companions(
            self.runtime, services, self.limits, directory, timeout, internet=internet
        ) as (
            network,
            processes,
        ):
            self.network = network
            try:
                yield processes
            finally:
                self.network = "none"
                try:
                    self._checkpoint()
                finally:
                    if self.container:
                        self.runtime._command(
                            "rm", "--force", "--ignore", self.container, interruptible=False
                        )
                        self.container = None

    @contextmanager
    def service(self, argv, *, environment, directory, user="0"):
        self.validate_command(argv, 60)
        self._create(argv, environment, user)
        running = PersistentSession(self, directory)
        try:
            self.runtime._command("start", self.container)
            running.require_running()
            yield running
            running.require_running()
        finally:
            running.close()

    def close(self):
        errors = []
        commands = [["rm", "--force", "--ignore", self.container]] if self.container else []
        commands += [["rmi", "--ignore", tag] for tag in reversed(self.images)]
        for args in commands:
            try:
                self.runtime._command(*args, timeout=30, interruptible=False)
            except (OSError, subprocess.SubprocessError) as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("Subject cleanup failed.", errors)
