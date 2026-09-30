"""hum.meta.model: the PackageMetadata class hierarchy.
"""

from __future__ import annotations

import abc
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from .normalize import normalize_cve_product, normalize_fix_status
from .validation import (
    ModificationStatus,
    ValidationError,
    _check_present_and_non_empty,
    _fedora_release,
    _TAG_STRIP_PREFIX_DEFAULT,
    check_fedora_matches_state,
    check_fedora_release,
    check_modification_reason,
    check_modification_reason_matches_state,
    check_modification_status,
    check_upstream_repo,
    check_upstream_tracking_combination,
    check_version,
    check_version_from_ref,
    check_version_from_ref_requires_upstream_repo,
    check_version_source,
)

logger = logging.getLogger(__name__)


def _raise_on_violations(violations: list[ValidationError]) -> None:
    """Shared by the three upstream-tracking configuration methods -- joins
    every violation into one message rather than only surfacing the first,
    because check_upstream_tracking_combination can return more than one."""
    if violations:
        raise ValueError("; ".join(v.message for v in violations))


class MetadataError(Exception):
    """Base class for errors raised by this library."""


class InvalidStateTransitionError(MetadataError):
    """Raised when code expected a package to be in one state but it's in
    another -- e.g. calling ensure_clean() (below) on a package that isn't
    actually a CleanPackageMetadata."""


class StaleMetadataError(MetadataError):
    """Raised on any attribute access against an object after one of its own
    transition methods (to_clean()/to_modified_fedora()/etc.) has already run
    on it. A transition returns a new object rather than mutating self, so
    the pre-transition object would otherwise remain fully usable -- silently
    stale rather than clearly wrong. Forgetting to replace every reference to
    it is a real mistake a caller can make; this turns it into an immediate
    failure at the next use instead of a silent one. update() is unaffected,
    because it mutates in place on purpose and never produces a stale sibling
    object."""


class MalformedMetadataError(MetadataError):
    """Raised by MetadataRepository.load() (not yet implemented) when a
    document can't be classified into one of the four concrete classes at
    all -- a missing required field, an unrecognized field, or a value that
    contradicts what modification_status implies. Distinct from
    InvalidStateTransitionError, which is about calling the wrong method on
    an already-constructed object; this is about the raw document itself,
    before any object exists to validate."""


class ReleaseMismatchError(MetadataError):
    """Raised by bump_release() (both release-computing mixins) when
    current_release doesn't conform to the shape this object's own state
    says it should -- the spec disagrees with fedora.release (on-Fedora
    classes) or isn't shaped like a 0.N release (on-upstream/independent
    classes). Not a metadata-authoring problem like MalformedMetadataError/
    InvalidMetadataError -- this is a caller-supplied argument disagreeing
    with the object it was called against."""


@dataclass(frozen=True)
class ValidationResult:
    """What validate() found, plus whether the resolver-dependent spec
    version check actually ran -- an empty violations list is only
    meaningful once you know whether that check was skipped for lack of a
    resolver."""

    checked_spec_version: bool
    violations: list[ValidationError]


class InvalidMetadataError(MetadataError):
    """Raised by MetadataRepository.save() (not yet implemented) when
    validate(), called with no resolvers, finds any violation. Carries the
    ValidationResult that caused the refusal. Distinct from
    MalformedMetadataError, which is about a raw document load() can't
    classify at all -- this is about a real, already-constructed
    PackageMetadata that validate() found a real problem with."""

    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        super().__init__(f"{len(result.violations)} validation violation(s): {result.violations}")


_ZERO_DOT_N_RELEASE = re.compile(r"0\.(\d+)")

# The complete top-level metadata/<package>.json field set, per ADR-0003's
# field reference. package_name isn't here -- it comes from the filename,
# never from the JSON body itself.
_RECOGNIZED_FIELDS = frozenset(
    {
        "version",
        "modification_status",
        "modification_reason",
        "fedora",
        "upstream_repo",
        "upstream_branch",
        "version_from_ref",
        "track_upstream",
        "release_monitoring_project_id",
        "version_source",
        "tag_strip_prefix",
        "version_suffix_strip",
        "upstream_version_transform",
        "source_availability_check",
        "cve_product",
        "version_transform",
        "fix_status",
    }
)
_RECOGNIZED_FEDORA_FIELDS = frozenset({"git_url", "branch", "sha", "release"})
_RECOGNIZED_VERSION_FROM_REF_FIELDS = frozenset({"type"})


