"""Fresh subject lifecycle and a facade for ordinary Python test scripts."""

import json
import os
import signal
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from tmt_rpmtest.context import PrimitiveContext
from tmt_rpmtest.podman import Podman
from tmt_rpmtest.primitives import Files, Packages
from tmt_rpmtest.rpm import digest

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
def subject(directory: Path | None = None):
    """Open a fresh root from TMT_PLAN_DATA. Keep command evidence in TMT_TEST_DATA."""
    directory = directory or Path(os.environ["TMT_PLAN_DATA"]) / "rpmtest"
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
