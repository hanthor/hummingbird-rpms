"""Unit tests for hum.meta.model.

Built incrementally alongside model.py. The class hierarchy is constructible,
all of PackageMetadata's setters/configuration methods work, all 10
transitions and the ensure_* helpers work, and so does bump_release(). Only
MetadataRepository.load() itself (not yet implemented) is missing --
_from_raw_dict(), the classifier it will call, is tested directly here
(documentation/design/metadata-library-design.md, section 10).
"""

import dataclasses
import logging
from types import SimpleNamespace

import pytest

from hum.meta.model import (
    CleanPackageMetadata,
    FedoraOrigin,
    FedoraOriginWithRelease,
    IndependentPackageMetadata,
    InvalidMetadataError,
    InvalidStateTransitionError,
    MalformedMetadataError,
    ModificationStatus,
    ModifiedFedoraPackageMetadata,
    ModifiedUpstreamPackageMetadata,
    PackageMetadata,
    ReleaseMismatchError,
    StaleMetadataError,
    VersionFromRef,
)

from .conftest import clean_fedora


def upstream_fedora(**overrides: object) -> FedoraOrigin:
    defaults: dict[str, object] = {
        "git_url": "https://src.fedoraproject.org/rpms/avahi.git",
        "branch": "rawhide",
        "sha": "195919c9f9a8dff0e996921afd8736df8f12715b",
    }
    defaults.update(overrides)
    return FedoraOrigin(**defaults)  # type: ignore[arg-type]


def base_kwargs(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "package_name": "bash",
        "version": "5.4.3",
        "upstream_repo": "https://git.savannah.gnu.org/git/bash.git",
    }
    defaults.update(overrides)
    return defaults


class TestModificationStatus:
    def test_values_match_on_disk_strings(self) -> None:
        assert ModificationStatus.CLEAN == "clean"
        assert ModificationStatus.MODIFIED == "modified"
        assert ModificationStatus.INDEPENDENT == "independent"

    def test_constructible_from_the_on_disk_string(self) -> None:
        assert ModificationStatus("clean") is ModificationStatus.CLEAN


class TestVersionFromRef:
    def test_construction(self) -> None:
        ref = VersionFromRef(type="commit-date")
        assert ref.type == "commit-date"

    def test_frozen(self) -> None:
        ref = VersionFromRef(type="commit-date")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ref.type = "tag"  # type: ignore[misc]

    def test_invalid_type_is_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="commit-date"):
            VersionFromRef(type="tag")


class TestFedoraOrigin:
    def test_construction(self) -> None:
        origin = FedoraOrigin(
            git_url="https://example.com/pkg.git", branch="rawhide", sha="abc123"
        )
        assert origin.git_url == "https://example.com/pkg.git"
        assert origin.branch == "rawhide"
        assert origin.sha == "abc123"

    def test_frozen(self) -> None:
        origin = FedoraOrigin(git_url="https://example.com/pkg.git", branch="rawhide", sha="abc123")
        with pytest.raises(dataclasses.FrozenInstanceError):
            origin.sha = "def456"  # type: ignore[misc]

    @pytest.mark.parametrize("field", ["git_url", "branch", "sha"])
    def test_empty_field_is_rejected_at_construction(self, field: str) -> None:
        kwargs = {"git_url": "https://example.com/pkg.git", "branch": "rawhide", "sha": "abc123"}
        kwargs[field] = ""
        with pytest.raises(ValueError, match=field):
            FedoraOrigin(**kwargs)


class TestFedoraOriginWithRelease:
    def test_construction(self) -> None:
        origin = FedoraOriginWithRelease(
            git_url="https://example.com/pkg.git", branch="rawhide", sha="abc123", release="3"
        )
        assert origin.release == "3"

    def test_is_a_fedora_origin(self) -> None:
        origin = FedoraOriginWithRelease(
            git_url="https://example.com/pkg.git", branch="rawhide", sha="abc123", release="3"
        )
        assert isinstance(origin, FedoraOrigin)

    def test_plain_fedora_origin_is_not_a_with_release(self) -> None:
        origin = FedoraOrigin(git_url="https://example.com/pkg.git", branch="rawhide", sha="abc123")
        assert not isinstance(origin, FedoraOriginWithRelease)

    def test_dist_tag_suffix_is_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="dist-tag suffix"):
            FedoraOriginWithRelease(
                git_url="https://example.com/pkg.git",
                branch="rawhide",
                sha="abc123",
                release="3.fc42",
            )

    @pytest.mark.parametrize("release", ["16.p2", "1.git359ab16", "60.20260512cvs", "0.63.20260714gitafff0cf"])
    def test_alpha_release_values_are_accepted(self, release: str) -> None:
        origin = FedoraOriginWithRelease(
            git_url="https://example.com/pkg.git", branch="rawhide", sha="abc123", release=release
        )
        assert origin.release == release

    @pytest.mark.parametrize("release", ["3.el9", "3.el9_4", "3.eln140"])
    def test_other_dist_tag_suffixes_are_rejected(self, release: str) -> None:
        with pytest.raises(ValueError, match="dist-tag suffix"):
            FedoraOriginWithRelease(
                git_url="https://example.com/pkg.git", branch="rawhide", sha="abc123", release=release
            )

    def test_inherited_field_validation_still_runs(self) -> None:
        # Dataclasses don't chain __post_init__ across inheritance
        # automatically -- this confirms FedoraOriginWithRelease.__post_init__
        # actually calls super().__post_init__() rather than silently
        # skipping FedoraOrigin's own git_url/branch/sha checks.
        with pytest.raises(ValueError, match="git_url"):
            FedoraOriginWithRelease(git_url="", branch="rawhide", sha="abc123", release="3")


class TestPackageMetadataIsAbstract:
    def test_cannot_be_instantiated_directly(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            PackageMetadata(**base_kwargs())  # type: ignore[abstract,arg-type]


class TestPackageMetadataBaseFields:
    def test_getters_reflect_constructor_values(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        assert metadata.package_name == "bash"
        assert metadata.version == "5.4.3"
        assert metadata.upstream_repo == "https://git.savannah.gnu.org/git/bash.git"
        assert metadata.upstream_branch is None
        assert metadata.tag_strip_prefix == "v"
        assert metadata.private_product is None

    def test_empty_version_is_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="version"):
            CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs(version=""))

    def test_empty_upstream_repo_is_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="upstream_repo"):
            CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs(upstream_repo=""))

    def test_invalid_version_from_ref_is_rejected_at_construction(self) -> None:
        # VersionFromRef self-validates, so this simulates something that
        # bypassed that check reaching __init__ directly -- same reasoning
        # as validation.py's own tests for this defense-in-depth case.
        from types import SimpleNamespace

        with pytest.raises(ValueError, match="commit-date"):
            CleanPackageMetadata(
                fedora=clean_fedora(),
                **base_kwargs(version_from_ref=SimpleNamespace(type="tag")),
            )