def _parse_fedora(package_name: str, raw: object) -> FedoraOrigin | FedoraOriginWithRelease | None:
    """Builds the fedora sub-object from raw JSON data, or None if the key
    is absent. FedoraOrigin/FedoraOriginWithRelease's own __post_init__
    validates the individual fields, so this doesn't reimplement that --
    _from_raw_dict translates the resulting ValueError into
    MalformedMetadataError."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise MalformedMetadataError(f"{package_name}: fedora must be an object")
    unrecognized = set(raw) - _RECOGNIZED_FEDORA_FIELDS
    if unrecognized:
        raise MalformedMetadataError(
            f"{package_name}: unrecognized fedora field(s): {sorted(unrecognized)}"
        )
    # A missing sub-key defaults to "" rather than None: every one of these
    # fields must be a non-empty str, and "" is rejected by the exact same
    # check a present-but-blank value would be (_check_present_and_non_empty),
    # so a missing key surfaces as the same violation, not a different one.
    if "release" in raw:
        return FedoraOriginWithRelease(
            git_url=raw.get("git_url", ""),
            branch=raw.get("branch", ""),
            sha=raw.get("sha", ""),
            release=raw.get("release", ""),
        )
    return FedoraOrigin(
        git_url=raw.get("git_url", ""), branch=raw.get("branch", ""), sha=raw.get("sha", "")
    )


def _parse_version_from_ref(package_name: str, raw: object) -> VersionFromRef | None:
    """Builds version_from_ref from raw JSON data, or None if the key is
    absent. Same reasoning as _parse_fedora."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise MalformedMetadataError(f"{package_name}: version_from_ref must be an object")
    unrecognized = set(raw) - _RECOGNIZED_VERSION_FROM_REF_FIELDS
    if unrecognized:
        raise MalformedMetadataError(
            f"{package_name}: unrecognized version_from_ref field(s): {sorted(unrecognized)}"
        )
    return VersionFromRef(type=raw.get("type", ""))


@dataclass(frozen=True)
class VersionFromRef:
    """Configures fixed-ref pin-refresh logic for computing a gorget
    source-pipeline's --version string automatically from a newly-pinned
    commit."""

    type: str

    def __post_init__(self) -> None:
        """Raises if type fails validation -- a frozen dataclass still runs
        __post_init__ once, at construction, before becoming immutable."""
        error = check_version_from_ref(self)
        if error is not None:
            raise ValueError(error.message)


@dataclass(frozen=True)
class FedoraOrigin:
    """Everything about the Fedora build a package tracks or last tracked,
    when there's no guarantee it's still Fedora's current release."""

    git_url: str
    branch: str
    sha: str

    def __post_init__(self) -> None:
        """Raises if git_url/branch/sha fail validation."""
        for field, value in (
            ("git_url", self.git_url),
            ("branch", self.branch),
            ("sha", self.sha),
        ):
            error = _check_present_and_non_empty(value, field=field)
            if error is not None:
                raise ValueError(error.message)


@dataclass(frozen=True)
class FedoraOriginWithRelease(FedoraOrigin):
    """FedoraOrigin plus release, for the two states where the package is
    still confirmed to ship exactly what this build shipped."""

    release: str

    def __post_init__(self) -> None:
        """Raises if git_url/branch/sha/release fail validation -- a frozen
        dataclass still runs __post_init__ once, at construction, before
        becoming immutable."""
        super().__post_init__()
        error = check_fedora_release(self.release)
        if error is not None:
            raise ValueError(error.message)


def _normalize_or_raise_cve_product(value: str | list[str] | None) -> str | list[str] | None:
    """Strict-write policy for cve_product: None means "not set," anything
    else must normalize cleanly or this raises. Used by __init__, matching
    the setter's own policy once it exists (section 8). A file loaded from
    disk with a genuinely messy cve_product never reaches this as a raw
    value -- _from_raw_dict pre-normalizes leniently before calling
    __init__, so lenient reads and this strict write don't contradict each
    other."""
    if value is None:
        return None
    normalized = normalize_cve_product(value)
    if normalized is None:
        raise ValueError(f"cve_product {value!r} doesn't normalize to a valid value")
    return normalized


def _normalize_or_raise_fix_status(value: int | None) -> int | None:
    """Strict-write policy for fix_status -- see
    _normalize_or_raise_cve_product, same reasoning."""
    if value is None:
        return None
    normalized = normalize_fix_status(value)
    if normalized is None:
        raise ValueError(f"fix_status {value!r} doesn't normalize to a valid value")
    return normalized


