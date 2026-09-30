"""Unit tests for hum.meta.repository.

Uses a real temp directory (tmp_path), not a mocked filesystem -- these
methods are thin enough that faking the filesystem would just test the
fakes (documentation/design/metadata-library-design.md, section 10).
"""

import json
from pathlib import Path

import pytest
import yaml

from hum.meta.model import (
    CleanPackageMetadata,
    FedoraOrigin,
    IndependentPackageMetadata,
    InvalidMetadataError,
    MalformedMetadataError,
    ModifiedFedoraPackageMetadata,
    ModifiedUpstreamPackageMetadata,
    VersionFromRef,
)
from hum.meta.repository import MetadataRepository

from .conftest import clean_fedora


class TestLoad:
    def test_load_clean(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        (tmp_path / "metadata").mkdir()
        (tmp_path / "metadata" / "bash.json").write_text(
            json.dumps(
                {
                    "version": "5.4.3",
                    "modification_status": "clean",
                    "upstream_repo": "https://git.savannah.gnu.org/git/bash.git",
                    "fedora": {
                        "git_url": "https://src.fedoraproject.org/rpms/bash.git",
                        "branch": "rawhide",
                        "sha": "2768211b5135c7169f513965d79e9f89e0ca6124",
                        "release": "3",
                    },
                }
            )
        )
        metadata = repo.load("bash")
        assert isinstance(metadata, CleanPackageMetadata)
        assert metadata.package_name == "bash"
        assert metadata.version == "5.4.3"

    def test_load_missing_file_raises_os_error(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        with pytest.raises(OSError):
            repo.load("does-not-exist")

    def test_load_invalid_json_raises_json_decode_error(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        (tmp_path / "metadata").mkdir()
        (tmp_path / "metadata" / "bash.json").write_text("{not valid json")
        with pytest.raises(json.JSONDecodeError):
            repo.load("bash")

    def test_load_non_object_json_raises_malformed(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        (tmp_path / "metadata").mkdir()
        (tmp_path / "metadata" / "bash.json").write_text("[1, 2, 3]")
        with pytest.raises(MalformedMetadataError, match="expected a JSON object"):
            repo.load("bash")

    def test_load_unclassifiable_data_raises_malformed(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        (tmp_path / "metadata").mkdir()
        (tmp_path / "metadata" / "bash.json").write_text(json.dumps({"bogus": "x"}))
        with pytest.raises(MalformedMetadataError):
            repo.load("bash")

    def test_load_with_explicit_path(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        staged_path = tmp_path / "staged.json"
        staged_path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "modification_status": "independent",
                    "upstream_repo": "https://github.com/example/agent",
                }
            )
        )
        metadata = repo.load("hummingbird-agent", path=staged_path)
        assert isinstance(metadata, IndependentPackageMetadata)


class TestSave:
    def test_save_round_trip(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        metadata = CleanPackageMetadata(
            package_name="bash",
            version="5.4.3",
            upstream_repo="https://git.savannah.gnu.org/git/bash.git",
            fedora=clean_fedora(),
        )
        repo.save(metadata)
        loaded = repo.load("bash")
        assert isinstance(loaded, CleanPackageMetadata)
        assert loaded.version == "5.4.3"
        assert loaded.fedora.release == "3"

    def test_save_round_trip_with_version_from_ref(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        metadata = CleanPackageMetadata(
            package_name="bash",
            version="5.4.3",
            upstream_repo="https://git.savannah.gnu.org/git/bash.git",
            fedora=clean_fedora(),
            version_from_ref=VersionFromRef(type="commit-date"),
        )
        repo.save(metadata)
        raw = json.loads((tmp_path / "metadata" / "bash.json").read_text())
        assert raw["version_from_ref"] == {"type": "commit-date"}
        loaded = repo.load("bash")
        assert loaded.version_from_ref == VersionFromRef(type="commit-date")

    def test_save_omits_none_fields_never_writes_null(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        metadata = CleanPackageMetadata(
            package_name="bash",
            version="5.4.3",
            upstream_repo="https://git.savannah.gnu.org/git/bash.git",
            fedora=clean_fedora(),
        )
        repo.save(metadata)
        raw = json.loads((tmp_path / "metadata" / "bash.json").read_text())
        assert "upstream_branch" not in raw
        assert "null" not in (tmp_path / "metadata" / "bash.json").read_text()

    def test_save_formatting_matches_dist_git_convention(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        metadata = CleanPackageMetadata(
            package_name="bash",
            version="5.4.3",
            upstream_repo="https://git.savannah.gnu.org/git/bash.git",
            fedora=clean_fedora(),
        )
        repo.save(metadata)
        text = (tmp_path / "metadata" / "bash.json").read_text()
        assert text.endswith("\n")
        assert not text.endswith("\n\n")
        raw = json.loads(text)
        assert list(raw.keys()) == sorted(raw.keys())

    def test_save_rejects_invalid_metadata(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        metadata = IndependentPackageMetadata(
            package_name="bash", version="5.4.3", upstream_repo="https://example.com/bash"
        )
        metadata.fedora = clean_fedora()  # type: ignore[attr-defined]
        with pytest.raises(InvalidMetadataError):
            repo.save(metadata)
        assert not (tmp_path / "metadata" / "bash.json").exists()

    def test_save_malformed_cve_product_round_trip_drops_the_field(self, tmp_path: Path) -> None:
        # section 8's self-healing consequence: a legacy file with a
        # malformed cve_product loads fine (dropped to None), and the very
        # next save doesn't write the field back -- not a bug.
        repo = MetadataRepository(tmp_path)
        (tmp_path / "metadata").mkdir()
        (tmp_path / "metadata" / "bash.json").write_text(
            json.dumps(
                {
                    "version": "5.4.3",
                    "modification_status": "clean",
                    "upstream_repo": "https://git.savannah.gnu.org/git/bash.git",
                    "fedora": {
                        "git_url": "https://src.fedoraproject.org/rpms/bash.git",
                        "branch": "rawhide",
                        "sha": "2768211b5135c7169f513965d79e9f89e0ca6124",
                        "release": "3",
                    },
                    "cve_product": 123,
                }
            )
        )
        metadata = repo.load("bash")
        assert metadata.cve_product is None
        repo.save(metadata)
        raw = json.loads((tmp_path / "metadata" / "bash.json").read_text())
        assert "cve_product" not in raw

    def test_save_with_explicit_path_does_not_touch_canonical_location(
        self, tmp_path: Path
    ) -> None:
        repo = MetadataRepository(tmp_path)
        metadata = CleanPackageMetadata(
            package_name="bash",
            version="5.4.3",
            upstream_repo="https://git.savannah.gnu.org/git/bash.git",
            fedora=clean_fedora(),
        )
        staged_path = tmp_path / "staged.json"
        repo.save(metadata, path=staged_path)
        assert staged_path.exists()
        assert not (tmp_path / "metadata" / "bash.json").exists()

    def test_save_omits_tag_strip_prefix_when_default(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        metadata = CleanPackageMetadata(
            package_name="bash",
            version="5.4.3",
            upstream_repo="https://git.savannah.gnu.org/git/bash.git",
            fedora=clean_fedora(),
        )
        assert metadata.tag_strip_prefix == "v"
        repo.save(metadata)
        raw = json.loads((tmp_path / "metadata" / "bash.json").read_text())
        assert "tag_strip_prefix" not in raw

    def test_save_writes_tag_strip_prefix_when_customized(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        metadata = CleanPackageMetadata(
            package_name="bash",
            version="5.4.3",
            upstream_repo="https://git.savannah.gnu.org/git/bash.git",
            fedora=clean_fedora(),
        )
        metadata.enable_upstream_tracking_gitlab_tags(tag_strip_prefix="release-")
        repo.save(metadata)
        raw = json.loads((tmp_path / "metadata" / "bash.json").read_text())
        assert raw["tag_strip_prefix"] == "release-"
        loaded = repo.load("bash")
        assert loaded.tag_strip_prefix == "release-"


class TestCreateMethods:
    def test_create_clean_saves_and_returns(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        metadata = repo.create_clean(
            "bash",
            version="5.4.3",
            upstream_repo="https://git.savannah.gnu.org/git/bash.git",
            fedora=clean_fedora(),
        )
        assert isinstance(metadata, CleanPackageMetadata)
        assert (tmp_path / "metadata" / "bash.json").exists()

    def test_create_modified_fedora_saves_and_returns(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        metadata = repo.create_modified_fedora(
            "bash",
            version="5.4.3",
            upstream_repo="https://git.savannah.gnu.org/git/bash.git",
            fedora=clean_fedora(),
            reason="Backport CVE fix",
        )
        assert isinstance(metadata, ModifiedFedoraPackageMetadata)
        assert (tmp_path / "metadata" / "bash.json").exists()

    def test_create_modified_upstream_saves_and_returns(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        metadata = repo.create_modified_upstream(
            "avahi",
            version="0.9",
            upstream_repo="https://github.com/avahi/avahi",
            fedora=FedoraOrigin(
                git_url="https://src.fedoraproject.org/rpms/avahi.git",
                branch="rawhide",
                sha="195919c9f9a8dff0e996921afd8736df8f12715b",
            ),
            reason="Ahead of Fedora",
        )
        assert isinstance(metadata, ModifiedUpstreamPackageMetadata)
        assert (tmp_path / "metadata" / "avahi.json").exists()

    def test_create_independent_saves_and_returns(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        metadata = repo.create_independent(
            "hummingbird-agent", version="1.0.0", upstream_repo="https://github.com/example/agent"
        )
        assert isinstance(metadata, IndependentPackageMetadata)
        assert (tmp_path / "metadata" / "hummingbird-agent.json").exists()


class TestLoadAll:
    def test_load_all_keyed_by_package_name(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        repo.create_clean(
            "bash",
            version="5.4.3",
            upstream_repo="https://git.savannah.gnu.org/git/bash.git",
            fedora=clean_fedora(),
        )
        repo.create_independent(
            "hummingbird-agent", version="1.0.0", upstream_repo="https://github.com/example/agent"
        )
        all_metadata = repo.load_all()
        assert set(all_metadata) == {"bash", "hummingbird-agent"}
        assert isinstance(all_metadata["bash"], CleanPackageMetadata)
        assert isinstance(all_metadata["hummingbird-agent"], IndependentPackageMetadata)

    def test_load_all_empty_when_no_metadata_dir(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        assert repo.load_all() == {}


class TestPackageUniverseHelpers:
    @pytest.fixture
    def mixed_repo(self, tmp_path: Path) -> MetadataRepository:
        # A deliberate mix: some packages with a spec only, some with
        # metadata only, some with both, some target-manifest-only, some
        # with neither -- per section 10's test plan.
        root = tmp_path
        rpms_dir = root / "rpms"
        metadata_dir = root / "metadata"
        rpms_dir.mkdir()
        metadata_dir.mkdir()

        (rpms_dir / "spec-and-metadata").mkdir()
        (rpms_dir / "spec-and-metadata" / "spec-and-metadata.spec").write_text("")
        (metadata_dir / "spec-and-metadata.json").write_text("{}")

        (rpms_dir / "spec-only").mkdir()
        (rpms_dir / "spec-only" / "spec-only.spec").write_text("")

        (rpms_dir / "no-spec").mkdir()  # a directory with no .spec file at all

        (metadata_dir / "metadata-only.json").write_text("{}")  # no rpms/ dir at all

        (root / "target-packages.yml").write_text(
            yaml.safe_dump({"packages": ["target-manifest-only", "spec-and-metadata"]})
        )

        return MetadataRepository(root)

    def test_packages_with_spec(self, mixed_repo: MetadataRepository) -> None:
        assert mixed_repo.packages_with_spec() == ["spec-and-metadata", "spec-only"]

    def test_packages_with_metadata(self, mixed_repo: MetadataRepository) -> None:
        assert mixed_repo.packages_with_metadata() == ["metadata-only", "spec-and-metadata"]

    def test_target_packages(self, mixed_repo: MetadataRepository) -> None:
        # Union of the manifest's list and the rpms/ directory listing.
        assert mixed_repo.target_packages() == [
            "no-spec",
            "spec-and-metadata",
            "spec-only",
            "target-manifest-only",
        ]

    def test_all_package_directories(self, mixed_repo: MetadataRepository) -> None:
        assert mixed_repo.all_package_directories() == ["no-spec", "spec-and-metadata", "spec-only"]

    def test_all_package_directories_differs_from_packages_with_spec(
        self, mixed_repo: MetadataRepository
    ) -> None:
        assert "no-spec" in mixed_repo.all_package_directories()
        assert "no-spec" not in mixed_repo.packages_with_spec()

    def test_results_are_sorted_lists(self, mixed_repo: MetadataRepository) -> None:
        for result in (
            mixed_repo.packages_with_spec(),
            mixed_repo.packages_with_metadata(),
            mixed_repo.target_packages(),
            mixed_repo.all_package_directories(),
        ):
            assert result == sorted(result)

    def test_empty_repo_returns_empty_lists(self, tmp_path: Path) -> None:
        repo = MetadataRepository(tmp_path)
        assert repo.packages_with_spec() == []
        assert repo.packages_with_metadata() == []
        assert repo.target_packages() == []
        assert repo.all_package_directories() == []
