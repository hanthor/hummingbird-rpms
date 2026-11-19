"""Validation predicates for hummingbird_rpm_metadata.

Each ``check_*`` function encodes exactly one ADR-0003 rule (or one tightly
related cluster of rules). Every mutation path in this library -- setters,
transitions, ``update()``, ``create_*()``, ``MetadataRepository.load()``'s
classification, and ``validate()`` -- calls into these same functions rather
than re-implementing the same condition, so a future rule change is a
one-function edit instead of hunting down every call site that happened to
duplicate it.

Every function here returns ``None`` (or an empty list, for the ones that can
find more than one problem at once) on success, rather than raising.
Translating a violation into a raised exception is the caller's job:
setters/transitions/``create_*()``/``load()`` raise immediately, at the point
of the bad call, while ``validate()`` collects every violation into a
``ValidationResult`` instead of raising at all.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import PackageMetadata, VersionFromRef


class ModificationStatus(str, Enum):
    """The value space for the on-disk modification_status field. This is
    not the mechanism anything dispatches on -- which of the four concrete
    PackageMetadata classes an object is, is that mechanism instead."""

    CLEAN = "clean"
    MODIFIED = "modified"
    INDEPENDENT = "independent"


# Real Fedora/RHEL dist tags are ".fc<N>", ".el<N>", ".el<N>_<N>", or
# ".eln<N>".  fedora.release is expected to already be the resolved,
# dist-stripped value, so any match here means it wasn't stripped.
_DIST_TAG_SUFFIX = re.compile(r"\.(?:fc|el|eln)\d+(?:_\d+)?$")

# The value tag_strip_prefix takes when gitlab_tags tracking hasn't
# customized it -- shared with model.py/repository.py so construction,
# loading, serialization, and this check all agree by construction, not by
# coincidence.
_TAG_STRIP_PREFIX_DEFAULT = "v"


def _fedora_release(metadata: object) -> object:
    """Safely reads metadata.fedora.release, treating "no fedora at all"
    and "fedora present but no release" the same way -- None either way.
    Duck-typed like the rest of this module, so it copes with
    attribute-polluted or non-real objects too."""
    return getattr(getattr(metadata, "fedora", None), "release", None)


@dataclass(frozen=True)
class ValidationError:
    """One validation violation, with a message that fully explains the
    problem on its own -- no external rule number needed to make sense of
    it."""

    message: str


def _check_present_and_non_empty(value: object, *, field: str) -> ValidationError | None:
    """Shared by every check_* function whose whole job is "must be a
    non-empty string" -- one place to change if that definition ever
    changes, instead of the same condition reimplemented per field."""
    if not isinstance(value, str) or not value:
        return ValidationError(message=f"{field} must be present and non-empty")
    return None


def check_version(value: object) -> ValidationError | None:
    """version must be present and non-empty."""
    return _check_present_and_non_empty(value, field="version")


def check_modification_status(value: object) -> ValidationError | None:
    """modification_status must be present and equal to clean, modified,
    or independent."""
    if value is None:
        return ValidationError(message="modification_status must be present")
    if not isinstance(value, str):
        return ValidationError(
            message=(
                f"modification_status must be a string, got {type(value).__name__}"
            )
        )
    if value not in {status.value for status in ModificationStatus}:
        return ValidationError(
            message=(
                f"modification_status must be clean, modified, or independent, got {value!r}"
            )
        )
    return None


def check_upstream_repo(value: object) -> ValidationError | None:
    """upstream_repo must be present and non-empty."""
    return _check_present_and_non_empty(value, field="upstream_repo")


def check_private_product(value: object) -> ValidationError | None:
    """An optional private product must be a non-empty string."""
    if value is None:
        return None
    return _check_present_and_non_empty(value, field="private_product")


def check_private_products(
    assignments: Mapping[str, str], known_products: Collection[str],
) -> list[ValidationError]:
    """Every package assignment must match a configured private RPA."""
    return [
        ValidationError(
            message=(
                f"Package '{package}' has private_product: '{product}' but no RPA "
                f"entry has private_product: '{product}'. "
                f"Known products: {sorted(known_products)}"
            )
        )
        for package, product in assignments.items()
        if product not in known_products
    ]


def check_version_source(value: object) -> ValidationError | None:
    """version_source, when present, must be gitlab_tags."""
    if value is not None and value != "gitlab_tags":
        return ValidationError(
            message=f"version_source, when present, must be gitlab_tags, got {value!r}"
        )
    return None


def check_modification_reason(value: object) -> ValidationError | None:
    """modification_reason must be present and non-empty."""
    return _check_present_and_non_empty(value, field="modification_reason")


def check_fedora_release(release: object) -> ValidationError | None:
    """fedora.release, when present, must be a non-empty string without a
    dist-tag suffix (e.g. .fc42, .el9). None means no release is carried,
    which is valid for modified-upstream and independent packages."""
    if release is None:
        return None
    if not isinstance(release, str):
        return ValidationError(
            message=(
                f"fedora.release must be a string, got {type(release).__name__}"
            )
        )
    if not release:
        return ValidationError(message="fedora.release must not be empty")
    if _DIST_TAG_SUFFIX.search(release):
        return ValidationError(
            message=(
                f"fedora.release {release!r} includes a dist-tag suffix, which isn't allowed "
                "here -- it should already be the resolved, dist-stripped value"
            )
        )
    return None


def check_version_from_ref(value: VersionFromRef | None) -> ValidationError | None:
    """version_from_ref.type, when present, must be commit-date."""
    if value is not None and value.type != "commit-date":
        return ValidationError(
            message=f"version_from_ref.type must be commit-date, got {value.type!r}"
        )
    return None


def check_upstream_tracking_combination(
    *,
    track_upstream: str | None,
    release_monitoring_project_id: int | None,
    version_source: str | None,
    tag_strip_prefix: str,
) -> list[ValidationError]:
    """Validates track_upstream/release_monitoring_project_id/version_source/
    tag_strip_prefix as a combination, the one cluster of upstream-tracking
    configuration that's genuinely cross-field: release_monitoring_project_id
    must be present whenever track_upstream is present without
    version_source; release_monitoring_project_id being present requires
    track_upstream; version_source being gitlab_tags forbids
    release_monitoring_project_id; and a customized tag_strip_prefix
    requires version_source to be gitlab_tags. release_monitoring_project_id,
    when present, must be an int, and track_upstream, when present, must be a
    str -- both checked directly here rather than trusting the type hint,
    because a raw JSON document can hand in anything.

    ``tag_strip_prefix`` always has a value on a constructed object (it
    defaults to "v"), so "customized" is read as "not equal to the
    default" -- the only signal left once presence/default can't be told
    apart at the object level. Worth a second look: this is an
    interpretation, not something the design doc pinned down explicitly.
    """
    violations: list[ValidationError] = []

    if track_upstream is not None and not isinstance(track_upstream, str):
        violations.append(
            ValidationError(message=f"track_upstream must be a str, got {track_upstream!r}")
        )

    if release_monitoring_project_id is not None and (
        isinstance(release_monitoring_project_id, bool)
        or not isinstance(release_monitoring_project_id, int)
    ):
        violations.append(
            ValidationError(
                message=(
                    "release_monitoring_project_id must be an int, got "
                    f"{release_monitoring_project_id!r}"
                )
            )
        )

    if (
        track_upstream is not None
        and version_source is None
        and release_monitoring_project_id is None
    ):
        violations.append(
            ValidationError(
                message=(
                    "release_monitoring_project_id must be present when track_upstream is "
                    "present and version_source is absent"
                )
            )
        )

    if release_monitoring_project_id is not None and track_upstream is None:
        violations.append(
            ValidationError(
                message="track_upstream must be present when release_monitoring_project_id is present"
            )
        )

    if version_source == "gitlab_tags" and release_monitoring_project_id is not None:
        violations.append(
            ValidationError(
                message="release_monitoring_project_id must be absent when version_source is gitlab_tags"
            )
        )

    if tag_strip_prefix != _TAG_STRIP_PREFIX_DEFAULT and version_source != "gitlab_tags":
        violations.append(
            ValidationError(
                message=(
                    "version_source must be gitlab_tags when tag_strip_prefix is customized "
                    f"away from the default (got {tag_strip_prefix!r})"
                )
            )
        )

    return violations


def check_fedora_matches_state(metadata: PackageMetadata) -> list[ValidationError]:
    """fedora must be present with exactly the shape modification_status
    implies: absent for independent; present with git_url/branch/sha for
    clean/modified; and present with release for clean.

    Reads ``getattr(metadata, "fedora", None)`` directly rather than
    trusting ``isinstance``/class identity, because Python doesn't stop a
    caller from adding a ``fedora`` attribute to an instance whose class
    never declared one (design doc, section 3's rule-enforcement
    subsection).
    """
    violations: list[ValidationError] = []
    status = metadata.modification_status
    fedora = getattr(metadata, "fedora", None)

    if status == ModificationStatus.INDEPENDENT:
        if fedora is not None:
            violations.append(
                ValidationError(message="independent implies fedora is absent")
            )
        return violations

    if fedora is None:
        violations.append(
            ValidationError(message=f"{status} implies fedora is present")
        )
        return violations

    for field in ("git_url", "branch", "sha"):
        if not getattr(fedora, field, None):
            violations.append(ValidationError(message=f"fedora.{field} must be present"))

    if status == ModificationStatus.CLEAN and _fedora_release(metadata) is None:
        violations.append(
            ValidationError(message="clean implies fedora.release is present")
        )

    if status == ModificationStatus.MODIFIED and _fedora_release(metadata) is not None:
        from .model import _NoStoredReleaseMixin  # noqa: PLC0415

        if isinstance(metadata, _NoStoredReleaseMixin):
            violations.append(
                ValidationError(
                    message="modified-upstream must not carry a fedora release "
                    "(would round-trip as modified-fedora)"
                )
            )

    return violations


def check_modification_reason_matches_state(metadata: PackageMetadata) -> list[ValidationError]:
    """modification_reason must be present exactly when modification_status
    is modified, and absent otherwise.

    Reads ``hasattr(metadata, "modification_reason")`` directly, the same
    reasoning as ``check_fedora_matches_state``.
    """
    violations: list[ValidationError] = []
    status = metadata.modification_status
    reason = getattr(metadata, "modification_reason", None)

    if status == ModificationStatus.MODIFIED:
        if not reason:
            violations.append(
                ValidationError(message="modified implies modification_reason is present")
            )
    elif hasattr(metadata, "modification_reason") and reason:
        violations.append(
            ValidationError(message=f"{status} implies modification_reason is absent")
        )

    return violations


def check_version_from_ref_requires_upstream_repo(
    metadata: PackageMetadata,
) -> ValidationError | None:
    """If version_from_ref is present, upstream_repo must also be present.

    Checked directly against the object's current values rather than
    inferred from the ``upstream_repo`` setter already requiring it to be
    non-empty everywhere.
    """
    if metadata.version_from_ref is not None and not metadata.upstream_repo:
        return ValidationError(
            message="version_from_ref is present, so upstream_repo must also be present"
        )
    return None
