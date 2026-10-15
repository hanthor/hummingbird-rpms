"""RPM inspection primitives. Tests decide which observations to assert."""

import subprocess
from collections.abc import Callable


class Packages:
    def __init__(self, query: Callable[..., subprocess.CompletedProcess[str]]):
        self._query = query

    def _lines(self, *args: str) -> tuple[str, ...]:
        result = self._query(*args)
        result.check_returncode()
        return tuple(result.stdout.splitlines())

    def verify(self, *names: str) -> subprocess.CompletedProcess[str]:
        """Verify selected packages. Return RPM's status and reported differences."""
        if not names:
            raise ValueError("Select at least one package to verify.")
        return self._query("-V", *names)

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
