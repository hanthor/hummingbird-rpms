"""Unit tests for hum.meta.validation.

Each check_* function is tested directly and in isolation here, per
documentation/design/metadata-library-design.md, section 10. The three
functions that take a whole "metadata" object are tested against simple
duck-typed stand-ins (not real PackageMetadata instances, which don't exist
yet) -- this is intentional, not a shortcut: those functions read
getattr/hasattr directly rather than checking isinstance, precisely so they
work against anything with the right shape, real or not.

Assertions check message *content* (substrings), never an ADR rule number --
ValidationError doesn't carry one, on purpose (see validation.py's module
docstring).
"""

from types import SimpleNamespace
from typing import cast

import pytest

from hum.meta.model import PackageMetadata, VersionFromRef
from hum.meta.validation import (
    ValidationError,
    check_fedora_matches_state,
    check_fedora_release,
    check_modification_reason,
    check_modification_reason_matches_state,
    check_modification_status,
    check_private_product,
    check_private_products,
    check_upstream_repo,
    check_upstream_tracking_combination,
    check_version,
    check_version_from_ref,
    check_version_from_ref_requires_upstream_repo,
    check_version_source,
)


def assert_invalid(result: ValidationError | None, *expected_substrings: str) -> None:
    """Narrows result to a ValidationError and checks its message contains
    every expected substring, without pinning down the exact wording."""
    assert isinstance(result, ValidationError)
    for substring in expected_substrings:
        assert substring in result.message


def assert_one_violation(violations: list[ValidationError], *expected_substrings: str) -> None:
    """Same as assert_invalid, for the functions that return a list."""
    assert len(violations) == 1
    assert_invalid(violations[0], *expected_substrings)


def messages(violations: list[ValidationError]) -> list[str]:
    return [v.message for v in violations]


@pytest.mark.parametrize("value", [None, "nodejs20", "example-product"])
def test_private_product_valid(value: object) -> None:
    assert check_private_product(value) is None


@pytest.mark.parametrize("value", ["", 1, True, [], {}])
def test_private_product_invalid(value: object) -> None:
    assert_invalid(check_private_product(value), "private_product", "non-empty")


def test_private_products_match_rpas() -> None:
    assert check_private_products({"hello": "example"}, {"example", "nodejs20"}) == []


def test_private_products_report_each_unknown_assignment() -> None:
    violations = check_private_products({"hello": "missing", "nodejs20-lts": "nodejs20"}, set())
    assert len(violations) == 2
    assert "hello" in violations[0].message
    assert "missing" in violations[0].message
    assert "nodejs20-lts" in violations[1].message


