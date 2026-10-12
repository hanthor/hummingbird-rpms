"""Tests for ci/migrate_metadata.py -- the one-time flat-to-nested-fedora
metadata schema migration tool (HUM-6232)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ci"))
import migrate_metadata as migrate  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hum.meta import MetadataRepository  # noqa: E402
from hum.meta.model import CleanPackageMetadata  # noqa: E402


class TestReshapeToNewSchema:
    def test_independent_package_passes_through_unchanged(self) -> None:
        data: dict[str, object] = {
            "version": "1.0.0",
            "modification_status": "independent",
            "upstream_repo": "https://github.com/example/agent",
        }
        assert migrate.reshape_to_new_schema(data) == data

    def test_clean_package_with_release_is_reshaped(self) -> None:
        data: dict[str, object] = {
            "version": "5.4.3",
            "modification_status": "clean",
            "upstream_repo": "https://git.savannah.gnu.org/git/bash.git",
            "source": "https://src.fedoraproject.org/rpms/bash.git",
            "branch": "rawhide",
            "sha": "2768211b5135c7169f513965d79e9f89e0ca6124",
            "release": "3",
        }
        reshaped = migrate.reshape_to_new_schema(data)
        assert reshaped["fedora"] == {
            "git_url": "https://src.fedoraproject.org/rpms/bash.git",
            "branch": "rawhide",
            "sha": "2768211b5135c7169f513965d79e9f89e0ca6124",
            "release": "3",
        }
        assert "source" not in reshaped
        assert "branch" not in reshaped
        assert "sha" not in reshaped
        assert "release" not in reshaped

    def test_modified_package_without_release_is_reshaped(self) -> None:
        data: dict[str, object] = {
            "version": "0.9~rc4",
            "modification_status": "modified",
            "modification_reason": "update to 0.9rc4",
            "upstream_repo": "https://github.com/avahi/avahi",
            "source": "https://src.fedoraproject.org/rpms/avahi.git",
            "branch": "rawhide",
            "sha": "195919c9f9a8dff0e996921afd8736df8f12715b",
        }
        reshaped = migrate.reshape_to_new_schema(data)
        assert reshaped["fedora"] == {
            "git_url": "https://src.fedoraproject.org/rpms/avahi.git",
            "branch": "rawhide",
            "sha": "195919c9f9a8dff0e996921afd8736df8f12715b",
        }
        assert "release" not in reshaped["fedora"]

    def test_other_fields_are_preserved(self) -> None:
        data: dict[str, object] = {
            "version": "1.26.8",
            "modification_status": "modified",
            "modification_reason": "Update to upstream version 1.26.8",
            "upstream_repo": "https://github.com/golang/go",
            "source": "https://src.fedoraproject.org/rpms/golang.git",
            "branch": "rawhide",
            "sha": "0241d8077bf0228910b349a13b07dc867d476a41",
            "release": "1",
            "cve_product": "Go ",
            "track_upstream": "1.26",
            "release_monitoring_project_id": 1227,
        }
        reshaped = migrate.reshape_to_new_schema(data)
        assert reshaped["cve_product"] == "Go "
        assert reshaped["track_upstream"] == "1.26"
        assert reshaped["release_monitoring_project_id"] == 1227
        assert reshaped["modification_reason"] == "Update to upstream version 1.26.8"

    def test_missing_branch_raises(self) -> None:
        data: dict[str, object] = {
            "source": "https://src.fedoraproject.org/rpms/bash.git",
            "sha": "abc123",
        }
        with pytest.raises(ValueError, match="branch"):
            migrate.reshape_to_new_schema(data)

    def test_original_dict_not_mutated(self) -> None:
        data: dict[str, object] = {
            "modification_status": "clean",
            "source": "https://src.fedoraproject.org/rpms/bash.git",
            "branch": "rawhide",
            "sha": "abc123",
            "release": "3",
        }
        original = dict(data)
        migrate.reshape_to_new_schema(data)
        assert data == original


class TestMigrate:
    def _write(self, root: Path, name: str, data: dict[str, object]) -> None:
        (root / "metadata").mkdir(exist_ok=True)
        (root / "metadata" / f"{name}.json").write_text(json.dumps(data))

    def test_migrates_a_real_old_schema_file(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            "bash",
            {
                "version": "5.4.3",
                "modification_status": "clean",
                "upstream_repo": "https://git.savannah.gnu.org/git/bash.git",
                "source": "https://src.fedoraproject.org/rpms/bash.git",
                "branch": "rawhide",
                "sha": "2768211b5135c7169f513965d79e9f89e0ca6124",
                "release": "3",
            },
        )
        migrated, skipped, failed = migrate.migrate(tmp_path)
        assert migrated == ["bash"]
        assert skipped == []
        assert failed == []

        repo = MetadataRepository(tmp_path)
        loaded = repo.load("bash")
        assert isinstance(loaded, CleanPackageMetadata)
        assert loaded.fedora.git_url == "https://src.fedoraproject.org/rpms/bash.git"
        assert loaded.fedora.release == "3"

    def test_skips_already_migrated_file(self, tmp_path: Path) -> None:
        raw: dict[str, object] = {
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
        self._write(tmp_path, "bash", raw)
        before = (tmp_path / "metadata" / "bash.json").read_text()

        migrated, skipped, failed = migrate.migrate(tmp_path)
        assert migrated == []
        assert skipped == ["bash"]
        assert failed == []
        assert (tmp_path / "metadata" / "bash.json").read_text() == before

    def test_collects_failures_without_stopping(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            "bash",
            {
                "version": "5.4.3",
                "modification_status": "clean",
                "upstream_repo": "https://git.savannah.gnu.org/git/bash.git",
                "source": "https://src.fedoraproject.org/rpms/bash.git",
                "branch": "rawhide",
                "sha": "2768211b5135c7169f513965d79e9f89e0ca6124",
                "release": "3",
            },
        )
        self._write(
            tmp_path,
            "broken",
            {
                "version": "1.0",
                "modification_status": "clean",
                "upstream_repo": "https://example.com/broken",
                "source": "https://src.fedoraproject.org/rpms/broken.git",
                "branch": "rawhide",
                # 'sha' deliberately missing
                "release": "1",
            },
        )
        migrated, skipped, failed = migrate.migrate(tmp_path)
        assert migrated == ["bash"]
        assert [name for name, _ in failed] == ["broken"]

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            "bash",
            {
                "version": "5.4.3",
                "modification_status": "clean",
                "upstream_repo": "https://git.savannah.gnu.org/git/bash.git",
                "source": "https://src.fedoraproject.org/rpms/bash.git",
                "branch": "rawhide",
                "sha": "2768211b5135c7169f513965d79e9f89e0ca6124",
                "release": "3",
            },
        )
        before = (tmp_path / "metadata" / "bash.json").read_text()

        migrated, skipped, failed = migrate.migrate(tmp_path, dry_run=True)
        assert migrated == ["bash"]
        assert failed == []
        assert (tmp_path / "metadata" / "bash.json").read_text() == before
