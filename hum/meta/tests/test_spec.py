"""Unit tests for hum.meta.spec.

Uses real spec files from this repo's own rpms/<package>/ trees for
resolve_spec_version, and synthetic fixtures for bump_release -- the
latter needs controlled Release values and metadata state.
"""

from pathlib import Path

import pytest

from hum.meta.model import (
    FedoraOrigin,
    FedoraOriginWithRelease,
)
from hum.meta.spec import bump_release, find_spec, resolve_spec_version

REPO_ROOT = Path(__file__).resolve().parents[3]

_SPEC_TEMPLATE = """\
Name: testpkg
Version: {version}
Release: {release}
Summary: Test
License: MIT
%description
Test package.
"""


def _write_spec(
    tmp_path: Path, release: str, *, version: str = "1.0"
) -> Path:
    """Create a minimal spec in a temp dir and return the package dir."""
    pkg_dir = tmp_path / "testpkg"
    pkg_dir.mkdir(exist_ok=True)
    spec = pkg_dir / "testpkg.spec"
    spec.write_text(_SPEC_TEMPLATE.format(version=version, release=release))
    return pkg_dir


def _clean_metadata(**overrides: object):
    from hum.meta.model import CleanPackageMetadata

    defaults: dict[str, object] = {
        "package_name": "testpkg",
        "version": "1.0",
        "upstream_repo": "https://example.com/testpkg",
        "fedora": FedoraOriginWithRelease(
            git_url="https://src.fedoraproject.org/rpms/testpkg.git",
            branch="rawhide",
            sha="abc123",
            release="1",
        ),
    }
    defaults.update(overrides)
    return CleanPackageMetadata(**defaults)  # type: ignore[arg-type]


def _modified_fedora_metadata(**overrides: object):
    from hum.meta.model import ModifiedFedoraPackageMetadata

    defaults: dict[str, object] = {
        "package_name": "testpkg",
        "version": "1.0",
        "upstream_repo": "https://example.com/testpkg",
        "fedora": FedoraOriginWithRelease(
            git_url="https://src.fedoraproject.org/rpms/testpkg.git",
            branch="rawhide",
            sha="abc123",
            release="1",
        ),
        "modification_reason": "test",
    }
    defaults.update(overrides)
    return ModifiedFedoraPackageMetadata(**defaults)  # type: ignore[arg-type]


def _modified_upstream_metadata(**overrides: object):
    from hum.meta.model import ModifiedUpstreamPackageMetadata

    defaults: dict[str, object] = {
        "package_name": "testpkg",
        "version": "2.0",
        "upstream_repo": "https://example.com/testpkg",
        "fedora": FedoraOrigin(
            git_url="https://src.fedoraproject.org/rpms/testpkg.git",
            branch="rawhide",
            sha="abc123",
        ),
        "modification_reason": "ahead of Fedora",
    }
    defaults.update(overrides)
    return ModifiedUpstreamPackageMetadata(**defaults)  # type: ignore[arg-type]


def _independent_metadata(**overrides: object):
    from hum.meta.model import IndependentPackageMetadata

    defaults: dict[str, object] = {
        "package_name": "testpkg",
        "version": "1.0",
        "upstream_repo": "https://example.com/testpkg",
    }
    defaults.update(overrides)
    return IndependentPackageMetadata(**defaults)  # type: ignore[arg-type]


# --- find_spec ---


class TestFindSpec:
    def test_finds_single_spec(self, tmp_path: Path) -> None:
        spec = tmp_path / "foo.spec"
        spec.write_text("Name: foo\n")
        assert find_spec(tmp_path) == spec

    def test_no_spec_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="exactly one"):
            find_spec(tmp_path)

    def test_multiple_specs_raises(self, tmp_path: Path) -> None:
        (tmp_path / "a.spec").write_text("Name: a\n")
        (tmp_path / "b.spec").write_text("Name: b\n")
        with pytest.raises(FileNotFoundError, match="exactly one"):
            find_spec(tmp_path)


# --- resolve_spec_version ---


