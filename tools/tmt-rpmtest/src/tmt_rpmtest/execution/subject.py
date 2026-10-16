"""Fresh subject lifecycle and a facade for ordinary Python test scripts."""

import json
import os
import signal
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from tmt_rpmtest.drivers.podman import Podman
from tmt_rpmtest.environment.rpm import digest
from tmt_rpmtest.execution.context import PrimitiveContext
from tmt_rpmtest.primitives import Files, Packages

T = TypeVar("T")


class Subject:
    def __init__(self, context: PrimitiveContext):
        self._context = context
        self.files = self.use(Files)
        self.packages = self.use(Packages)

    def use(self, primitive: Callable[[PrimitiveContext], T]) -> T:
        """Construct a primitive with this session's context. No registration is required."""
        return primitive(self._context)

    def run(self, argv: list[str], *, timeout=60, environment=None):
        return self._context.run(argv, timeout=timeout, environment=environment)

    def copy(self, source: Path, destination: str):
        return self.files.copy(source, destination)

    def stat(self, path: str):
        return self.files.stat(path)

    def rpm(self, *args: str):
        return self.packages.query(*args)

    @contextmanager
    def companions(self, services, *, timeout=30):
        """Connect the subject to scoped companions on a private internal network."""
        with self._context.companions(services, timeout=timeout) as companions:
            yield companions

    @contextmanager
    def service(self, argv: list[str], *, ready: list[str], timeout=30, environment=None, user="0"):
        """Run a foreground service and execute subject commands alongside it."""
        with self._context.service(
            argv, ready=ready, timeout=timeout, environment=environment, user=user
        ):
            yield self


@contextmanager
def subject(directory: Path | None = None):
    """Open a fresh root from TMT_PLAN_DATA. Keep command evidence in TMT_TEST_DATA."""
    if directory is None:
        directory = (
            Path(os.environ["RPMTEST_DIRECTORY"])
            if "RPMTEST_DIRECTORY" in os.environ
            else Path(os.environ["TMT_PLAN_DATA"]) / "rpmtest"
        )
    manifest = json.loads((directory / "manifest.json").read_text())
    if digest(directory / "rootfs.tar") != manifest["rootfs_sha256"]:
        raise ValueError("The prepared root archive changed before execution.")
    configuration = json.loads((directory / "session.json").read_text())
    evidence = Path(os.environ.get("TMT_TEST_DATA", tempfile.gettempdir()))
    evidence.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="rpmtest-", dir=evidence))
    (work / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    runtime = Podman(owner=configuration["owner"])
    previous_handler = signal.getsignal(signal.SIGTERM)

    def interrupt(signum, frame):
        raise KeyboardInterrupt("The tmt test was interrupted.")

    signal.signal(signal.SIGTERM, interrupt)
    try:
        with runtime.session(directory / "rootfs.tar", configuration["limits"]) as session:
            with PrimitiveContext(session, work, manifest["builder"]) as context:
                yield Subject(context)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