def _validate_version(version: str) -> None:
    """Shared by __init__ and every update() method -- version is a state
    field with no public setter (only transitions/update() ever change
    it), so this is the one place its validation logic lives instead of
    being reimplemented at each mutation site."""
    error = check_version(version)
    if error is not None:
        raise ValueError(error.message)


class _OnFedoraVersionMixin:
    """Shared by CleanPackageMetadata and ModifiedFedoraPackageMetadata:
    both track a complete FedoraOriginWithRelease and share the same
    "compare against fedora.release" bump_release() algorithm."""

    _fedora: FedoraOriginWithRelease
    _version: str
    _consumed: bool

    def _base_kwargs(self) -> dict[str, object]:
        raise NotImplementedError  # provided by PackageMetadata via MRO

    @property
    def fedora(self) -> FedoraOriginWithRelease:
        return self._fedora

    def update(self, *, fedora: FedoraOriginWithRelease, version: str) -> None:
        """Refreshes to a newer Fedora build without changing state -- the
        routine "Fedora shipped something new" case. Mutates in place,
        unlike transitions -- the class doesn't change."""
        _validate_version(version)
        self._fedora = fedora
        self._version = version

    def to_modified_upstream(
        self, *, version: str, reason: str
    ) -> ModifiedUpstreamPackageMetadata:
        """Keeps fedora.git_url/branch/sha as the pointer back to Fedora,
        drops fedora.release, sets version and a fresh reason."""
        pinned_fedora = FedoraOrigin(
            git_url=self._fedora.git_url, branch=self._fedora.branch, sha=self._fedora.sha
        )
        kwargs = self._base_kwargs()
        kwargs["version"] = version
        self._consumed = True
        return ModifiedUpstreamPackageMetadata(
            fedora=pinned_fedora, modification_reason=reason, **kwargs
        )

    def bump_release(self, current_release: str) -> str:
        """Computes the next Release: value by comparing current_release
        against self.fedora.release -- the base to compare against is this
        object's own state, not a second parameter (contrast
        _NoStoredReleaseMixin below, which has no base to compare against
        at all). Raises ReleaseMismatchError if current_release doesn't
        equal self.fedora.release and doesn't start with
        self.fedora.release + "." followed by an integer."""
        base = self._fedora.release
        if current_release == base:
            return f"{base}.1"
        match = re.fullmatch(rf"{re.escape(base)}\.(\d+)", current_release)
        if match is None:
            raise ReleaseMismatchError(
                f"current_release {current_release!r} doesn't start with fedora.release {base!r}"
            )
        return f"{base}.{int(match.group(1)) + 1}"


class _ModifiedMixin:
    """Shared by ModifiedFedoraPackageMetadata and
    ModifiedUpstreamPackageMetadata: both require modification_reason.
    Sharing this one field through a mixin rather than declaring it twice
    means the two classes can't structurally drift apart on how it's
    validated -- there's only one implementation to change."""

    _modification_reason: str

    @property
    def modification_reason(self) -> str:
        return self._modification_reason

    @modification_reason.setter
    def modification_reason(self, value: str) -> None:
        """Revises the reason text in place, without changing state.
        Raises if the value fails validation. Only reachable on the two
        classes that inherit this mixin -- there's no modification_reason
        property on CleanPackageMetadata/IndependentPackageMetadata to set
        in the first place."""
        error = check_modification_reason(value)
        if error is not None:
            raise ValueError(error.message)
        self._modification_reason = value


class _CanBecomeIndependentMixin:
    """Shared by CleanPackageMetadata, ModifiedFedoraPackageMetadata, and
    ModifiedUpstreamPackageMetadata: to_independent() is identical for all
    three. No tool performs this transition today; no field records why a
    package went independent -- that context, if any, lives outside
    metadata entirely (e.g. a commit message)."""

    _consumed: bool

    def _base_kwargs(self) -> dict[str, object]:
        raise NotImplementedError  # provided by PackageMetadata via MRO

    def to_independent(self) -> IndependentPackageMetadata:
        """Drops fedora entirely and modification_reason if present -- both
        happen automatically, because neither is a base-class field
        _base_kwargs() returns."""
        kwargs = self._base_kwargs()
        self._consumed = True
        return IndependentPackageMetadata(**kwargs)