class TestResolveSpecVersion:
    def test_resolves_a_real_spec(self) -> None:
        assert resolve_spec_version(REPO_ROOT / "rpms" / "bash") == "5.3.15"

    def test_missing_rpmspec_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "bash.spec").write_text("Name: bash\n")
        monkeypatch.setenv("PATH", str(tmp_path))  # a real PATH with no rpmspec on it
        with pytest.raises(ValueError, match="rpmspec not found on PATH"):
            resolve_spec_version(tmp_path)

    def test_no_spec_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="exactly one"):
            resolve_spec_version(tmp_path)

    def test_multiple_spec_files_raises(self, tmp_path: Path) -> None:
        (tmp_path / "a.spec").write_text("Name: a\n")
        (tmp_path / "b.spec").write_text("Name: b\n")
        with pytest.raises(FileNotFoundError, match="exactly one"):
            resolve_spec_version(tmp_path)

    def test_malformed_spec_raises_with_rpmspec_diagnostics(self, tmp_path: Path) -> None:
        (tmp_path / "bad.spec").write_text("not a valid spec file at all {{{\n")
        with pytest.raises(ValueError, match="rpmspec failed"):
            resolve_spec_version(tmp_path)


# --- bump_release: clean/modified-fedora (has fedora.release base) ---


