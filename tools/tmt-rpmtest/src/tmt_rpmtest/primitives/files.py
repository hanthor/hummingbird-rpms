"""Fixture staging and filesystem inspection without subject-side utilities."""

import tarfile
from pathlib import Path, PurePosixPath

from tmt_rpmtest.context import PrimitiveContext


class Files:
    def __init__(self, context: PrimitiveContext):
        self._context = context

    def copy(self, source: Path, destination: str):
        target = PurePosixPath(destination)
        if not target.is_absolute() or ".." in target.parts or str(target) == "/":
            raise ValueError("Fixture destinations must be absolute paths below root.")
        with self._context.operation("fixture") as directory:
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
            self._context.stage(archive)

    def stat(self, path: str) -> tarfile.TarInfo:
        with self._context.snapshot("stat") as archive:
            with tarfile.open(archive) as snapshot:
                for info in snapshot:
                    if info.name.removeprefix("./").lstrip("/") == path.lstrip("/"):
                        return info
            raise FileNotFoundError(path)
