"""hum.meta: canonical metadata/<package>.json library.

See documentation/design/metadata-library-design.md, section 2, for the
full planned export list.
"""

from .model import (
    CleanPackageMetadata,
    FedoraOrigin,
    FedoraOriginWithRelease,
    IndependentPackageMetadata,
    InvalidMetadataError,
    InvalidStateTransitionError,
    MalformedMetadataError,
    MetadataError,
    ModificationStatus,
    ModifiedFedoraPackageMetadata,
    ModifiedUpstreamPackageMetadata,
    PackageMetadata,
    ReleaseMismatchError,
    StaleMetadataError,
    ValidationResult,
    VersionFromRef,
)
from .normalize import normalize_cve_product, normalize_fix_status
from .repository import MetadataRepository
from .spec import resolve_spec_version
from .validation import ValidationError

__all__ = [
    "CleanPackageMetadata",
    "FedoraOrigin",
    "FedoraOriginWithRelease",
    "IndependentPackageMetadata",
    "InvalidMetadataError",
    "InvalidStateTransitionError",
    "MalformedMetadataError",
    "MetadataError",
    "MetadataRepository",
    "ModificationStatus",
    "ModifiedFedoraPackageMetadata",
    "ModifiedUpstreamPackageMetadata",
    "PackageMetadata",
    "ReleaseMismatchError",
    "StaleMetadataError",
    "ValidationError",
    "ValidationResult",
    "VersionFromRef",
    "normalize_cve_product",
    "normalize_fix_status",
    "resolve_spec_version",
]