class TestBumpReleaseWithBase:
    """Clean and modified-fedora packages use fedora.release for disambiguation."""

    def test_first_rebuild_appends_dot_one(self, tmp_path: Path) -> None:
        pkg_dir = _write_spec(tmp_path, "1%{?dist}")
        metadata = _clean_metadata()
        new = bump_release(metadata, pkg_dir)
        assert new == "1.1"
        assert "Release: 1.1%{?dist}" in (pkg_dir / "testpkg.spec").read_text()

    def test_second_rebuild_increments(self, tmp_path: Path) -> None:
        pkg_dir = _write_spec(tmp_path, "1.1%{?dist}")
        metadata = _clean_metadata()
        new = bump_release(metadata, pkg_dir)
        assert new == "1.2"

    def test_many_rebuilds(self, tmp_path: Path) -> None:
        pkg_dir = _write_spec(tmp_path, "1.99%{?dist}")
        metadata = _clean_metadata()
        assert bump_release(metadata, pkg_dir) == "1.100"

    def test_dotted_fedora_release_first_rebuild(self, tmp_path: Path) -> None:
        """Fedora ships 3.1 -- first rebuild should be 3.1.1, not 3.2."""
        pkg_dir = _write_spec(tmp_path, "3.1%{?dist}")
        fedora = FedoraOriginWithRelease(
            git_url="https://src.fedoraproject.org/rpms/testpkg.git",
            branch="rawhide", sha="abc", release="3.1",
        )
        metadata = _clean_metadata(fedora=fedora)
        assert bump_release(metadata, pkg_dir) == "3.1.1"

    def test_dotted_fedora_release_subsequent_rebuild(self, tmp_path: Path) -> None:
        """Already rebuilt 3.1 to 3.1.1 -- next should be 3.1.2."""
        pkg_dir = _write_spec(tmp_path, "3.1.1%{?dist}")
        fedora = FedoraOriginWithRelease(
            git_url="https://src.fedoraproject.org/rpms/testpkg.git",
            branch="rawhide", sha="abc", release="3.1",
        )
        metadata = _clean_metadata(fedora=fedora)
        assert bump_release(metadata, pkg_dir) == "3.1.2"

    def test_prerelease_zero_dot_n(self, tmp_path: Path) -> None:
        """Fedora pre-release 0.1 -- first rebuild is 0.1.1, not 0.2."""
        pkg_dir = _write_spec(tmp_path, "0.1%{?dist}")
        fedora = FedoraOriginWithRelease(
            git_url="https://src.fedoraproject.org/rpms/testpkg.git",
            branch="rawhide", sha="abc", release="0.1",
        )
        metadata = _clean_metadata(fedora=fedora)
        assert bump_release(metadata, pkg_dir) == "0.1.1"

    def test_drifted_release_uses_string_arithmetic(self, tmp_path: Path) -> None:
        """Spec says 5, metadata says 1 -- just bump 5 to 5.1."""
        pkg_dir = _write_spec(tmp_path, "5%{?dist}")
        metadata = _clean_metadata()
        assert bump_release(metadata, pkg_dir) == "5.1"

    def test_modified_fedora_same_as_clean(self, tmp_path: Path) -> None:
        pkg_dir = _write_spec(tmp_path, "1%{?dist}")
        metadata = _modified_fedora_metadata()
        assert bump_release(metadata, pkg_dir) == "1.1"

    def test_preserves_content_after_dist(self, tmp_path: Path) -> None:
        """Content after %{?dist} is preserved (unbound-style)."""
        pkg_dir = _write_spec(
            tmp_path, "1%{?dist} %{?extra_version:-e %{extra_version}}"
        )
        metadata = _clean_metadata()
        bump_release(metadata, pkg_dir)
        content = (pkg_dir / "testpkg.spec").read_text()
        assert "Release: 1.1%{?dist} %{?extra_version:-e %{extra_version}}" in content

    def test_macro_release_with_dist(self, tmp_path: Path) -> None:
        """Macro-based release with %{?dist} gets .N appended to the raw macro."""
        pkg_dir = _write_spec(tmp_path, "%{baserelease}%{?dist}")
        # Write the macro definition so rpmspec can resolve it
        spec = pkg_dir / "testpkg.spec"
        spec.write_text("%define baserelease 42\n" + spec.read_text())
        metadata = _clean_metadata(
            fedora=FedoraOriginWithRelease(
                git_url="https://src.fedoraproject.org/rpms/testpkg.git",
                branch="rawhide", sha="abc", release="42",
            )
        )
        bump_release(metadata, pkg_dir)
        content = spec.read_text()
        assert "%{baserelease}.1%{?dist}" in content

    def test_macro_release_dotted_base_preserves_fedora_suffix(self, tmp_path: Path) -> None:
        """Fedora baseline .1 must not be treated as a rebuild counter.

        With %{baserelease} = 3 and Release: %{baserelease}.1, Fedora's
        baseline is 3.1. The first rebuild appends .1 to produce 3.1.1,
        preserving the macro expression as %{baserelease}.1.1.
        """
        pkg_dir = _write_spec(tmp_path, "%{baserelease}.1%{?dist}")
        spec = pkg_dir / "testpkg.spec"
        spec.write_text("%define baserelease 3\n" + spec.read_text())
        metadata = _clean_metadata(
            fedora=FedoraOriginWithRelease(
                git_url="https://src.fedoraproject.org/rpms/testpkg.git",
                branch="rawhide", sha="abc", release="3.1",
            )
        )
        result = bump_release(metadata, pkg_dir)
        content = spec.read_text()
        assert "%{baserelease}.1.1%{?dist}" in content
        assert result == "3.1.1"

    def test_macro_release_dotted_base_second_rebuild(self, tmp_path: Path) -> None:
        """Second rebuild of a dotted-base macro increments the rebuild counter."""
        pkg_dir = _write_spec(tmp_path, "%{baserelease}.1.1%{?dist}")
        spec = pkg_dir / "testpkg.spec"
        spec.write_text("%define baserelease 3\n" + spec.read_text())
        metadata = _clean_metadata(
            fedora=FedoraOriginWithRelease(
                git_url="https://src.fedoraproject.org/rpms/testpkg.git",
                branch="rawhide", sha="abc", release="3.1",
            )
        )
        result = bump_release(metadata, pkg_dir)
        content = spec.read_text()
        assert "%{baserelease}.1.2%{?dist}" in content
        assert result == "3.1.2"

    def test_macro_release_without_dist_raises(self, tmp_path: Path) -> None:
        """Macro-only release without %{?dist} is rejected."""
        pkg_dir = _write_spec(tmp_path, "%{some_macro}")
        spec = pkg_dir / "testpkg.spec"
        spec.write_text("%define some_macro 7\n" + spec.read_text())
        metadata = _clean_metadata(
            fedora=FedoraOriginWithRelease(
                git_url="https://src.fedoraproject.org/rpms/testpkg.git",
                branch="rawhide", sha="abc", release="7",
            )
        )
        with pytest.raises(ValueError, match="manual rebuild"):
            bump_release(metadata, pkg_dir)


