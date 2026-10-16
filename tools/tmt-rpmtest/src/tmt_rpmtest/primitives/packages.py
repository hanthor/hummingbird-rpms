"""RPM inspection primitives. Tests decide which observations to assert."""

import json
import subprocess
import tarfile
from dataclasses import asdict, dataclass

from tmt_rpmtest.execution.context import PrimitiveContext
from tmt_rpmtest.primitives.archive import RootArchive, glob_pattern
from tmt_rpmtest.primitives.elf import interpreter


@dataclass(frozen=True)
class Content:
    path: str
    info: tarfile.TarInfo | None


@dataclass(frozen=True)
class Link:
    path: str
    target: str
    resolved: str | None
    error: str | None


@dataclass(frozen=True)
class RuntimeLink:
    path: str
    loader: str
    result: subprocess.CompletedProcess[str]

    @property
    def resolved(self) -> bool:
        return (
            self.result.returncode == 0
            and "not found" not in self.result.stdout + self.result.stderr
        )


class Packages:
    def __init__(self, context: PrimitiveContext):
        self._context = context

    def query(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run an RPM query against the current snapshot using external tools."""
        result = self._context.inspect(["rpm", "--root", "/inspection", *args], name="rpm")
        return subprocess.CompletedProcess(
            ["rpm", *args], result.returncode, result.stdout, result.stderr
        )

    def _lines(self, *args: str) -> tuple[str, ...]:
        result = self.query(*args)
        result.check_returncode()
        return tuple(result.stdout.splitlines())

    def verify(self, *names: str) -> subprocess.CompletedProcess[str]:
        """Verify selected packages. Return RPM's status and reported differences."""
        if not names:
            raise ValueError("Select at least one package to verify.")
        return self.query("-V", *names)

    def installed(self) -> frozenset[str]:
        """Return installed package names from the current subject's RPM database."""
        return frozenset(self._lines("-qa", "--qf", "%{NAME}\\n"))

    def owners(self, path: str) -> frozenset[str]:
        """Return all package owners. An unowned path raises CalledProcessError."""
        return frozenset(self._lines("-qf", "--qf", "%{NAME}\\n", path))

    def metadata(self, package: str, field: str) -> tuple[str, ...]:
        """Read provides, requires, files, or nevra. Capability fields return names."""
        tags = {
            "provides": "PROVIDENAME",
            "requires": "REQUIRENAME",
            "files": "FILENAMES",
            "nevra": "NEVRA",
        }
        if field not in tags:
            raise ValueError(f"Unknown RPM metadata field: {field}")
        return self._lines("-q", "--qf", f"[%{{{tags[field]}}}\\n]", package)

    def contents(self, package: str, *, match: tuple[str, ...] = ("/**",)) -> tuple[Content, ...]:
        """Return owned paths and current file metadata, retaining missing paths as None.

        Globs support *, ** and ?. Tests decide counts, types, and allowed prefixes.
        """
        patterns = [glob_pattern(pattern) for pattern in match]
        paths = sorted(set(self.metadata(package, "files")))
        with self._context.snapshot("contents") as snapshot:
            with tarfile.open(snapshot) as archive:
                root = RootArchive(archive)
                result = []
                for path in paths:
                    if not any(pattern.fullmatch(path) for pattern in patterns):
                        continue
                    try:
                        _, info = root.resolve(path, follow=False)
                    except (FileNotFoundError, NotADirectoryError):
                        info = None
                    result.append(Content(path, info))
                (snapshot.parent / "contents.json").write_text(
                    json.dumps(
                        [
                            {
                                "path": item.path,
                                "present": item.info is not None,
                                "mode": item.info.mode if item.info else None,
                                "type": item.info.type.decode("ascii") if item.info else None,
                                "target": item.info.linkname if item.info else None,
                            }
                            for item in result
                        ],
                        indent=2,
                    )
                    + "\n"
                )
                return tuple(result)

    def links(self, package: str) -> tuple[Link, ...]:
        """Observe owned symlinks, resolving their targets within the subject root."""
        paths = self.metadata(package, "files")
        with self._context.snapshot("links") as snapshot:
            with tarfile.open(snapshot) as archive:
                root = RootArchive(archive)
                result = []
                for path in sorted(set(paths)):
                    try:
                        _, info = root.resolve(path, follow=False)
                    except (FileNotFoundError, NotADirectoryError):
                        continue
                    if not info.issym():
                        continue
                    try:
                        resolved, _ = root.resolve(path)
                        result.append(Link(path, info.linkname, resolved, None))
                    except OSError as error:
                        result.append(Link(path, info.linkname, None, str(error)))
                (snapshot.parent / "links.json").write_text(
                    json.dumps([asdict(item) for item in result], indent=2) + "\n"
                )
                return tuple(result)

    def runtime_links(self, package: str, *, timeout=60) -> tuple[RuntimeLink, ...]:
        """Ask each dynamic ELF's loader to resolve dependencies inside the subject.

        Static and non-ELF files are skipped. No ldd, shell, or ELF tools are installed.
        """
        paths = self.metadata(package, "files")
        checks = []
        with self._context.snapshot("elf") as snapshot:
            with tarfile.open(snapshot) as archive:
                root = RootArchive(archive)
                for path in sorted(set(paths)):
                    try:
                        _, info = root.resolve(path, follow=False)
                    except (FileNotFoundError, NotADirectoryError):
                        continue
                    if not info.isfile():
                        continue
                    with archive.extractfile(info) as stream:
                        loader = interpreter(stream)
                    if loader is None:
                        continue
                    try:
                        root.resolve(loader)
                    except (FileNotFoundError, NotADirectoryError):
                        result = subprocess.CompletedProcess(
                            [loader, "--list", path], 127, "", f"Missing dynamic loader: {loader}"
                        )
                    else:
                        result = None
                    checks.append((path, loader, result))
                (snapshot.parent / "elf.json").write_text(
                    json.dumps(
                        [
                            {
                                "path": path,
                                "loader": loader,
                                "error": result.stderr if result else None,
                            }
                            for path, loader, result in checks
                        ],
                        indent=2,
                    )
                    + "\n"
                )
        return tuple(
            RuntimeLink(
                path,
                loader,
                result
                if result is not None
                else self._context.run([loader, "--list", path], timeout=timeout),
            )
            for path, loader, result in checks
        )
