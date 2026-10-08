"""hum.meta.spec: spec file operations for RPM packages.

Uses rpmspec to resolve macros so the library always works with
expanded values. Not called automatically by anything else in this
library, because PackageMetadata itself has no access to the
filesystem or a package's rpms/<name>/ directory -- a caller wires
this in directly.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .model import (
    CleanPackageMetadata,
    IndependentPackageMetadata,
    ModifiedFedoraPackageMetadata,
    ModifiedUpstreamPackageMetadata,
    PackageMetadata,
)

_RPMSPEC = "rpmspec"


def find_spec(package_dir: Path) -> Path:
    """Find the single spec file in a package directory.

    Raises FileNotFoundError when zero or more than one .spec file
    is found.
    """
    spec_files = list(package_dir.glob("*.spec"))
    if len(spec_files) != 1:
        raise FileNotFoundError(
            f"expected exactly one .spec file in {package_dir}, "
            f"found {len(spec_files)}"
        )
    return spec_files[0]


def resolve_spec_version(package_dir: Path) -> str:
    """Resolves the spec's Version: line via rpmspec, with macros fully
    expanded. package_dir is the package's directory (e.g. rpms/bash), not
    the spec file itself -- the *.spec file inside it is found
    automatically. Raises ValueError on anything unexpected: rpmspec not on
    PATH, no spec file, more than one, rpmspec failing, or unparseable
    output."""
    if shutil.which(_RPMSPEC) is None:
        raise ValueError(f"{_RPMSPEC} not found on PATH")

    spec_file = find_spec(package_dir)

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


def _resolve_release(package_dir: Path) -> str:
    """Use rpmspec to get the resolved Release value from a spec.

    Sets dist to %%{nil} so the result is the release without the
    dist suffix, matching the convention used by bump_release and
    update_release_in_spec in ci/dist_git.py.
    """
    if shutil.which(_RPMSPEC) is None:
        raise ValueError(f"{_RPMSPEC} not found on PATH")

    spec_file = find_spec(package_dir)

    result = subprocess.run(
        [
            _RPMSPEC,
            "-q",
            "--qf",
            "%{RELEASE}\n",
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

    release = result.stdout.strip().splitlines()[0]
    if not release:
        raise ValueError(f"empty release from {_RPMSPEC} for {spec_file}")
    return release


def _string_arithmetic(release: str) -> str:
    """Bump a release string with no base context.

    If it ends with .N (where N is an integer), increment N.
    Otherwise append .1.

    Lifted from ci/dist_git.py bump_release() -- the proven
    fallback that handles macros, git snapshots, and any other
    non-standard release string.
    """
    match = re.match(r"^(.+)\.(\d+)$", release)
    if match:
        base, n = match.groups()
        return f"{base}.{int(n) + 1}"
    return f"{release}.1"


def _update_spec_release(
    spec_file: Path, content: str, new_release: str, *, current: str | None = None,
) -> None:
    """Write a new Release value into a spec file on disk.

    Handles three cases, lifted from ci/dist_git.py
    update_release_in_spec():

    1. Macro-based release before %%{?dist} -- manipulate the raw
       macro string directly (append/increment .N suffix).
    2. Macros without %%{?dist} -- refuse (requires manual rebuild).
    3. Plain release with %%{?dist} -- substitute the new value.

    Preserves whitespace after "Release:" and any content after
    %%{?dist}.
    """
    release_match = re.search(
        r"^Release:\s+(.*)%\{\??dist\}(.*)$", content, flags=re.MULTILINE
    )

    if release_match:
        release_before_dist = release_match.group(1)
        release_after_dist = release_match.group(2)

        if "%{" in release_before_dist:
            # Macro-based release: manipulate the raw string directly.
            # Use the computed new_release and current resolved value to
            # distinguish "first rebuild (append .1)" from "subsequent
            # rebuild (increment last .N)".  Without this, a Fedora
            # baseline like %{baserelease}.1 (resolving to 3.1) would
            # have its .1 incremented to .2 (3.2) instead of getting
            # .1 appended (3.1.1).
            if current is not None and new_release == f"{current}.1":
                new_release_line = f"{release_before_dist}.1"
            else:
                suffix_match = re.match(r"^(.*)\.(\d+)$", release_before_dist)
                if suffix_match:
                    base = suffix_match.group(1)
                    num = int(suffix_match.group(2))
                    new_release_line = f"{base}.{num + 1}"
                else:
                    new_release_line = f"{release_before_dist}.1"

            new_content = re.sub(
                r"^Release:(\s+).*%\{\??dist\}.*$",
                lambda m: f"Release:{m.group(1)}{new_release_line}%{{?dist}}{release_after_dist}",
                content,
                flags=re.MULTILINE,
            )
            spec_file.write_text(new_content)
            return

    # Macros without %{?dist} require manual handling.
    release_match_no_dist = re.search(
        r"^Release:\s+.*%\{[^}]+\}", content, flags=re.MULTILINE
    )
    if release_match_no_dist and not release_match:
        raise ValueError(
            f"{spec_file.name} uses macros in Release field without "
            f"%{{?dist}}: {release_match_no_dist.group(0)}\n"
            f"This package requires manual rebuild."
        )

    # Standard case: no macros before dist.
    new_content = re.sub(
        r"^Release:(\s+).*?(%\{\??dist\})(.*)$",
        lambda m: f"Release:{m.group(1)}{new_release}{m.group(2)}{m.group(3)}",
        content,
        flags=re.MULTILINE,
    )

    if new_content == content:
        raise ValueError(
            f"failed to update Release in {spec_file.name} -- "
            f"Release field missing %{{?dist}}"
        )

    spec_file.write_text(new_content)


def bump_release(metadata: PackageMetadata, package_dir: Path) -> str:
    """Bump the Release field in a spec file on disk.

    Reads the spec to get the current resolved release via rpmspec,
    picks the bump strategy from the metadata state, writes the
    result back (handling macro-based and plain releases internally),
    and returns the new resolved release value.
    """
    spec_file = find_spec(package_dir)
    content = spec_file.read_text()

    # Resolve macros via rpmspec so we always work with expanded values
    current = _resolve_release(package_dir)

    match metadata:
        case CleanPackageMetadata() | ModifiedFedoraPackageMetadata():
            base = metadata.fedora.release
            if current == base:
                new = f"{base}.1"
            else:
                m = re.fullmatch(rf"{re.escape(base)}\.(\d+)", current)
                if m:
                    new = f"{base}.{int(m.group(1)) + 1}"
                else:
                    new = _string_arithmetic(current)

        case ModifiedUpstreamPackageMetadata() | IndependentPackageMetadata():
            new = _string_arithmetic(current)

        case _:
            raise TypeError(
                f"unsupported metadata type: {type(metadata).__name__}"
            )

    _update_spec_release(spec_file, content, new, current=current)

    return _resolve_release(package_dir)