class _NoStoredReleaseMixin:
    """Shared by ModifiedUpstreamPackageMetadata and
    IndependentPackageMetadata: neither has a stored release value to
    compare against, so both increment the spec's own current Release:
    directly. This is what makes the HUM-5182 bug class structurally
    impossible: nothing stored can disagree with the spec's actual value,
    because nothing is stored."""

    def bump_release(self, current_release: str) -> str:
        """Computes the next Release: value with no stored base. Raises
        ReleaseMismatchError if current_release isn't shaped 0.N (e.g. a
        bare "1", or anything not starting with "0.") -- these classes'
        release convention always starts at 0.1, so anything else means the
        spec itself is inconsistent with that convention, not a valid
        starting point to increment from."""
        match = _ZERO_DOT_N_RELEASE.fullmatch(current_release)
        if match is None:
            raise ReleaseMismatchError(
                f"current_release {current_release!r} isn't shaped 0.N"
            )
        return f"0.{int(match.group(1)) + 1}"


class PackageMetadata(abc.ABC):
    """A package's current version, and how upstream-version tracking and
    CVE analysis should treat it. State-specific data (Fedora origin,
    modification reason) lives on the concrete subclasses and the mixins
    they inherit from, not here."""

    def __init__(
        self,
        *,
        package_name: str,
        version: str,
        upstream_repo: str,
        upstream_branch: str | None = None,
        track_upstream: str | None = None,
        release_monitoring_project_id: int | None = None,
        version_source: str | None = None,
        tag_strip_prefix: str = _TAG_STRIP_PREFIX_DEFAULT,
        version_suffix_strip: str | None = None,
        upstream_version_transform: str | None = None,
        source_availability_check: str | None = None,
        version_from_ref: VersionFromRef | None = None,
        cve_product: str | list[str] | None = None,
        version_transform: str | None = None,
        fix_status: int | None = None,
    ) -> None:
        """Stores every base-class field, validating the same way the
        corresponding setter does."""
        self._consumed = False
        _validate_version(version)
        upstream_repo_error = check_upstream_repo(upstream_repo)
        if upstream_repo_error is not None:
            raise ValueError(upstream_repo_error.message)
        version_from_ref_error = check_version_from_ref(version_from_ref)
        if version_from_ref_error is not None:
            raise ValueError(version_from_ref_error.message)

        self._package_name = package_name
        self._version = version
        self._upstream_repo = upstream_repo
        self._upstream_branch = upstream_branch
        self._track_upstream = track_upstream
        self._release_monitoring_project_id = release_monitoring_project_id
        self._version_source = version_source
        self._tag_strip_prefix = tag_strip_prefix
        self._version_suffix_strip = version_suffix_strip
        self._upstream_version_transform = upstream_version_transform
        self._source_availability_check = source_availability_check
        self._version_from_ref = version_from_ref
        self._cve_product = _normalize_or_raise_cve_product(cve_product)
        self._version_transform = version_transform
        self._fix_status = _normalize_or_raise_fix_status(fix_status)
        self._cve_product_last_warned: str | list[str] | None = None
        self._fix_status_last_warned: int | None = None

    def __getattribute__(self, name: str) -> object:
        """Blocks all non-dunder attribute access once a transition has
        consumed this object (see StaleMetadataError), converting a
        forgotten-reassignment bug into an immediate failure at the next use
        rather than silent operation on stale data. Dunders are exempt so
        things like repr()/isinstance() still work for debugging and normal
        Python machinery."""
        if not name.startswith("__") and object.__getattribute__(self, "_consumed"):
            # Reads the raw stored field, not the package_name property --
            # the property's own body does self._package_name, which would
            # re-enter this same check and recurse forever.
            package_name = object.__getattribute__(self, "_package_name")
            raise StaleMetadataError(
                f"{package_name}: this object was already transitioned to a new state -- "
                "use the object the transition method returned instead"
            )
        return object.__getattribute__(self, name)

    def _base_kwargs(self) -> dict[str, object]:
        """Every base-class field's current value, packaged for handing to
        another concrete class's __init__ during a transition. Every field
        the base class owns is explicitly independent of state and carries
        over unchanged unless a transition overrides it -- this is also why
        to_independent() doesn't need any special-case code to drop fedora/
        modification_reason: neither is a base-class field, so neither is
        ever in here to carry over in the first place."""
        return {
            "package_name": self._package_name,
            "version": self._version,
            "upstream_repo": self._upstream_repo,
            "upstream_branch": self._upstream_branch,
            "track_upstream": self._track_upstream,
            "release_monitoring_project_id": self._release_monitoring_project_id,
            "version_source": self._version_source,
            "tag_strip_prefix": self._tag_strip_prefix,
            "version_suffix_strip": self._version_suffix_strip,
            "upstream_version_transform": self._upstream_version_transform,
            "source_availability_check": self._source_availability_check,
            "version_from_ref": self._version_from_ref,
            "cve_product": self._cve_product,
            "version_transform": self._version_transform,
            "fix_status": self._fix_status,
        }

    @classmethod
    def _from_raw_dict(cls, package_name: str, data: dict[str, object]) -> PackageMetadata:
        """Runs the load()-time structural checks against data to pick one
        of the four concrete classes, then calls that class's __init__ with
        the fields pulled out of data. Raises MalformedMetadataError on
        failure. The only place a raw dict becomes a PackageMetadata.

        Deliberately doesn't re-validate version/upstream_repo/
        version_from_ref/cve_product/fix_status ahead of time -- __init__
        (and FedoraOrigin/FedoraOriginWithRelease/VersionFromRef's own
        __post_init__) already do, so this lets those raise and translates
        the result into MalformedMetadataError, rather than checking the
        same things twice."""
        unrecognized = set(data) - _RECOGNIZED_FIELDS
        if unrecognized:
            raise MalformedMetadataError(
                f"{package_name}: unrecognized field(s): {sorted(unrecognized)}"
            )

        modification_status = data.get("modification_status")
        status_error = check_modification_status(modification_status)
        if status_error is not None:
            raise MalformedMetadataError(f"{package_name}: {status_error.message}")

        try:
            fedora = _parse_fedora(package_name, data.get("fedora"))
            version_from_ref = _parse_version_from_ref(package_name, data.get("version_from_ref"))
            base_kwargs: dict[str, object] = {
                "package_name": package_name,
                "version": data.get("version"),
                "upstream_repo": data.get("upstream_repo"),
                "upstream_branch": data.get("upstream_branch"),
                "track_upstream": data.get("track_upstream"),
                "release_monitoring_project_id": data.get("release_monitoring_project_id"),
                "version_source": data.get("version_source"),
                "tag_strip_prefix": data.get("tag_strip_prefix", _TAG_STRIP_PREFIX_DEFAULT),
                "version_suffix_strip": data.get("version_suffix_strip"),
                "upstream_version_transform": data.get("upstream_version_transform"),
                "source_availability_check": data.get("source_availability_check"),
                "version_from_ref": version_from_ref,
                # Pre-normalized leniently here, matching read semantics --
                # __init__'s own strict re-check then always sees an
                # already-clean value, so a messy legacy file still loads
                # instead of raising (section 8's read/write asymmetry).
                "cve_product": normalize_cve_product(data.get("cve_product")),
                "version_transform": data.get("version_transform"),
                "fix_status": normalize_fix_status(data.get("fix_status")),
            }

            if modification_status == ModificationStatus.INDEPENDENT:
                if fedora is not None:
                    raise MalformedMetadataError(
                        f"{package_name}: independent implies fedora is absent"
                    )
                return IndependentPackageMetadata(**base_kwargs)

            if fedora is None:
                raise MalformedMetadataError(
                    f"{package_name}: {modification_status} implies fedora is present"
                )

            if modification_status == ModificationStatus.CLEAN:
                if "modification_reason" in data:
                    raise MalformedMetadataError(
                        f"{package_name}: clean implies modification_reason is absent"
                    )
                if not isinstance(fedora, FedoraOriginWithRelease):
                    raise MalformedMetadataError(
                        f"{package_name}: clean implies fedora.release is present"
                    )
                return CleanPackageMetadata(fedora=fedora, **base_kwargs)

            # modification_status == ModificationStatus.MODIFIED. reason is
            # `object`, not `str`, straight out of a dict[str, object] --
            # the modification_reason setter these constructors call
            # validates it, same reasoning as **base_kwargs below.
            reason = data.get("modification_reason", "")
            if isinstance(fedora, FedoraOriginWithRelease):
                return ModifiedFedoraPackageMetadata(
                    fedora=fedora, modification_reason=reason, **base_kwargs  # type: ignore[arg-type]
                )
            return ModifiedUpstreamPackageMetadata(
                fedora=fedora, modification_reason=reason, **base_kwargs  # type: ignore[arg-type]
            )
        except ValueError as exc:
            raise MalformedMetadataError(f"{package_name}: {exc}") from exc

    @property
    def package_name(self) -> str:
        return self._package_name

    @property
    def version(self) -> str:
        return self._version

    @property
    @abc.abstractmethod
    def modification_status(self) -> ModificationStatus: ...

    @property
    def upstream_repo(self) -> str:
        return self._upstream_repo

    @upstream_repo.setter
    def upstream_repo(self, value: str) -> None:
        """Raises if the value is missing or empty."""
        error = check_upstream_repo(value)
        if error is not None:
            raise ValueError(error.message)
        self._upstream_repo = value

    @property
    def upstream_branch(self) -> str | None:
        return self._upstream_branch

    @upstream_branch.setter
    def upstream_branch(self, value: str | None) -> None:
        self._upstream_branch = value

    @property
    def track_upstream(self) -> str | None:
        return self._track_upstream

    @property
    def release_monitoring_project_id(self) -> int | None:
        return self._release_monitoring_project_id

    @property
    def version_source(self) -> str | None:
        return self._version_source

    @property
    def tag_strip_prefix(self) -> str:
        return self._tag_strip_prefix

    @property
    def version_suffix_strip(self) -> str | None:
        return self._version_suffix_strip

    @version_suffix_strip.setter
    def version_suffix_strip(self, value: str | None) -> None:
        self._version_suffix_strip = value

    @property
    def upstream_version_transform(self) -> str | None:
        return self._upstream_version_transform

    @upstream_version_transform.setter
    def upstream_version_transform(self, value: str | None) -> None:
        self._upstream_version_transform = value

    @property
    def source_availability_check(self) -> str | None:
        return self._source_availability_check

    @source_availability_check.setter
    def source_availability_check(self, value: str | None) -> None:
        self._source_availability_check = value

    @property
    def version_from_ref(self) -> VersionFromRef | None:
        return self._version_from_ref

    @version_from_ref.setter
    def version_from_ref(self, value: VersionFromRef | None) -> None:
        """Raises if value.type isn't a supported type."""
        error = check_version_from_ref(value)
        if error is not None:
            raise ValueError(error.message)
        self._version_from_ref = value

    @property
    def cve_product(self) -> str | list[str] | None:
        """Normalized via normalize_cve_product() -- malformed on-disk
        values are dropped with a warning, not raised. Warns only once per
        distinct bad value, not on every read."""
        normalized = normalize_cve_product(self._cve_product)
        if (
            self._cve_product is not None
            and normalized is None
            and self._cve_product != self._cve_product_last_warned
        ):
            logger.warning(
                "%s: cve_product %r doesn't normalize to a valid value, dropping it",
                self._package_name,
                self._cve_product,
            )
            self._cve_product_last_warned = self._cve_product
        return normalized

    @cve_product.setter
    def cve_product(self, value: str | list[str] | None) -> None:
        """Raises if value doesn't normalize cleanly -- new data is held to
        a stricter standard than what's already on disk."""
        self._cve_product = _normalize_or_raise_cve_product(value)

    @property
    def version_transform(self) -> str | None:
        return self._version_transform

    @version_transform.setter
    def version_transform(self, value: str | None) -> None:
        self._version_transform = value

    @property
    def fix_status(self) -> int | None:
        """Normalized via normalize_fix_status() -- malformed on-disk
        values are dropped with a warning, not raised. Warns only once per
        distinct bad value, not on every read."""
        normalized = normalize_fix_status(self._fix_status)
        if (
            self._fix_status is not None
            and normalized is None
            and self._fix_status != self._fix_status_last_warned
        ):
            logger.warning(
                "%s: fix_status %r doesn't normalize to a valid value, dropping it",
                self._package_name,
                self._fix_status,
            )
            self._fix_status_last_warned = self._fix_status
        return normalized

    @fix_status.setter
    def fix_status(self, value: int | None) -> None:
        """Raises if value doesn't normalize cleanly -- new data is held to
        a stricter standard than what's already on disk."""
        self._fix_status = _normalize_or_raise_fix_status(value)

    def enable_upstream_tracking(
        self, *, track_upstream: str, release_monitoring_project_id: int
    ) -> None:
        """Anitya-based upstream tracking. version_source stays absent."""
        _raise_on_violations(
            check_upstream_tracking_combination(
                track_upstream=track_upstream,
                release_monitoring_project_id=release_monitoring_project_id,
                version_source=None,
                tag_strip_prefix=_TAG_STRIP_PREFIX_DEFAULT,
            )
        )
        self._track_upstream = track_upstream
        self._release_monitoring_project_id = release_monitoring_project_id
        self._version_source = None
        self._tag_strip_prefix = _TAG_STRIP_PREFIX_DEFAULT

    def enable_upstream_tracking_gitlab_tags(
        self, *, track_upstream: str | None = None, tag_strip_prefix: str = _TAG_STRIP_PREFIX_DEFAULT
    ) -> None:
        """gitlab_tags-based upstream tracking. Sets version_source to
        "gitlab_tags"; release_monitoring_project_id stays absent.
        track_upstream is optional -- it's a version-prefix search filter in
        this mode, not an identifier, so omitting it is a perfectly valid
        configuration, not a partial one."""
        _raise_on_violations(
            check_upstream_tracking_combination(
                track_upstream=track_upstream,
                release_monitoring_project_id=None,
                version_source="gitlab_tags",
                tag_strip_prefix=tag_strip_prefix,
            )
        )
        self._track_upstream = track_upstream
        self._release_monitoring_project_id = None
        self._version_source = "gitlab_tags"
        self._tag_strip_prefix = tag_strip_prefix

    def disable_upstream_tracking(self) -> None:
        """Clears track_upstream, release_monitoring_project_id, version_source,
        and tag_strip_prefix at once -- the "not tracked" case. No validation
        call here: this exact combination (all-None, default tag_strip_prefix)
        can never violate check_upstream_tracking_combination's rules, so
        there's nothing for one to catch."""
        self._track_upstream = None
        self._release_monitoring_project_id = None
        self._version_source = None
        self._tag_strip_prefix = _TAG_STRIP_PREFIX_DEFAULT

    def validate(
        self,
        *,
        resolve_spec_version: Callable[[PackageMetadata], str] | None = None,
    ) -> ValidationResult:
        """Checks every applicable rule against this object's actual
        current data -- doesn't trust the class hierarchy, doesn't trust
        that data came in through _from_raw_dict, doesn't trust that a
        caller only ever mutated this object through this library's own
        methods. Only one check needs data this object doesn't have on its
        own: the spec's resolved Version: line (needs the spec file),
        conditional on the caller supplying resolve_spec_version. Every
        violation among the checks actually run is returned, not just the
        first."""
        violations: list[ValidationError] = []

        def add(error: ValidationError | None) -> None:
            if error is not None:
                violations.append(error)

        add(check_version(self.version))
        add(check_modification_status(self.modification_status))
        add(check_upstream_repo(self.upstream_repo))
        add(check_version_source(self.version_source))
        add(check_version_from_ref(self.version_from_ref))
        add(check_fedora_release(_fedora_release(self)))
        if hasattr(self, "modification_reason"):
            add(check_modification_reason(self.modification_reason))
        add(check_version_from_ref_requires_upstream_repo(self))
        violations.extend(
            check_upstream_tracking_combination(
                track_upstream=self.track_upstream,
                release_monitoring_project_id=self.release_monitoring_project_id,
                version_source=self.version_source,
                tag_strip_prefix=self.tag_strip_prefix,
            )
        )
        violations.extend(check_fedora_matches_state(self))
        violations.extend(check_modification_reason_matches_state(self))

        checked_spec_version = resolve_spec_version is not None
        if resolve_spec_version is not None:
            spec_version = resolve_spec_version(self)
            if spec_version != self.version:
                violations.append(
                    ValidationError(
                        message=(
                            f"the spec's Version: ({spec_version!r}) doesn't match "
                            f"metadata version ({self.version!r})"
                        )
                    )
                )

        return ValidationResult(
            checked_spec_version=checked_spec_version,
            violations=violations,
        )

    def ensure_clean(self) -> CleanPackageMetadata:
        """Narrows self down to CleanPackageMetadata, raising a clear error
        if it's actually something else. General-purpose, not tied to
        loading -- works equally well on a value pulled out of load_all()'s
        dict or returned from a transition."""
        if not isinstance(self, CleanPackageMetadata):
            raise InvalidStateTransitionError(
                f"{self.package_name} is {type(self).__name__}, not clean"
            )
        return self

    def ensure_modified_fedora(self) -> ModifiedFedoraPackageMetadata:
        """See ensure_clean."""
        if not isinstance(self, ModifiedFedoraPackageMetadata):
            raise InvalidStateTransitionError(
                f"{self.package_name} is {type(self).__name__}, not modified_fedora"
            )
        return self

    def ensure_modified_upstream(self) -> ModifiedUpstreamPackageMetadata:
        """See ensure_clean."""
        if not isinstance(self, ModifiedUpstreamPackageMetadata):
            raise InvalidStateTransitionError(
                f"{self.package_name} is {type(self).__name__}, not modified_upstream"
            )
        return self

    def ensure_independent(self) -> IndependentPackageMetadata:
        """See ensure_clean."""
        if not isinstance(self, IndependentPackageMetadata):
            raise InvalidStateTransitionError(
                f"{self.package_name} is {type(self).__name__}, not independent"
            )
        return self


