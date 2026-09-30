"""hum.meta.repository: MetadataRepository, the filesystem-facing half of
this library.

See documentation/design/metadata-library-design.md, section 9 (and
section 7 for the package-universe helpers), for the full design this
module implements. PackageMetadata itself knows nothing about the
filesystem -- no paths, no JSON, no metadata/ directory -- that split keeps
it purely about one package's domain rules.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .model import (
    CleanPackageMetadata,
    FedoraOrigin,
    FedoraOriginWithRelease,
    IndependentPackageMetadata,
    InvalidMetadataError,
    MalformedMetadataError,
    ModifiedFedoraPackageMetadata,
    ModifiedUpstreamPackageMetadata,
    PackageMetadata,
)
from .validation import _TAG_STRIP_PREFIX_DEFAULT

# The base-class fields with no cross-field rules (section 3's "Mutation
# Types" subsection) -- each is written as-is when not None, nothing more.
_PLAIN_OPTIONAL_FIELDS = (
    "upstream_branch",
    "track_upstream",
    "release_monitoring_project_id",
    "version_source",
    "version_suffix_strip",
    "upstream_version_transform",
    "source_availability_check",
    "cve_product",
    "version_transform",
    "fix_status",
)


def _fedora_to_raw_dict(fedora: FedoraOrigin) -> dict[str, object]:
    """FedoraOriginWithRelease is a FedoraOrigin, so the isinstance check
    handles both shapes without needing two separate functions."""
    data: dict[str, object] = {
        "git_url": fedora.git_url,
        "branch": fedora.branch,
        "sha": fedora.sha,
    }
    if isinstance(fedora, FedoraOriginWithRelease):
        data["release"] = fedora.release
    return data


def _to_raw_dict(metadata: PackageMetadata) -> dict[str, object]:
    """The inverse of PackageMetadata._from_raw_dict() -- the only place a
    PackageMetadata becomes a raw dict, kept entirely inside this module so
    nothing outside the library ever sees one either way."""
    data: dict[str, object] = {
        "version": metadata.version,
        "modification_status": metadata.modification_status.value,
        "upstream_repo": metadata.upstream_repo,
    }

    fedora = getattr(metadata, "fedora", None)
    if fedora is not None:
        data["fedora"] = _fedora_to_raw_dict(fedora)

    if hasattr(metadata, "modification_reason"):
        data["modification_reason"] = metadata.modification_reason

    if metadata.version_from_ref is not None:
        data["version_from_ref"] = {"type": metadata.version_from_ref.type}

    # tag_strip_prefix always has a value (defaults to _TAG_STRIP_PREFIX_DEFAULT);
    # it's only written when it's been customized away from that default,
    # matching how every other field here is omitted rather than written
    # with its default/absent value.
    if metadata.tag_strip_prefix != _TAG_STRIP_PREFIX_DEFAULT:
        data["tag_strip_prefix"] = metadata.tag_strip_prefix

    for field in _PLAIN_OPTIONAL_FIELDS:
        value = getattr(metadata, field)
        if value is not None:
            data[field] = value

    return data


class MetadataRepository:
    """Loads, saves, creates, and enumerates metadata/<package>.json files
    under one rpms-repo checkout."""

    def __init__(self, root: Path) -> None:
        """root is the rpms repo's top-level directory -- metadata/, rpms/,
        and target-packages.yml are all found relative to it, the same
        single-root convention dist_git.py/check_upstream_versions.py
        already use."""
        self._root = root

    def _canonical_path(self, package_name: str) -> Path:
        return self._root / "metadata" / f"{package_name}.json"

    def load(self, package_name: str, *, path: Path | None = None) -> PackageMetadata:
        """Reads metadata/<package_name>.json, or an explicit path if given.
        Determines the right concrete class either way. Raises OSError if
        the file doesn't exist, json.JSONDecodeError if it isn't valid
        JSON, and MalformedMetadataError if the document isn't a JSON
        object, can't be classified, or has a field this library doesn't
        recognize."""
        actual_path = path if path is not None else self._canonical_path(package_name)
        with actual_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise MalformedMetadataError(
                f"{package_name}: expected a JSON object, got {type(data).__name__}"
            )
        return PackageMetadata._from_raw_dict(package_name, data)

    def save(self, metadata: PackageMetadata, *, path: Path | None = None) -> None:
        """Calls metadata.validate() with no resolvers, and raises
        InvalidMetadataError without writing anything if the result has any
        violations. This applies identically whether path is the canonical
        location or a conflict-recovery staging path -- path changes
        *where* it's written, not what's enforced before writing.

        Otherwise writes to metadata/<package_name>.json, or the explicit
        path if given, serializing whichever concrete class it's given to
        the correct on-disk shape. Any field that's None internally is
        omitted from the JSON, never written as null. The only function
        that ever writes the canonical file."""
        result = metadata.validate()
        if result.violations:
            raise InvalidMetadataError(result)

        actual_path = path if path is not None else self._canonical_path(metadata.package_name)
        actual_path.parent.mkdir(parents=True, exist_ok=True)
        data = _to_raw_dict(metadata)
        with actual_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")

    def create_clean(
        self,
        package_name: str,
        *,
        version: str,
        upstream_repo: str,
        fedora: FedoraOriginWithRelease,
    ) -> CleanPackageMetadata:
        """Builds and immediately saves a new clean package."""
        metadata = CleanPackageMetadata(
            package_name=package_name, version=version, upstream_repo=upstream_repo, fedora=fedora
        )
        self.save(metadata)
        return metadata

    def create_modified_fedora(
        self,
        package_name: str,
        *,
        version: str,
        upstream_repo: str,
        fedora: FedoraOriginWithRelease,
        reason: str,
    ) -> ModifiedFedoraPackageMetadata:
        """Builds and immediately saves a new package that's modified while
        still on Fedora's version."""
        metadata = ModifiedFedoraPackageMetadata(
            package_name=package_name,
            version=version,
            upstream_repo=upstream_repo,
            fedora=fedora,
            modification_reason=reason,
        )
        self.save(metadata)
        return metadata

    def create_modified_upstream(
        self,
        package_name: str,
        *,
        version: str,
        upstream_repo: str,
        fedora: FedoraOrigin,
        reason: str,
    ) -> ModifiedUpstreamPackageMetadata:
        """Builds and immediately saves a new package that's already ahead
        of Fedora's version at creation time."""
        metadata = ModifiedUpstreamPackageMetadata(
            package_name=package_name,
            version=version,
            upstream_repo=upstream_repo,
            fedora=fedora,
            modification_reason=reason,
        )
        self.save(metadata)
        return metadata

    def create_independent(
        self, package_name: str, *, version: str, upstream_repo: str
    ) -> IndependentPackageMetadata:
        """Builds and immediately saves a new independent package,
        replacing an inferred decision with an explicit one."""
        metadata = IndependentPackageMetadata(
            package_name=package_name, version=version, upstream_repo=upstream_repo
        )
        self.save(metadata)
        return metadata

    def load_all(self) -> dict[str, PackageMetadata]:
        """Reads every metadata/*.json file, keyed by package name."""
        return {name: self.load(name) for name in self.packages_with_metadata()}

    def packages_with_spec(self) -> list[str]:
        """Packages under rpms/<name>/ that have a .spec file. Matches
        check_upstream_versions.py's get_all_packages() today: excludes
        dotdirs, requires at least one *.spec file present."""
        rpms_dir = self._root / "rpms"
        if not rpms_dir.exists():
            return []
        return sorted(
            entry.name
            for entry in rpms_dir.iterdir()
            if entry.is_dir() and not entry.name.startswith(".") and any(entry.glob("*.spec"))
        )

    def packages_with_metadata(self) -> list[str]:
        """Packages with a metadata/<name>.json file -- checks the file
        exists (a glob), never opens or parses it. Sorted, unlike
        dist_git.py's get_all_imported_packages() today, which returns
        filesystem-glob order -- see section 7's ordering rationale."""
        metadata_dir = self._root / "metadata"
        if not metadata_dir.exists():
            return []
        return sorted(path.stem for path in metadata_dir.glob("*.json"))

    def target_packages(self) -> list[str]:
        """Packages in target-packages.yml, unioned with the rpms/
        directory listing. Matches generate_resources.py's
        get_all_packages() today: a missing target-packages.yml is
        tolerated (treated as contributing no packages), and the rpms/
        directory union has no dotdir/spec-file filtering."""
        packages: set[str] = set()

        manifest_path = self._root / "target-packages.yml"
        if manifest_path.exists():
            with manifest_path.open(encoding="utf-8") as handle:
                manifest = yaml.safe_load(handle)
            if manifest and "packages" in manifest:
                packages.update(manifest["packages"])

        rpms_dir = self._root / "rpms"
        if rpms_dir.exists():
            packages.update(entry.name for entry in rpms_dir.iterdir() if entry.is_dir())

        return sorted(packages)

    def all_package_directories(self) -> list[str]:
        """Every directory under rpms/, unconditionally -- distinct from
        packages_with_spec(), which requires a .spec file to be present."""
        rpms_dir = self._root / "rpms"
        if not rpms_dir.exists():
            return []
        return sorted(entry.name for entry in rpms_dir.iterdir() if entry.is_dir())