class TestPrivateProduct:
    @pytest.mark.parametrize("state", ["clean", "modified-fedora", "modified-upstream", "independent"])
    def test_load_and_round_trip_in_every_state(self, state: str) -> None:
        data: dict[str, object] = {
            "version": "1.0", "upstream_repo": "https://example.com/project",
            "modification_status": state.split("-")[0], "private_product": "nodejs20",
        }
        if state != "independent":
            fedora = {"git_url": "https://example.com/fedora", "branch": "rawhide", "sha": "abc"}
            if state != "modified-upstream":
                fedora["release"] = "1"
            data["fedora"] = fedora
        if state.startswith("modified"):
            data["modification_reason"] = "Local changes"
        metadata = PackageMetadata._from_raw_dict("project", data)
        assert metadata.private_product == "nodejs20"
        assert metadata.to_dict()["private_product"] == "nodejs20"
        assert metadata.validate().violations == []

    def test_set_and_clear(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        metadata.private_product = "example"
        assert metadata.private_product == "example"
        metadata.private_product = None
        assert "private_product" not in metadata.to_dict()

    @pytest.mark.parametrize("value", ["", 123, False, [], {}])
    def test_constructor_and_setter_reject_invalid_values(self, value: object) -> None:
        with pytest.raises(ValueError, match="private_product"):
            IndependentPackageMetadata(**base_kwargs(private_product=value))
        metadata = IndependentPackageMetadata(**base_kwargs(private_product="example"))
        with pytest.raises(ValueError, match="private_product"):
            metadata.private_product = value  # type: ignore[assignment]
        assert metadata.private_product == "example"

    @pytest.mark.parametrize("value", [None, "", 123, False, [], {}])
    def test_invalid_json_field_is_not_silently_dropped(self, value: object) -> None:
        data = {
            "version": "1.0", "upstream_repo": "https://example.com/project",
            "modification_status": "independent", "private_product": value,
        }
        with pytest.raises(MalformedMetadataError, match="private_product"):
            PackageMetadata._from_raw_dict("project", data)

    def test_validate_detects_invalid_stored_value(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        metadata._private_product = ""  # simulate mutation bypassing the setter
        assert any("private_product" in v.message for v in metadata.validate().violations)

    def test_transitions_preserve_product(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs(private_product="example"))
        modified = metadata.to_modified_fedora(reason="Backport")
        upstream = modified.to_modified_upstream(version="6", reason="Upstream release")
        independent = upstream.to_independent()
        assert independent.private_product == "example"
        clean = independent.to_clean(fedora=clean_fedora(), version="5.4.3")
        assert clean.private_product == "example"


class TestCveProductAndFixStatus:
    def test_valid_cve_product_round_trips(self) -> None:
        metadata = CleanPackageMetadata(
            fedora=clean_fedora(), **base_kwargs(cve_product="bash")
        )
        assert metadata.cve_product == "bash"

    def test_none_cve_product_is_valid(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        assert metadata.cve_product is None

    def test_non_normalizable_cve_product_is_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="cve_product"):
            CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs(cve_product=123))

    def test_valid_fix_status_round_trips(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs(fix_status=1))
        assert metadata.fix_status == 1

    def test_non_normalizable_fix_status_is_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="fix_status"):
            CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs(fix_status=2))

    def test_getter_is_lenient_against_pollution(self, caplog: pytest.LogCaptureFixture) -> None:
        # __init__ is strict, so this simulates a value that reached
        # storage some other way (e.g. _from_raw_dict, in a later slice,
        # or direct attribute manipulation) -- the getter still has to
        # cope leniently rather than raising on every read.
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        metadata._cve_product = 123  # type: ignore[assignment]
        with caplog.at_level(logging.WARNING):
            assert metadata.cve_product is None
        assert "cve_product" in caplog.text

    def test_pollution_warning_fires_once_per_distinct_bad_value(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        metadata._cve_product = 123  # type: ignore[assignment]
        with caplog.at_level(logging.WARNING):
            assert metadata.cve_product is None
            assert metadata.cve_product is None
        assert len(caplog.records) == 1

    def test_pollution_warning_fires_again_for_a_different_bad_value(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        metadata._cve_product = 123  # type: ignore[assignment]
        with caplog.at_level(logging.WARNING):
            assert metadata.cve_product is None
            metadata._cve_product = 456  # type: ignore[assignment]
            assert metadata.cve_product is None
        assert len(caplog.records) == 2

    def test_fix_status_pollution_warning_fires_once_per_distinct_bad_value(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        metadata._fix_status = 2  # type: ignore[assignment]
        with caplog.at_level(logging.WARNING):
            assert metadata.fix_status is None
            assert metadata.fix_status is None
        assert len(caplog.records) == 1


class TestCleanPackageMetadata:
    def test_modification_status(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        assert metadata.modification_status is ModificationStatus.CLEAN

    def test_is_a_package_metadata(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        assert isinstance(metadata, PackageMetadata)

    def test_fedora_round_trips(self) -> None:
        fedora = clean_fedora()
        metadata = CleanPackageMetadata(fedora=fedora, **base_kwargs())
        assert metadata.fedora is fedora

    def test_update_mutates_in_place(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        new_fedora = clean_fedora(sha="ffffffffffffffffffffffffffffffffffffffff", release="4")
        metadata.update(fedora=new_fedora, version="5.4.4")
        assert metadata.fedora is new_fedora
        assert metadata.version == "5.4.4"
        assert metadata.modification_status is ModificationStatus.CLEAN

    def test_update_rejects_empty_version(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        with pytest.raises(ValueError, match="version"):
            metadata.update(fedora=clean_fedora(), version="")

    def test_no_modification_reason_attribute(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        assert not hasattr(metadata, "modification_reason")


class TestModifiedFedoraPackageMetadata:
    def test_modification_status(self) -> None:
        metadata = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(), modification_reason="Backport CVE fix", **base_kwargs()
        )
        assert metadata.modification_status is ModificationStatus.MODIFIED

    def test_fedora_and_reason_round_trip(self) -> None:
        fedora = clean_fedora()
        metadata = ModifiedFedoraPackageMetadata(
            fedora=fedora, modification_reason="Backport CVE fix", **base_kwargs()
        )
        assert metadata.fedora is fedora
        assert metadata.modification_reason == "Backport CVE fix"

    def test_empty_reason_is_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="modification_reason"):
            ModifiedFedoraPackageMetadata(
                fedora=clean_fedora(), modification_reason="", **base_kwargs()
            )

    def test_modification_reason_setter_revises_in_place(self) -> None:
        metadata = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(), modification_reason="Old reason", **base_kwargs()
        )
        metadata.modification_reason = "New reason"
        assert metadata.modification_reason == "New reason"

    def test_modification_reason_setter_rejects_empty(self) -> None:
        metadata = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(), modification_reason="Old reason", **base_kwargs()
        )
        with pytest.raises(ValueError, match="modification_reason"):
            metadata.modification_reason = ""


class TestModifiedUpstreamPackageMetadata:
    def test_modification_status(self) -> None:
        metadata = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(), modification_reason="Ahead of Fedora", **base_kwargs()
        )
        assert metadata.modification_status is ModificationStatus.MODIFIED

    def test_fedora_has_no_release(self) -> None:
        fedora = upstream_fedora()
        metadata = ModifiedUpstreamPackageMetadata(
            fedora=fedora, modification_reason="Ahead of Fedora", **base_kwargs()
        )
        assert metadata.fedora is fedora
        assert not isinstance(metadata.fedora, FedoraOriginWithRelease)

    def test_rejects_fedora_origin_with_release(self) -> None:
        fedora_with_release = FedoraOriginWithRelease(
            git_url="https://src.fedoraproject.org/rpms/avahi.git",
            branch="rawhide",
            sha="abc123",
            release="3",
        )
        with pytest.raises(ValueError, match="round-trip as modified-fedora"):
            ModifiedUpstreamPackageMetadata(
                fedora=fedora_with_release,
                modification_reason="Ahead of Fedora",
                **base_kwargs(),
            )

    def test_update_changes_version_without_touching_fedora(self) -> None:
        fedora = upstream_fedora()
        metadata = ModifiedUpstreamPackageMetadata(
            fedora=fedora, modification_reason="Ahead of Fedora", **base_kwargs(version="0.9")
        )
        metadata.update(version="0.9.1")
        assert metadata.version == "0.9.1"
        assert metadata.fedora is fedora  # pinned -- unchanged by update()


class TestIndependentPackageMetadata:
    def test_modification_status(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        assert metadata.modification_status is ModificationStatus.INDEPENDENT

    def test_no_fedora_attribute(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        assert not hasattr(metadata, "fedora")

    def test_no_modification_reason_attribute(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        assert not hasattr(metadata, "modification_reason")

    def test_update_changes_version(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        metadata.update(version="2.0.0")
        assert metadata.version == "2.0.0"


class TestUpstreamTrackingConfiguration:
    def test_enable_upstream_tracking(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        metadata.enable_upstream_tracking(track_upstream="1.26", release_monitoring_project_id=12345)
        assert metadata.track_upstream == "1.26"
        assert metadata.release_monitoring_project_id == 12345
        assert metadata.version_source is None
        assert metadata.tag_strip_prefix == "v"

    def test_enable_upstream_tracking_rejects_invalid_combination(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        with pytest.raises(ValueError, match="release_monitoring_project_id"):
            metadata.enable_upstream_tracking(
                track_upstream="1.26", release_monitoring_project_id=None  # type: ignore[arg-type]
            )

    def test_enable_upstream_tracking_rejects_string_project_id(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        with pytest.raises(ValueError, match="release_monitoring_project_id must be an int"):
            metadata.enable_upstream_tracking(
                track_upstream="1.26", release_monitoring_project_id="12345"  # type: ignore[arg-type]
            )

    def test_enable_upstream_tracking_gitlab_tags_rejects_bool_track_upstream(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        with pytest.raises(ValueError, match="track_upstream must be a str"):
            metadata.enable_upstream_tracking_gitlab_tags(track_upstream=True)  # type: ignore[arg-type]

    def test_enable_upstream_tracking_gitlab_tags_with_track_upstream(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        metadata.enable_upstream_tracking_gitlab_tags(track_upstream="20")
        assert metadata.track_upstream == "20"
        assert metadata.version_source == "gitlab_tags"
        assert metadata.release_monitoring_project_id is None
        assert metadata.tag_strip_prefix == "v"

    def test_enable_upstream_tracking_gitlab_tags_without_track_upstream(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        metadata.enable_upstream_tracking_gitlab_tags()
        assert metadata.track_upstream is None
        assert metadata.version_source == "gitlab_tags"

    def test_enable_upstream_tracking_gitlab_tags_custom_prefix(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        metadata.enable_upstream_tracking_gitlab_tags(tag_strip_prefix="release-")
        assert metadata.tag_strip_prefix == "release-"

    def test_disable_upstream_tracking_clears_everything(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        metadata.enable_upstream_tracking(track_upstream="1.26", release_monitoring_project_id=12345)
        metadata.disable_upstream_tracking()
        assert metadata.track_upstream is None
        assert metadata.release_monitoring_project_id is None
        assert metadata.version_source is None
        assert metadata.tag_strip_prefix == "v"

    def test_switching_configurations_leaves_no_stale_fields(self) -> None:
        # Anitya tracking sets release_monitoring_project_id; switching
        # straight to gitlab_tags must not leave it behind uncleared.
        metadata = IndependentPackageMetadata(**base_kwargs())
        metadata.enable_upstream_tracking(track_upstream="1.26", release_monitoring_project_id=12345)
        metadata.enable_upstream_tracking_gitlab_tags(track_upstream="20")
        assert metadata.release_monitoring_project_id is None


class TestPlainSetters:
    def test_upstream_repo_setter(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        metadata.upstream_repo = "https://github.com/example/bash"
        assert metadata.upstream_repo == "https://github.com/example/bash"

    def test_upstream_repo_setter_rejects_empty(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        with pytest.raises(ValueError, match="upstream_repo"):
            metadata.upstream_repo = ""

    def test_version_from_ref_setter(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        ref = VersionFromRef(type="commit-date")
        metadata.version_from_ref = ref
        assert metadata.version_from_ref is ref

    def test_version_from_ref_setter_accepts_none(self) -> None:
        metadata = CleanPackageMetadata(
            fedora=clean_fedora(), **base_kwargs(version_from_ref=VersionFromRef(type="commit-date"))
        )
        metadata.version_from_ref = None
        assert metadata.version_from_ref is None

    def test_version_from_ref_setter_rejects_invalid_type(self) -> None:
        # A real VersionFromRef can never carry an invalid type -- its own
        # __post_init__ already rejects that -- so this simulates a
        # duck-typed stand-in reaching the setter some other way, the same
        # reasoning as the cve_product/fedora pollution tests elsewhere.
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        with pytest.raises(ValueError, match="commit-date"):
            metadata.version_from_ref = SimpleNamespace(type="bogus")  # type: ignore[assignment]

    @pytest.mark.parametrize(
        ("attribute", "value"),
        [
            ("upstream_branch", "f42"),
            ("version_suffix_strip", "-stable"),
            ("upstream_version_transform", "strip-v-prefix"),
            ("source_availability_check", "github-releases"),
            ("version_transform", "some-transform"),
        ],
    )
    def test_plain_setters_round_trip(self, attribute: str, value: str) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        setattr(metadata, attribute, value)
        assert getattr(metadata, attribute) == value

    def test_cve_product_setter_valid(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        metadata.cve_product = "bash"
        assert metadata.cve_product == "bash"

    def test_cve_product_setter_rejects_garbage(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        with pytest.raises(ValueError, match="cve_product"):
            metadata.cve_product = 123  # type: ignore[assignment]

    def test_fix_status_setter_valid(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        metadata.fix_status = 1
        assert metadata.fix_status == 1

    def test_fix_status_setter_rejects_garbage(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        with pytest.raises(ValueError, match="fix_status"):
            metadata.fix_status = 2


class TestTransitionsReturnNewObjectsAndPreserveBaseFields:
    def test_transitions_never_mutate_the_source(self) -> None:
        source = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        result = source.to_modified_fedora(reason="Backport CVE fix")
        assert result is not source
        # source itself becomes unreadable after the transition -- see
        # TestStaleObjectAfterTransition -- so there's nothing further to
        # assert about its own state here.

    def test_base_fields_carry_over_unchanged(self) -> None:
        source = CleanPackageMetadata(
            fedora=clean_fedora(),
            **base_kwargs(track_upstream=None, cve_product="bash", fix_status=1),
        )
        source.enable_upstream_tracking_gitlab_tags(track_upstream="20")
        result = source.to_independent()
        assert result.package_name == "bash"
        assert result.upstream_repo == "https://git.savannah.gnu.org/git/bash.git"
        assert result.track_upstream == "20"
        assert result.version_source == "gitlab_tags"
        assert result.cve_product == "bash"
        assert result.fix_status == 1


class TestCleanToModifiedFedora:
    def test_transition(self) -> None:
        fedora = clean_fedora()
        source = CleanPackageMetadata(fedora=fedora, **base_kwargs(version="5.4.3"))
        result = source.to_modified_fedora(reason="Backport CVE fix")
        assert isinstance(result, ModifiedFedoraPackageMetadata)
        assert result.modification_reason == "Backport CVE fix"
        assert result.fedora is fedora
        assert result.version == "5.4.3"


class TestCleanToModifiedUpstream:
    def test_transition(self) -> None:
        fedora = clean_fedora()
        source = CleanPackageMetadata(fedora=fedora, **base_kwargs(version="5.4.3"))
        result = source.to_modified_upstream(version="5.5.0", reason="Security fix upstream")
        assert isinstance(result, ModifiedUpstreamPackageMetadata)
        assert result.version == "5.5.0"
        assert result.modification_reason == "Security fix upstream"
        assert result.fedora.git_url == fedora.git_url
        assert result.fedora.branch == fedora.branch
        assert result.fedora.sha == fedora.sha
        assert not isinstance(result.fedora, FedoraOriginWithRelease)


class TestCleanToIndependent:
    def test_transition(self) -> None:
        source = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        result = source.to_independent()
        assert isinstance(result, IndependentPackageMetadata)
        assert not hasattr(result, "fedora")
        assert not hasattr(result, "modification_reason")


class TestModifiedFedoraToClean:
    def test_transition(self) -> None:
        fedora = clean_fedora()
        source = ModifiedFedoraPackageMetadata(
            fedora=fedora, modification_reason="Backport CVE fix", **base_kwargs()
        )
        result = source.to_clean()
        assert isinstance(result, CleanPackageMetadata)
        assert result.fedora is fedora
        assert not hasattr(result, "modification_reason")


class TestModifiedFedoraToModifiedUpstream:
    def test_transition(self) -> None:
        source = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(),
            modification_reason="Backport CVE fix",
            **base_kwargs(version="5.4.3"),
        )
        result = source.to_modified_upstream(version="5.5.0", reason="Now tracking upstream")
        assert isinstance(result, ModifiedUpstreamPackageMetadata)
        assert result.version == "5.5.0"
        assert result.modification_reason == "Now tracking upstream"


class TestModifiedFedoraToIndependent:
    def test_transition(self) -> None:
        source = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(), modification_reason="Backport CVE fix", **base_kwargs()
        )
        result = source.to_independent()
        assert isinstance(result, IndependentPackageMetadata)


class TestModifiedUpstreamToClean:
    def test_transition(self) -> None:
        source = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(),
            modification_reason="Ahead of Fedora",
            **base_kwargs(version="0.9"),
        )
        new_fedora = clean_fedora()
        result = source.to_clean(fedora=new_fedora, version="0.9")
        assert isinstance(result, CleanPackageMetadata)
        assert result.fedora is new_fedora
        assert result.version == "0.9"
        assert not hasattr(result, "modification_reason")


class TestModifiedUpstreamToModifiedFedora:
    def test_transition(self) -> None:
        source = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(),
            modification_reason="Ahead of Fedora",
            **base_kwargs(version="0.9"),
        )
        new_fedora = clean_fedora(release="1")
        result = source.to_modified_fedora(fedora=new_fedora, reason="Still carrying local patch")
        assert isinstance(result, ModifiedFedoraPackageMetadata)
        assert result.fedora is new_fedora
        assert result.modification_reason == "Still carrying local patch"
        # Fedora caught up to the version we're already shipping -- version
        # itself doesn't change as part of this transition.
        assert result.version == "0.9"


class TestModifiedUpstreamToIndependent:
    def test_transition(self) -> None:
        source = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(), modification_reason="Ahead of Fedora", **base_kwargs()
        )
        result = source.to_independent()
        assert isinstance(result, IndependentPackageMetadata)


class TestIndependentToClean:
    def test_transition(self) -> None:
        source = IndependentPackageMetadata(**base_kwargs(version="1.0.0"))
        new_fedora = clean_fedora()
        result = source.to_clean(fedora=new_fedora, version="1.0.0")
        assert isinstance(result, CleanPackageMetadata)
        assert result.fedora is new_fedora


class TestStaleObjectAfterTransition:
    """Every transition consumes its source object -- using it afterwards
    (instead of the object the transition returned) raises StaleMetadataError
    immediately, rather than silently operating on stale data."""

    def _assert_source_is_stale(self, source: PackageMetadata) -> None:
        with pytest.raises(StaleMetadataError, match="already transitioned"):
            source.version
        with pytest.raises(StaleMetadataError, match="already transitioned"):
            source.modification_status

    def test_clean_to_modified_fedora(self) -> None:
        source = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        source.to_modified_fedora(reason="Backport CVE fix")
        self._assert_source_is_stale(source)

    def test_clean_to_modified_upstream(self) -> None:
        source = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        source.to_modified_upstream(version="5.5.0", reason="Security fix upstream")
        self._assert_source_is_stale(source)

    def test_clean_to_independent(self) -> None:
        source = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        source.to_independent()
        self._assert_source_is_stale(source)

    def test_modified_fedora_to_clean(self) -> None:
        source = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(), modification_reason="Backport CVE fix", **base_kwargs()
        )
        source.to_clean()
        self._assert_source_is_stale(source)

    def test_modified_fedora_to_modified_upstream(self) -> None:
        source = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(), modification_reason="Backport CVE fix", **base_kwargs()
        )
        source.to_modified_upstream(version="5.5.0", reason="Now tracking upstream")
        self._assert_source_is_stale(source)

    def test_modified_fedora_to_independent(self) -> None:
        source = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(), modification_reason="Backport CVE fix", **base_kwargs()
        )
        source.to_independent()
        self._assert_source_is_stale(source)

    def test_modified_upstream_to_clean(self) -> None:
        source = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(), modification_reason="Ahead of Fedora", **base_kwargs()
        )
        source.to_clean(fedora=clean_fedora(), version="5.4.3")
        self._assert_source_is_stale(source)

    def test_modified_upstream_to_modified_fedora(self) -> None:
        source = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(), modification_reason="Ahead of Fedora", **base_kwargs()
        )
        source.to_modified_fedora(fedora=clean_fedora(), reason="Still carrying local patch")
        self._assert_source_is_stale(source)

    def test_modified_upstream_to_independent(self) -> None:
        source = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(), modification_reason="Ahead of Fedora", **base_kwargs()
        )
        source.to_independent()
        self._assert_source_is_stale(source)

    def test_independent_to_clean(self) -> None:
        source = IndependentPackageMetadata(**base_kwargs())
        source.to_clean(fedora=clean_fedora(), version="5.4.3")
        self._assert_source_is_stale(source)

    def test_dunders_still_work_on_a_stale_object(self) -> None:
        source = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        source.to_modified_fedora(reason="Backport CVE fix")
        assert isinstance(source, CleanPackageMetadata)  # doesn't raise
        repr(source)  # doesn't raise

    def test_the_returned_object_is_not_itself_stale(self) -> None:
        source = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        result = source.to_modified_fedora(reason="Backport CVE fix")
        assert result.version == "5.4.3"
        assert result.modification_status is ModificationStatus.MODIFIED

    def test_update_does_not_consume(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        metadata.update(fedora=clean_fedora(release="4"), version="5.4.4")
        assert metadata.version == "5.4.4"


class TestSourceRemainsUsableAfterFailedTransition:
    """If the new object's constructor raises, the transition itself raises
    too -- the source must not be marked consumed in that case, since no new
    object was actually produced for the caller to switch to. Each transition
    method must construct the new object before setting self._consumed,
    not after."""

    def _assert_source_is_still_usable(self, source: PackageMetadata) -> None:
        source.version  # doesn't raise StaleMetadataError

    def test_clean_to_modified_fedora(self) -> None:
        source = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        with pytest.raises(ValueError, match="modification_reason"):
            source.to_modified_fedora(reason="")
        self._assert_source_is_still_usable(source)

    def test_clean_to_modified_upstream(self) -> None:
        source = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        with pytest.raises(ValueError, match="modification_reason"):
            source.to_modified_upstream(version="5.5.0", reason="")
        self._assert_source_is_still_usable(source)

    def test_clean_to_independent(self) -> None:
        # to_independent() takes no caller-supplied arguments, so there's no
        # natural bad-argument failure -- simulate one the same way the
        # attribute-pollution tests elsewhere in this file do.
        source = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        source._version = ""
        with pytest.raises(ValueError, match="version"):
            source.to_independent()
        self._assert_source_is_still_usable(source)

    def test_modified_fedora_to_clean(self) -> None:
        # to_clean() also takes no arguments here -- same pollution approach.
        source = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(), modification_reason="Backport CVE fix", **base_kwargs()
        )
        source._version = ""
        with pytest.raises(ValueError, match="version"):
            source.to_clean()
        self._assert_source_is_still_usable(source)

    def test_modified_fedora_to_modified_upstream(self) -> None:
        source = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(), modification_reason="Backport CVE fix", **base_kwargs()
        )
        with pytest.raises(ValueError, match="modification_reason"):
            source.to_modified_upstream(version="5.5.0", reason="")
        self._assert_source_is_still_usable(source)

    def test_modified_upstream_to_clean(self) -> None:
        source = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(), modification_reason="Ahead of Fedora", **base_kwargs()
        )
        with pytest.raises(ValueError, match="version"):
            source.to_clean(fedora=clean_fedora(), version="")
        self._assert_source_is_still_usable(source)

    def test_modified_upstream_to_modified_fedora(self) -> None:
        source = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(), modification_reason="Ahead of Fedora", **base_kwargs()
        )
        with pytest.raises(ValueError, match="modification_reason"):
            source.to_modified_fedora(fedora=clean_fedora(), reason="")
        self._assert_source_is_still_usable(source)

    def test_independent_to_clean(self) -> None:
        source = IndependentPackageMetadata(**base_kwargs())
        with pytest.raises(ValueError, match="version"):
            source.to_clean(fedora=clean_fedora(), version="")
        self._assert_source_is_still_usable(source)


class TestEnsureHelpers:
    def test_ensure_clean_passes_through(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        assert metadata.ensure_clean() is metadata

    def test_ensure_clean_raises_on_wrong_class(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        with pytest.raises(InvalidStateTransitionError, match="not clean"):
            metadata.ensure_clean()

    def test_ensure_modified_fedora_passes_through(self) -> None:
        metadata = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(), modification_reason="Backport CVE fix", **base_kwargs()
        )
        assert metadata.ensure_modified_fedora() is metadata

    def test_ensure_modified_fedora_raises_on_wrong_class(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        with pytest.raises(InvalidStateTransitionError, match="not modified_fedora"):
            metadata.ensure_modified_fedora()

    def test_ensure_modified_upstream_passes_through(self) -> None:
        metadata = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(), modification_reason="Ahead of Fedora", **base_kwargs()
        )
        assert metadata.ensure_modified_upstream() is metadata

    def test_ensure_modified_upstream_raises_on_wrong_class(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        with pytest.raises(InvalidStateTransitionError, match="not modified_upstream"):
            metadata.ensure_modified_upstream()

    def test_ensure_independent_passes_through(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        assert metadata.ensure_independent() is metadata

    def test_ensure_independent_raises_on_wrong_class(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        with pytest.raises(InvalidStateTransitionError, match="not independent"):
            metadata.ensure_independent()


class TestBumpReleaseOnFedoraVersion:
    """CleanPackageMetadata/ModifiedFedoraPackageMetadata, via
    _OnFedoraVersionMixin."""

    def test_first_local_rebuild_appends_dot_one(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(release="3"), **base_kwargs())
        assert metadata.bump_release("3") == "3.1"

    def test_subsequent_rebuild_increments_trailing_n(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(release="3"), **base_kwargs())
        assert metadata.bump_release("3.1") == "3.2"
        assert metadata.bump_release("3.9") == "3.10"

    def test_works_on_modified_fedora_too(self) -> None:
        metadata = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(release="3"), modification_reason="Backport fix", **base_kwargs()
        )
        assert metadata.bump_release("3") == "3.1"

    def test_unrelated_release_is_rejected(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(release="3"), **base_kwargs())
        with pytest.raises(ReleaseMismatchError, match="fedora.release"):
            metadata.bump_release("7")

    def test_non_numeric_suffix_is_rejected(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(release="3"), **base_kwargs())
        with pytest.raises(ReleaseMismatchError):
            metadata.bump_release("3.abc")

    def test_result_never_depends_on_stored_version_field(self) -> None:
        # bump_release compares against fedora.release specifically, not
        # anything else stored on the object -- this is the HUM-5182 bug
        # shape for this branch: only fedora.release is the valid base.
        first = CleanPackageMetadata(
            fedora=clean_fedora(release="3"), **base_kwargs(version="1.0")
        )
        second = CleanPackageMetadata(
            fedora=clean_fedora(release="3"), **base_kwargs(version="99.0")
        )
        assert first.bump_release("3.1") == second.bump_release("3.1") == "3.2"


class TestBumpReleaseNoStoredBase:
    """ModifiedUpstreamPackageMetadata/IndependentPackageMetadata, via
    _NoStoredReleaseMixin."""

    def test_increments_trailing_n(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        assert metadata.bump_release("0.1") == "0.2"
        assert metadata.bump_release("0.9") == "0.10"

    def test_works_on_modified_upstream_too(self) -> None:
        metadata = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(), modification_reason="Ahead of Fedora", **base_kwargs()
        )
        assert metadata.bump_release("0.1") == "0.2"

    def test_bare_integer_is_rejected(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        with pytest.raises(ReleaseMismatchError, match="0.N"):
            metadata.bump_release("1")

    def test_non_zero_prefix_is_rejected(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        with pytest.raises(ReleaseMismatchError):
            metadata.bump_release("1.2")

    def test_result_never_depends_on_any_stored_metadata_value(self) -> None:
        # This is the crux of the HUM-5182 regression guard: the no-stored-
        # base branch's result must depend only on current_release, never on
        # anything stored on the object (there's nothing stored to compare
        # against, by design).
        independent = IndependentPackageMetadata(**base_kwargs(version="1.0"))
        modified_upstream = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(),
            modification_reason="Ahead of Fedora",
            **base_kwargs(version="99.0"),
        )
        assert independent.bump_release("0.4") == modified_upstream.bump_release("0.4") == "0.5"


def raw_fedora(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "git_url": "https://src.fedoraproject.org/rpms/bash.git",
        "branch": "rawhide",
        "sha": "2768211b5135c7169f513965d79e9f89e0ca6124",
        "release": "3",
    }
    defaults.update(overrides)
    return defaults


def raw_data(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "version": "5.4.3",
        "modification_status": "clean",
        "upstream_repo": "https://git.savannah.gnu.org/git/bash.git",
        "fedora": raw_fedora(),
    }
    defaults.update(overrides)
    return defaults


class TestFromRawDictHappyPath:
    def test_clean(self) -> None:
        metadata = PackageMetadata._from_raw_dict("bash", raw_data())
        assert isinstance(metadata, CleanPackageMetadata)
        assert metadata.package_name == "bash"
        assert metadata.version == "5.4.3"
        assert metadata.upstream_repo == "https://git.savannah.gnu.org/git/bash.git"
        assert metadata.fedora.release == "3"

    def test_modified_fedora(self) -> None:
        data = raw_data(modification_status="modified", modification_reason="Backport CVE fix")
        metadata = PackageMetadata._from_raw_dict("bash", data)
        assert isinstance(metadata, ModifiedFedoraPackageMetadata)
        assert metadata.modification_reason == "Backport CVE fix"
        assert isinstance(metadata.fedora, FedoraOriginWithRelease)

    def test_modified_upstream(self) -> None:
        fedora_without_release = {k: v for k, v in raw_fedora().items() if k != "release"}
        data = raw_data(
            modification_status="modified",
            modification_reason="Ahead of Fedora",
            fedora=fedora_without_release,
        )
        metadata = PackageMetadata._from_raw_dict("avahi", data)
        assert isinstance(metadata, ModifiedUpstreamPackageMetadata)
        assert not isinstance(metadata.fedora, FedoraOriginWithRelease)

    def test_independent(self) -> None:
        data = raw_data(modification_status="independent")
        del data["fedora"]
        metadata = PackageMetadata._from_raw_dict("hummingbird-agent", data)
        assert isinstance(metadata, IndependentPackageMetadata)

    def test_tag_strip_prefix_defaults_to_v(self) -> None:
        metadata = PackageMetadata._from_raw_dict("bash", raw_data())
        assert metadata.tag_strip_prefix == "v"

    def test_optional_fields_round_trip(self) -> None:
        data = raw_data(
            upstream_branch="f42",
            track_upstream="1.26",
            release_monitoring_project_id=12345,
            cve_product="bash",
            version_transform="some-transform",
            fix_status=1,
        )
        metadata = PackageMetadata._from_raw_dict("bash", data)
        assert metadata.upstream_branch == "f42"
        assert metadata.track_upstream == "1.26"
        assert metadata.release_monitoring_project_id == 12345
        assert metadata.cve_product == "bash"
        assert metadata.version_transform == "some-transform"
        assert metadata.fix_status == 1


class TestFromRawDictLenientCveProductAndFixStatus:
    def test_malformed_cve_product_is_dropped_not_raised(self) -> None:
        # This is the whole reason _from_raw_dict pre-normalizes before
        # calling __init__ (which is otherwise strict): a legacy file with
        # messy cve_product must still load, per section 8's read/write
        # asymmetry, not raise MalformedMetadataError.
        metadata = PackageMetadata._from_raw_dict("bash", raw_data(cve_product=123))
        assert metadata.cve_product is None

    def test_malformed_fix_status_is_dropped_not_raised(self) -> None:
        metadata = PackageMetadata._from_raw_dict("bash", raw_data(fix_status="not-a-status"))
        assert metadata.fix_status is None


class TestFromRawDictMalformed:
    def test_unrecognized_top_level_field(self) -> None:
        with pytest.raises(MalformedMetadataError, match="unrecognized"):
            PackageMetadata._from_raw_dict("bash", raw_data(bogus_field="x"))

    def test_stray_top_level_release_field_is_rejected(self) -> None:
        # release belongs under fedora (fedora.release), never as a top-level
        # field -- distinct from bogus_field above, which proves unrecognized
        # fields are rejected in general, not that "release" specifically is
        # one of them.
        with pytest.raises(MalformedMetadataError, match="unrecognized"):
            PackageMetadata._from_raw_dict("bash", raw_data(release="3"))

    def test_missing_modification_status(self) -> None:
        data = raw_data()
        del data["modification_status"]
        with pytest.raises(MalformedMetadataError):
            PackageMetadata._from_raw_dict("bash", data)

    def test_invalid_modification_status(self) -> None:
        with pytest.raises(MalformedMetadataError):
            PackageMetadata._from_raw_dict("bash", raw_data(modification_status="bogus"))

    def test_missing_version_raises(self) -> None:
        data = raw_data()
        del data["version"]
        with pytest.raises(MalformedMetadataError, match="version"):
            PackageMetadata._from_raw_dict("bash", data)

    def test_missing_upstream_repo_raises(self) -> None:
        data = raw_data()
        del data["upstream_repo"]
        with pytest.raises(MalformedMetadataError, match="upstream_repo"):
            PackageMetadata._from_raw_dict("bash", data)

    def test_independent_with_fedora_present(self) -> None:
        with pytest.raises(MalformedMetadataError, match="independent implies fedora is absent"):
            PackageMetadata._from_raw_dict(
                "bash", raw_data(modification_status="independent")
            )

    def test_clean_without_fedora(self) -> None:
        data = raw_data()
        del data["fedora"]
        with pytest.raises(MalformedMetadataError, match="fedora is present"):
            PackageMetadata._from_raw_dict("bash", data)

    def test_modified_without_fedora(self) -> None:
        data = raw_data(modification_status="modified", modification_reason="x")
        del data["fedora"]
        with pytest.raises(MalformedMetadataError, match="fedora is present"):
            PackageMetadata._from_raw_dict("bash", data)

    def test_clean_with_fedora_missing_release(self) -> None:
        data = raw_data(fedora={k: v for k, v in raw_fedora().items() if k != "release"})
        with pytest.raises(MalformedMetadataError, match="fedora.release is present"):
            PackageMetadata._from_raw_dict("bash", data)

    def test_clean_with_modification_reason_present(self) -> None:
        with pytest.raises(MalformedMetadataError, match="modification_reason is absent"):
            PackageMetadata._from_raw_dict("bash", raw_data(modification_reason="stale text"))

    def test_modified_without_modification_reason(self) -> None:
        with pytest.raises(MalformedMetadataError):
            PackageMetadata._from_raw_dict("bash", raw_data(modification_status="modified"))

    def test_fedora_not_an_object(self) -> None:
        with pytest.raises(MalformedMetadataError, match="fedora must be an object"):
            PackageMetadata._from_raw_dict("bash", raw_data(fedora="not-an-object"))

    def test_fedora_missing_required_subfield(self) -> None:
        data = raw_data(fedora={k: v for k, v in raw_fedora().items() if k != "sha"})
        with pytest.raises(MalformedMetadataError, match="sha"):
            PackageMetadata._from_raw_dict("bash", data)

    def test_fedora_unrecognized_subfield(self) -> None:
        data = raw_data(fedora=raw_fedora(bogus="x"))
        with pytest.raises(MalformedMetadataError, match="unrecognized fedora field"):
            PackageMetadata._from_raw_dict("bash", data)

    def test_fedora_release_with_dist_tag(self) -> None:
        data = raw_data(fedora=raw_fedora(release="3.fc42"))
        with pytest.raises(MalformedMetadataError, match="dist-tag"):
            PackageMetadata._from_raw_dict("bash", data)

    def test_version_from_ref_invalid_type(self) -> None:
        data = raw_data(version_from_ref={"type": "tag"})
        with pytest.raises(MalformedMetadataError, match="commit-date"):
            PackageMetadata._from_raw_dict("bash", data)

    def test_version_from_ref_not_an_object(self) -> None:
        data = raw_data(version_from_ref="not-an-object")
        with pytest.raises(MalformedMetadataError, match="version_from_ref must be an object"):
            PackageMetadata._from_raw_dict("bash", data)

    def test_version_from_ref_unrecognized_subfield(self) -> None:
        data = raw_data(version_from_ref={"type": "commit-date", "bogus": "x"})
        with pytest.raises(MalformedMetadataError, match="unrecognized version_from_ref field"):
            PackageMetadata._from_raw_dict("bash", data)

    def test_independent_with_modification_reason_present(self) -> None:
        data = raw_data(modification_status="independent", modification_reason="leftover")
        del data["fedora"]
        with pytest.raises(MalformedMetadataError, match="independent implies modification_reason is absent"):
            PackageMetadata._from_raw_dict("bash", data)

    def test_unhashable_modification_status(self) -> None:
        with pytest.raises(MalformedMetadataError, match="must be a string"):
            PackageMetadata._from_raw_dict("bash", raw_data(modification_status=[]))


class TestFedoraOriginWithReleaseConstruction:
    def test_none_release_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be None"):
            FedoraOriginWithRelease(
                git_url="https://src.fedoraproject.org/rpms/bash.git",
                branch="rawhide", sha="abc", release=None,  # type: ignore[arg-type]
            )

    def test_empty_release_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            FedoraOriginWithRelease(
                git_url="https://src.fedoraproject.org/rpms/bash.git",
                branch="rawhide", sha="abc", release="",
            )


class TestValidateWellFormedObjects:
    def test_no_resolvers_no_violations_on_clean(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        result = metadata.validate()
        assert result.violations == []
        assert result.checked_spec_version is False

    def test_no_resolvers_no_violations_on_modified_fedora(self) -> None:
        metadata = ModifiedFedoraPackageMetadata(
            fedora=clean_fedora(), modification_reason="Backport CVE fix", **base_kwargs()
        )
        assert metadata.validate().violations == []

    def test_no_resolvers_no_violations_on_modified_upstream(self) -> None:
        metadata = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(), modification_reason="Ahead of Fedora", **base_kwargs()
        )
        assert metadata.validate().violations == []

    def test_no_resolvers_no_violations_on_independent(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        assert metadata.validate().violations == []


class TestValidateResolvers:
    def test_spec_version_match_is_valid(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs(version="5.4.3"))
        result = metadata.validate(resolve_spec_version=lambda m: "5.4.3")
        assert result.checked_spec_version is True
        assert result.violations == []

    def test_spec_version_mismatch_is_a_violation(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs(version="5.4.3"))
        result = metadata.validate(resolve_spec_version=lambda m: "5.4.4")
        assert any("5.4.4" in v.message for v in result.violations)

    def test_resolvers_default_to_not_checked(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        result = metadata.validate()
        assert result.checked_spec_version is False


class TestValidateAttributePollution:
    """These simulate an object that reached an invalid state some way
    other than this library's own API -- validate() is supposed to catch
    these independently of the class hierarchy, per its 100%-contract
    design (documentation/design/metadata-library-design.md, section 5)."""

    def test_polluted_fedora_on_independent_is_caught(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        metadata.fedora = clean_fedora()  # type: ignore[attr-defined]
        result = metadata.validate()
        assert any("fedora is absent" in v.message for v in result.violations)

    def test_polluted_modification_reason_on_clean_is_caught(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        metadata.modification_reason = "leftover text"  # type: ignore[attr-defined]
        result = metadata.validate()
        assert any("modification_reason is absent" in v.message for v in result.violations)

    def test_multiple_violations_all_reported(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        metadata.fedora = clean_fedora()  # type: ignore[attr-defined]
        metadata.modification_reason = "leftover text"  # type: ignore[attr-defined]
        result = metadata.validate()
        assert len(result.violations) >= 2

    def test_polluted_version_is_caught(self) -> None:
        # Exercises the single-error (add()) path in validate(), not just
        # the list-returning (extend()) checks the other pollution tests
        # above already cover -- __init__/the setter both already reject an
        # empty version, so this simulates it reaching storage some other
        # way, same reasoning as the other pollution tests.
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        metadata._version = ""
        result = metadata.validate()
        assert any("version" in v.message for v in result.violations)

    def test_polluted_release_on_modified_upstream_is_caught(self) -> None:
        metadata = ModifiedUpstreamPackageMetadata(
            fedora=upstream_fedora(), modification_reason="Ahead of Fedora", **base_kwargs()
        )
        metadata._fedora = FedoraOriginWithRelease(
            git_url="https://src.fedoraproject.org/rpms/avahi.git",
            branch="rawhide",
            sha="abc123",
            release="3",
        )
        result = metadata.validate()
        assert any("round-trip as modified-fedora" in v.message for v in result.violations)


class TestInvalidMetadataError:
    def test_carries_the_result_and_summarizes_violation_count(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        metadata.fedora = clean_fedora()  # type: ignore[attr-defined]
        result = metadata.validate()
        error = InvalidMetadataError(result)
        assert error.result is result
        assert str(len(result.violations)) in str(error)


class TestToDict:
    def test_clean_round_trips(self) -> None:
        metadata = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        d = metadata.to_dict()
        assert d["version"] == "5.4.3"
        assert d["modification_status"] == "clean"
        fedora = d["fedora"]
        assert isinstance(fedora, dict)
        assert fedora["release"] == "3"
        assert d["upstream_repo"] == "https://git.savannah.gnu.org/git/bash.git"

    def test_independent_has_no_fedora(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        d = metadata.to_dict()
        assert d["modification_status"] == "independent"
        assert "fedora" not in d

    def test_modified_fedora_includes_reason(self) -> None:
        clean = CleanPackageMetadata(fedora=clean_fedora(), **base_kwargs())
        modified = clean.to_modified_fedora(reason="backport CVE fix")
        d = modified.to_dict()
        assert d["modification_status"] == "modified"
        assert d["modification_reason"] == "backport CVE fix"
        fedora = d["fedora"]
        assert isinstance(fedora, dict)
        assert "release" in fedora

    def test_optional_fields_omitted_when_none(self) -> None:
        metadata = IndependentPackageMetadata(**base_kwargs())
        d = metadata.to_dict()
        assert "track_upstream" not in d
        assert "release_monitoring_project_id" not in d