class CleanPackageMetadata(PackageMetadata, _OnFedoraVersionMixin, _CanBecomeIndependentMixin):
    def __init__(self, *, fedora: FedoraOriginWithRelease, **base_kwargs: object) -> None:
        """fedora's own __post_init__ validates it; everything else goes to
        PackageMetadata.__init__."""
        super().__init__(**base_kwargs)  # type: ignore[arg-type]
        self._fedora = fedora

    @property
    def modification_status(self) -> ModificationStatus:
        return ModificationStatus.CLEAN

    def to_modified_fedora(self, *, reason: str) -> ModifiedFedoraPackageMetadata:
        """Sets modification_status = modified and modification_reason
        together; fedora is untouched."""
        fedora = self._fedora
        kwargs = self._base_kwargs()
        self._consumed = True
        return ModifiedFedoraPackageMetadata(fedora=fedora, modification_reason=reason, **kwargs)


class ModifiedFedoraPackageMetadata(
    PackageMetadata, _OnFedoraVersionMixin, _ModifiedMixin, _CanBecomeIndependentMixin
):
    def __init__(
        self, *, fedora: FedoraOriginWithRelease, modification_reason: str, **base_kwargs: object
    ) -> None:
        """modification_reason goes through check_modification_reason, the
        same function its setter uses -- by going through the setter
        itself, not a second copy of the same check."""
        super().__init__(**base_kwargs)  # type: ignore[arg-type]
        self._fedora = fedora
        self.modification_reason = modification_reason

    @property
    def modification_status(self) -> ModificationStatus:
        return ModificationStatus.MODIFIED

    def to_clean(self) -> CleanPackageMetadata:
        """No arguments -- the existing FedoraOriginWithRelease is already
        correct and unchanged. Clears modification_reason."""
        fedora = self._fedora
        kwargs = self._base_kwargs()
        self._consumed = True
        return CleanPackageMetadata(fedora=fedora, **kwargs)


