"""A minimal RPM subject for ordinary Python assertions in tmt tests."""

import json
import os
import signal
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from tmt_rpmtest.podman import Podman, Session
from tmt_rpmtest.rpm import digest

INSPECT = r"""
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
exec rpm --root /inspection "$@"
"""


class Subject:
    def __init__(self, session: Session, directory: Path, manifest: dict):
        self.session = session
        self.directory = directory
        self.manifest = manifest
        self.sequence = 0

    def _directory(self, operation):
        self.sequence += 1
        directory = self.directory / f"{self.sequence:03}-{operation}"
        directory.mkdir()
        return directory

    def run(self, argv: list[str], *, timeout=60, environment=None):
        directory = self._directory("command")
        try:
            result = self.session.run(
                argv, timeout=timeout, environment=environment or {}, directory=directory
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            (directory / "error.txt").write_text(str(error) + "\n")
            raise
        (directory / "execution.json").write_text(
            json.dumps(
                {
                    "argv": argv,
                    "exit_code": result.returncode,
                },
                indent=2,
            )
            + "\n"
        )
        return result

    def copy(self, source: Path, destination: str):
        target = PurePosixPath(destination)
        if not target.is_absolute() or ".." in target.parts or str(target) == "/":
            raise ValueError("Fixture destinations must be absolute paths below root.")
        directory = self._directory("fixture")
        archive = directory / "fixture.tar"

        def metadata(info):
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            return info

        with tarfile.open(archive, "w") as output:
            for parent in reversed(target.parents[:-1]):
                info = tarfile.TarInfo(str(parent).lstrip("/"))
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                output.addfile(info)
            output.add(source, arcname=str(target).lstrip("/"), filter=metadata)
        self.session.copy(archive)

    def stat(self, path: str) -> tarfile.TarInfo:
        directory = self._directory("stat")
        archive = directory / "snapshot.tar"
        self.session.snapshot(archive)
        try:
            with tarfile.open(archive) as snapshot:
                for info in snapshot:
                    if info.name.removeprefix("./").lstrip("/") == path.lstrip("/"):
                        return info
            raise FileNotFoundError(path)
        finally:
            archive.unlink()

    def rpm(self, *args: str):
        directory = self._directory("rpm")
        archive = directory / "snapshot.tar"
        self.session.snapshot(archive)
        try:
            result = self.session.runtime.build(
                self.manifest["builder"], directory, INSPECT, *args, check=False
            )
        finally:
            archive.unlink()
        (directory / "stdout.txt").write_text(result.stdout)
        (directory / "stderr.txt").write_text(result.stderr)
        return subprocess.CompletedProcess(
            ["rpm", *args], result.returncode, result.stdout, result.stderr
        )


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
            yield Subject(session, work, manifest)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
