"""Tests that every RPM package has upstream_repo in its metadata."""

import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
RPMS_DIR = ROOT_DIR / "rpms"
METADATA_DIR = ROOT_DIR / "metadata"

sys.path.insert(0, str(ROOT_DIR))
from hum.meta import MetadataRepository  # noqa: E402
from hum.meta.model import MalformedMetadataError  # noqa: E402

_METADATA_REPO = MetadataRepository(ROOT_DIR)

MISSING_FIELD_MESSAGE = """\
Package '{package}' is missing the 'upstream_repo' field in metadata/{package}.json.

Every package must have an 'upstream_repo' entry pointing to its canonical
upstream git repository.

To fix this, add the field to metadata/{package}.json:

    "upstream_repo": "https://github.com/example/project"

Optional fields you may also set:

    "upstream_branch": "<branch>"          # for versioned packages sharing a repo
    "cve_product": "<Vendor / Product>"    # or ["<Vendor / Product>", "<Vendor2 / Product2>"]
    "version_transform": "<rule>"          # version transform rule (defined and declared in tools repo)
    "upstream_version_transform": "<rule>"  # Anitya-to-RPM version transform (e.g., "openjdk_to_rpm")

If no upstream git repository exists, use the Fedora DistGit URL as a fallback:

    "upstream_repo": "https://src.fedoraproject.org/rpms/{package}"
"""


def _rpm_package_names() -> list[str]:
    """Return sorted list of RPM package directory names."""
    if not RPMS_DIR.is_dir():
        pytest.skip(f"rpms/ directory not found at {RPMS_DIR}")
    return sorted(d.name for d in RPMS_DIR.iterdir() if d.is_dir())


@pytest.mark.parametrize("package", _rpm_package_names())
def test_upstream_repo_exists(package: str) -> None:
    """Each RPM package must have upstream_repo set in its metadata."""
    path = METADATA_DIR / f"{package}.json"
    assert path.exists(), (
        f"Package '{package}' has no metadata file at metadata/{package}.json. "
        f"Create the file with at least: "
        f'{{"upstream_repo": "https://github.com/example/project", '
        f'"version": "...", "modification_status": "independent"}}'
    )
    try:
        _METADATA_REPO.load(package)
    except MalformedMetadataError as e:
        if "upstream_repo" in str(e):
            pytest.fail(MISSING_FIELD_MESSAGE.format(package=package))
        pytest.fail(f"metadata/{package}.json is malformed: {e}")