class ModifiedUpstreamPackageMetadata(
    PackageMetadata, _ModifiedMixin, _CanBecomeIndependentMixin, _NoStoredReleaseMixin
):
    def __init__(
        self, *, fedora: FedoraOrigin, modification_reason: str, **base_kwargs: object
    ) -> None:
        super().__init__(**base_kwargs)  # type: ignore[arg-type]
        self._fedora = fedora
        self.modification_reason = modification_reason

    @property
    def modification_status(self) -> ModificationStatus:
        return ModificationStatus.MODIFIED

    @property
    def fedora(self) -> FedoraOrigin:
        # No release -- moved ahead of Fedora's version; a pinned sha
        # recording the divergence point, unchanged until a state
        # transition (documentation/design/metadata-library-design.md,
        # section 3).
        return self._fedora

    def update(self, *, version: str) -> None:
        """Refreshes to a newer upstream version without changing state.
        No fedora parameter -- fedora.sha stays pinned; only a transition
        (to_clean()/to_modified_fedora()) changes it."""
        _validate_version(version)
        self._version = version

    def to_clean(self, *, fedora: FedoraOriginWithRelease, version: str) -> CleanPackageMetadata:
        """Requires a fresh, complete FedoraOriginWithRelease -- this
        package's own fedora has no release to fall back on. Clears
        modification_reason."""
        kwargs = self._base_kwargs()
        kwargs["version"] = version
        self._consumed = True
        return CleanPackageMetadata(fedora=fedora, **kwargs)

    def to_modified_fedora(
        self, *, fedora: FedoraOriginWithRelease, reason: str
    ) -> ModifiedFedoraPackageMetadata:
        """Fedora caught up to the version we're shipping, but local
        patches remain -- lands back on Fedora's version while staying
        modified, rather than becoming clean. Requires a fresh
        FedoraOriginWithRelease (this package's own fedora has no release)
        and a fresh reason."""
        kwargs = self._base_kwargs()
        self._consumed = True
        return ModifiedFedoraPackageMetadata(fedora=fedora, modification_reason=reason, **kwargs)


class IndependentPackageMetadata(PackageMetadata, _NoStoredReleaseMixin):
    def __init__(self, **base_kwargs: object) -> None:
        super().__init__(**base_kwargs)  # type: ignore[arg-type]

    @property
    def modification_status(self) -> ModificationStatus:
        return ModificationStatus.INDEPENDENT

    def update(self, *, version: str) -> None:
        """Refreshes to a newer upstream version without changing state."""
        _validate_version(version)
        self._version = version

    def to_clean(self, *, fedora: FedoraOriginWithRelease, version: str) -> CleanPackageMetadata:
        """Requires a fresh, complete FedoraOriginWithRelease -- this
        package has no fedora at all to begin with."""
        kwargs = self._base_kwargs()
        kwargs["version"] = version
        self._consumed = True
        return CleanPackageMetadata(fedora=fedora, **kwargs)
