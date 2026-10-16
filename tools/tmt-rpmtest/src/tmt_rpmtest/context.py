"""Session operations shared by built-in and test-local primitives."""

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Self

from tmt_rpmtest.podman import Session

_INSPECT = r"""
set -eu
python3 -m venv --without-pip /tmp/rpmtest-inspect
/tmp/rpmtest-inspect/bin/python3 - <<'PY'
import os
import tarfile
with tarfile.open('/work/snapshot.tar') as archive:
    archive.extractall('/inspection', filter='fully_trusted')
    for info in archive:
        for key, value in info.pax_headers.items():
            if key.startswith('SCHILY.xattr.'):
                os.setxattr('/inspection/' + info.name, key.removeprefix('SCHILY.xattr.'),
                            value.encode('utf-8', 'surrogateescape'), follow_symlinks=False)
PY
exec "$@"
"""


class PrimitiveContext:
    """Record evidence and keep primitive operations within the subject's lifetime."""

    def __init__(self, session: Session, directory: Path, builder: str):
        self._session = session
        self._directory = directory
        self._builder = builder
        self._sequence = 0
        self._active = True

    def __enter__(self) -> Self:
        self._require_active()
        return self

    def __exit__(self, *exc: object) -> None:
        self._active = False

    def _require_active(self):
        if not self._active:
            raise RuntimeError("The subject session is closed.")

    @contextmanager
    def operation(self, name: str) -> Iterator[Path]:
        """Allocate an evidence directory. Record errors and propagate them unchanged."""
        self._require_active()
        if not name or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in name
        ):
            raise ValueError("Operation names must contain lowercase letters, digits, or hyphens.")
        self._sequence += 1
        directory = self._directory / f"{self._sequence:03}-{name}"
        directory.mkdir()
        try:
            yield directory
        except Exception as error:
            (directory / "error.txt").write_text(str(error) + "\n")
            raise

    def run(
        self, argv: list[str], *, timeout=60, environment=None
    ) -> subprocess.CompletedProcess[str]:
        """Execute in the subject. Return nonzero exit codes to the caller."""
        with self.operation("command") as directory:
            result = self._session.run(
                argv, timeout=timeout, environment=environment or {}, directory=directory
            )
            (directory / "execution.json").write_text(
                json.dumps({"argv": argv, "exit_code": result.returncode}, indent=2) + "\n"
            )
            return result

    def stage(self, archive: Path) -> None:
        """Copy a fixture archive into the subject without a host mount."""
        self._require_active()
        self._session.copy(archive)

    @contextmanager
    def snapshot(self, name: str = "snapshot") -> Iterator[Path]:
        """Export the current filesystem. Remove the archive when the scope ends."""
        with self.operation(name) as directory:
            archive = directory / "snapshot.tar"
            try:
                self._session.snapshot(archive)
                yield archive
            finally:
                archive.unlink(missing_ok=True)

    def inspect(self, argv: list[str], *, name="inspect") -> subprocess.CompletedProcess[str]:
        """Run builder tools offline against a snapshot extracted at /inspection.

        The subject receives no additional tools or changes from inspection.
        """
        if not argv:
            raise ValueError("Inspection requires a command.")
        with self.snapshot(name) as archive:
            result = self._session.runtime.build(
                self._builder, archive.parent, _INSPECT, *argv, check=False
            )
            (archive.parent / "stdout.txt").write_text(result.stdout)
            (archive.parent / "stderr.txt").write_text(result.stderr)
            return subprocess.CompletedProcess(
                argv, result.returncode, result.stdout, result.stderr
            )