def fedora(**overrides: object) -> SimpleNamespace:
    """A stand-in for FedoraOrigin/FedoraOriginWithRelease."""
    defaults: dict[str, object] = {
        "git_url": "https://example.com/pkg.git",
        "branch": "rawhide",
        "sha": "abc123",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def metadata(**overrides: object) -> PackageMetadata:
    """A stand-in for PackageMetadata -- only the attributes actually
    exercised by validation.py's metadata-shaped checks are set by
    default; pass modification_reason=... / fedora=... to add them, because
    the real classes only have these attributes at all on some subclasses.

    Deliberately a SimpleNamespace, not a real CleanPackageMetadata/etc
    instance: several tests need states no real constructor allows (e.g.
    "clean" with no fedora at all), which is exactly the kind of thing
    check_fedora_matches_state and friends exist to catch regardless of
    whether it's a genuine instance. cast() tells mypy to treat this as a
    PackageMetadata for the functions under test, which read
    getattr/hasattr rather than checking isinstance anyway.
    """
    defaults: dict[str, object] = {
        "modification_status": "clean",
        "version": "1.0",
        "upstream_repo": "https://example.com/pkg",
        "version_from_ref": None,
    }
    defaults.update(overrides)
    return cast(PackageMetadata, SimpleNamespace(**defaults))


class TestCheckVersion:
    def test_valid(self) -> None:
        assert check_version("1.0") is None

    @pytest.mark.parametrize("value", [None, "", 1, 1.0, []])
    def test_invalid(self, value: object) -> None:
        assert_invalid(check_version(value), "version", "present and non-empty")


class TestCheckModificationStatus:
    @pytest.mark.parametrize("value", ["clean", "modified", "independent"])
    def test_valid(self, value: str) -> None:
        assert check_modification_status(value) is None

    def test_missing(self) -> None:
        assert_invalid(check_modification_status(None), "modification_status must be present")

    @pytest.mark.parametrize("value", ["bogus", ""])
    def test_invalid_value(self, value: object) -> None:
        assert_invalid(
            check_modification_status(value),
            "modification_status must be clean, modified, or independent",
        )

    @pytest.mark.parametrize("value", [1, [], {}])
    def test_non_string_value(self, value: object) -> None:
        assert_invalid(
            check_modification_status(value),
            "modification_status must be a string",
        )


class TestCheckUpstreamRepo:
    def test_valid(self) -> None:
        assert check_upstream_repo("https://example.com/pkg") is None

    @pytest.mark.parametrize("value", [None, "", 1])
    def test_invalid(self, value: object) -> None:
        assert_invalid(check_upstream_repo(value), "upstream_repo", "present and non-empty")


class TestCheckVersionSource:
    @pytest.mark.parametrize("value", [None, "gitlab_tags"])
    def test_valid(self, value: object) -> None:
        assert check_version_source(value) is None

    @pytest.mark.parametrize("value", ["anitya", "", "GITLAB_TAGS"])
    def test_invalid(self, value: object) -> None:
        assert_invalid(check_version_source(value), "version_source", "gitlab_tags")


class TestCheckModificationReason:
    def test_valid(self) -> None:
        assert check_modification_reason("Backport CVE fix") is None

    @pytest.mark.parametrize("value", [None, "", 1])
    def test_invalid(self, value: object) -> None:
        assert_invalid(
            check_modification_reason(value), "modification_reason", "present and non-empty"
        )


class TestCheckFedoraRelease:
    @pytest.mark.parametrize("value", [None, "3", "3.1", "0.1"])
    def test_valid(self, value: object) -> None:
        assert check_fedora_release(value) is None

    @pytest.mark.parametrize("value", ["3.fc42", "1.el9", "2.el9_4"])
    def test_invalid(self, value: str) -> None:
        assert_invalid(check_fedora_release(value), "dist-tag suffix")

    @pytest.mark.parametrize("value", [1, True, []])
    def test_non_string_rejected(self, value: object) -> None:
        assert_invalid(check_fedora_release(value), "must be a string")

    def test_empty_string_rejected(self) -> None:
        assert_invalid(check_fedora_release(""), "must not be empty")


class TestCheckVersionFromRef:
    def test_none_is_valid(self) -> None:
        assert check_version_from_ref(None) is None

    def test_commit_date_is_valid(self) -> None:
        assert check_version_from_ref(VersionFromRef(type="commit-date")) is None

    def test_other_type_is_invalid(self) -> None:
        # VersionFromRef self-validates at construction, so an invalid one
        # can't be built directly -- this stand-in simulates something that
        # bypassed that check (e.g. object.__setattr__ on a frozen
        # dataclass), which is exactly the case check_version_from_ref
        # exists to still catch independently.
        result = check_version_from_ref(SimpleNamespace(type="tag"))  # type: ignore[arg-type]
        assert_invalid(result, "version_from_ref.type must be commit-date")


class TestCheckUpstreamTrackingCombination:
    def test_not_tracked_is_valid(self) -> None:
        violations = check_upstream_tracking_combination(
            track_upstream=None,
            release_monitoring_project_id=None,
            version_source=None,
            tag_strip_prefix="v",
        )
        assert violations == []

    def test_anitya_tracking_is_valid(self) -> None:
        violations = check_upstream_tracking_combination(
            track_upstream="1.26",
            release_monitoring_project_id=12345,
            version_source=None,
            tag_strip_prefix="v",
        )
        assert violations == []

    def test_bool_track_upstream_is_invalid(self) -> None:
        violations = check_upstream_tracking_combination(
            track_upstream=True,  # type: ignore[arg-type]
            release_monitoring_project_id=12345,
            version_source=None,
            tag_strip_prefix="v",
        )
        assert_one_violation(violations, "track_upstream must be a str")

    def test_string_project_id_is_invalid(self) -> None:
        violations = check_upstream_tracking_combination(
            track_upstream="1.26",
            release_monitoring_project_id="12345",  # type: ignore[arg-type]
            version_source=None,
            tag_strip_prefix="v",
        )
        assert_one_violation(violations, "release_monitoring_project_id must be an int")

    def test_bool_project_id_is_invalid(self) -> None:
        # bool is an int subclass -- excluded explicitly, same reasoning as
        # normalize_fix_status's bool exclusion.
        violations = check_upstream_tracking_combination(
            track_upstream="1.26",
            release_monitoring_project_id=True,  # type: ignore[arg-type]
            version_source=None,
            tag_strip_prefix="v",
        )
        assert_one_violation(violations, "release_monitoring_project_id must be an int")

    def test_gitlab_tags_with_track_upstream_is_valid(self) -> None:
        violations = check_upstream_tracking_combination(
            track_upstream="20",
            release_monitoring_project_id=None,
            version_source="gitlab_tags",
            tag_strip_prefix="v",
        )
        assert violations == []

    def test_gitlab_tags_without_track_upstream_is_valid(self) -> None:
        violations = check_upstream_tracking_combination(
            track_upstream=None,
            release_monitoring_project_id=None,
            version_source="gitlab_tags",
            tag_strip_prefix="v",
        )
        assert violations == []

    def test_track_upstream_without_project_id_is_invalid(self) -> None:
        violations = check_upstream_tracking_combination(
            track_upstream="1.26",
            release_monitoring_project_id=None,
            version_source=None,
            tag_strip_prefix="v",
        )
        assert_one_violation(violations, "release_monitoring_project_id must be present")

    def test_project_id_without_track_upstream_is_invalid(self) -> None:
        violations = check_upstream_tracking_combination(
            track_upstream=None,
            release_monitoring_project_id=12345,
            version_source=None,
            tag_strip_prefix="v",
        )
        assert_one_violation(violations, "track_upstream must be present")

    def test_gitlab_tags_with_project_id_is_invalid(self) -> None:
        violations = check_upstream_tracking_combination(
            track_upstream="20",
            release_monitoring_project_id=12345,
            version_source="gitlab_tags",
            tag_strip_prefix="v",
        )
        assert_one_violation(violations, "release_monitoring_project_id must be absent")

    def test_custom_prefix_without_gitlab_tags_is_invalid(self) -> None:
        violations = check_upstream_tracking_combination(
            track_upstream=None,
            release_monitoring_project_id=None,
            version_source=None,
            tag_strip_prefix="release-",
        )
        assert_one_violation(violations, "tag_strip_prefix is customized")

    def test_multiple_violations_all_reported(self) -> None:
        # version_source="gitlab_tags" here satisfies the tag_strip_prefix
        # rule (it's customized *and* version_source is gitlab_tags), so
        # only the track_upstream/project_id violations fire.
        violations = check_upstream_tracking_combination(
            track_upstream=None,
            release_monitoring_project_id=12345,
            version_source="gitlab_tags",
            tag_strip_prefix="release-",
        )
        assert len(violations) == 2
        joined = " ".join(messages(violations))
        assert "track_upstream must be present" in joined
        assert "release_monitoring_project_id must be absent" in joined

    def test_multiple_violations_including_tag_strip_prefix(self) -> None:
        violations = check_upstream_tracking_combination(
            track_upstream=None,
            release_monitoring_project_id=12345,
            version_source=None,
            tag_strip_prefix="release-",
        )
        assert len(violations) == 2
        joined = " ".join(messages(violations))
        assert "track_upstream must be present" in joined
        assert "tag_strip_prefix is customized" in joined


class TestCheckFedoraMatchesState:
    def test_independent_without_fedora_is_valid(self) -> None:
        assert check_fedora_matches_state(metadata(modification_status="independent")) == []

    def test_independent_with_fedora_is_invalid(self) -> None:
        violations = check_fedora_matches_state(
            metadata(modification_status="independent", fedora=fedora())
        )
        assert_one_violation(violations, "fedora is absent")

    def test_clean_without_fedora_is_invalid(self) -> None:
        violations = check_fedora_matches_state(metadata(modification_status="clean"))
        assert_one_violation(violations, "clean implies fedora is present")

    def test_modified_without_fedora_is_invalid(self) -> None:
        violations = check_fedora_matches_state(metadata(modification_status="modified"))
        assert_one_violation(violations, "modified implies fedora is present")

    def test_clean_with_full_fedora_and_release_is_valid(self) -> None:
        violations = check_fedora_matches_state(
            metadata(modification_status="clean", fedora=fedora(release="3"))
        )
        assert violations == []

    def test_clean_without_release_is_invalid(self) -> None:
        violations = check_fedora_matches_state(
            metadata(modification_status="clean", fedora=fedora())
        )
        assert_one_violation(violations, "fedora.release is present")

    def test_modified_fedora_with_release_is_valid(self) -> None:
        violations = check_fedora_matches_state(
            metadata(modification_status="modified", fedora=fedora(release="3"))
        )
        assert violations == []

    def test_modified_upstream_without_release_is_valid(self) -> None:
        violations = check_fedora_matches_state(
            metadata(modification_status="modified", fedora=fedora())
        )
        assert violations == []

    def test_missing_fedora_subfield_is_invalid(self) -> None:
        violations = check_fedora_matches_state(
            metadata(modification_status="clean", fedora=fedora(release="3", sha=""))
        )
        assert_one_violation(violations, "fedora.sha must be present")


class TestCheckModificationReasonMatchesState:
    def test_modified_with_reason_is_valid(self) -> None:
        violations = check_modification_reason_matches_state(
            metadata(modification_status="modified", modification_reason="Backport fix")
        )
        assert violations == []

    def test_modified_without_reason_is_invalid(self) -> None:
        violations = check_modification_reason_matches_state(
            metadata(modification_status="modified")
        )
        assert_one_violation(violations, "modification_reason is present")

    def test_clean_without_reason_attribute_is_valid(self) -> None:
        violations = check_modification_reason_matches_state(metadata(modification_status="clean"))
        assert violations == []

    def test_clean_polluted_with_reason_is_invalid(self) -> None:
        # A real CleanPackageMetadata has no modification_reason attribute at
        # all -- this simulates a caller bypassing the API and setting one
        # anyway (design doc, section 3's rule-enforcement subsection).
        violations = check_modification_reason_matches_state(
            metadata(modification_status="clean", modification_reason="leftover text")
        )
        assert_one_violation(violations, "clean implies modification_reason is absent")


class TestCheckVersionFromRefRequiresUpstreamRepo:
    def test_no_version_from_ref_is_valid(self) -> None:
        assert check_version_from_ref_requires_upstream_repo(metadata()) is None

    def test_version_from_ref_with_upstream_repo_is_valid(self) -> None:
        result = check_version_from_ref_requires_upstream_repo(
            metadata(version_from_ref=VersionFromRef(type="commit-date"))
        )
        assert result is None

    def test_version_from_ref_without_upstream_repo_is_invalid(self) -> None:
        result = check_version_from_ref_requires_upstream_repo(
            metadata(version_from_ref=VersionFromRef(type="commit-date"), upstream_repo="")
        )
        assert_invalid(result, "upstream_repo must also be present")
