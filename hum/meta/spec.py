"""hum.meta.spec: resolving a package's spec file version via rpmspec.

Not called automatically by anything else in this library, because
PackageMetadata itself has no access to the filesystem or a
package's rpms/<name>/ directory -- a caller wires this in
directly, e.g.:

    metadata.validate(
        resolve_spec_version=lambda m: resolve_spec_version(root / "rpms" / m.package_name)
    )
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_RPMSPEC = "rpmspec"


def resolve_spec_version(package_dir: Path) -> str:
    """Resolves the spec's Version: line via rpmspec, with macros fully
    expanded. package_dir is the package's directory (e.g. rpms/bash), not
    the spec file itself -- the *.spec file inside it is found
    automatically. Raises ValueError on anything unexpected: rpmspec not on
    PATH, no spec file, more than one, rpmspec failing, or unparseable
    output."""
    if shutil.which(_RPMSPEC) is None:
        raise ValueError(f"{_RPMSPEC} not found on PATH")

    spec_files = list(package_dir.glob("*.spec"))
    if len(spec_files) != 1:
        raise ValueError(
            f"expected exactly one .spec file in {package_dir}, found {len(spec_files)}"
        )

    # This behavior is inherited from dist_git.py.  Future iterations should consider
    # not passing a package_dir but perhaps a qualified path to the spec itself.
    spec_file = spec_files[0]

    result = subprocess.run(
        [
            _RPMSPEC,
            "-q",
            "--qf",
            "%{VERSION}\n%{RELEASE}\n",
            "--define=dist %{nil}",
            f"--define=_sourcedir {package_dir}",
            "--srpm",
            str(spec_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"{_RPMSPEC} failed for {spec_file}: {result.stderr.strip()}")

    lines = result.stdout.strip().splitlines()
    if len(lines) < 2 or not lines[0]:
        raise ValueError(f"unexpected {_RPMSPEC} output for {spec_file}: {result.stdout!r}")
    return lines[0]