# --- bump_release: modified-upstream / independent (no base) ---


class TestBumpReleaseNoBase:
    """Modified-upstream and independent packages have no fedora.release."""

    def test_independent_simple_bump(self, tmp_path: Path) -> None:
        pkg_dir = _write_spec(tmp_path, "3%{?dist}")
        metadata = _independent_metadata()
        assert bump_release(metadata, pkg_dir) == "3.1"

    def test_independent_increment(self, tmp_path: Path) -> None:
        pkg_dir = _write_spec(tmp_path, "3.1%{?dist}")
        metadata = _independent_metadata()
        assert bump_release(metadata, pkg_dir) == "3.2"

    def test_modified_upstream_simple_bump(self, tmp_path: Path) -> None:
        pkg_dir = _write_spec(tmp_path, "7%{?dist}")
        metadata = _modified_upstream_metadata()
        assert bump_release(metadata, pkg_dir) == "7.1"

    def test_modified_upstream_increment(self, tmp_path: Path) -> None:
        pkg_dir = _write_spec(tmp_path, "7.2%{?dist}")
        metadata = _modified_upstream_metadata()
        assert bump_release(metadata, pkg_dir) == "7.3"

    def test_git_snapshot_release(self, tmp_path: Path) -> None:
        """libyuv-style: long timestamp string with no trailing digit."""
        pkg_dir = _write_spec(tmp_path, "0.62.20260213git6067afd%{?dist}")
        metadata = _independent_metadata()
        assert bump_release(metadata, pkg_dir) == "0.62.20260213git6067afd.1"

    def test_git_snapshot_already_bumped(self, tmp_path: Path) -> None:
        pkg_dir = _write_spec(tmp_path, "0.62.20260213git6067afd.1%{?dist}")
        metadata = _independent_metadata()
        assert bump_release(metadata, pkg_dir) == "0.62.20260213git6067afd.2"


# --- bump_release: real-world Release patterns ---


class TestBumpReleaseRealWorld:
    """Patterns actually seen in the repo's spec files."""

    def test_high_release_number(self, tmp_path: Path) -> None:
        """Packages like coreutils with Release: 527."""
        pkg_dir = _write_spec(tmp_path, "527%{?dist}")
        fedora = FedoraOriginWithRelease(
            git_url="https://src.fedoraproject.org/rpms/testpkg.git",
            branch="rawhide", sha="abc", release="527",
        )
        metadata = _clean_metadata(fedora=fedora)
        assert bump_release(metadata, pkg_dir) == "527.1"

    def test_already_multi_bumped(self, tmp_path: Path) -> None:
        """Release: 525.1 where fedora.release is 525."""
        pkg_dir = _write_spec(tmp_path, "525.1%{?dist}")
        fedora = FedoraOriginWithRelease(
            git_url="https://src.fedoraproject.org/rpms/testpkg.git",
            branch="rawhide", sha="abc", release="525",
        )
        metadata = _clean_metadata(fedora=fedora)
        assert bump_release(metadata, pkg_dir) == "525.2"

    def test_ansible_packaging_dotted_release(self, tmp_path: Path) -> None:
        """ansible-packaging: Fedora ships 20.1."""
        pkg_dir = _write_spec(tmp_path, "20.1%{?dist}")
        fedora = FedoraOriginWithRelease(
            git_url="https://src.fedoraproject.org/rpms/testpkg.git",
            branch="rawhide", sha="abc", release="20.1",
        )
        metadata = _clean_metadata(fedora=fedora)
        assert bump_release(metadata, pkg_dir) == "20.1.1"
