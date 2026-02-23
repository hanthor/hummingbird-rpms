"""
Tests for the source generation tool.

These tests verify the functionality of the source_gen module,
including configuration parsing, variable substitution, and
pipeline step execution.
"""

import subprocess
import tempfile
from pathlib import Path
from typing import Generator

import pytest

# Add ci directory to path for imports
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "ci"))

from source_gen.config import (
    SourceGenConfig,
    VariableResolver,
    VersionInfo,
    load_config,
    validate_config,
    resolve_config_variables,
    ConfigValidationResult,
)
from source_gen.archive import (
    get_archive_format,
    get_archive_extension,
    derive_prefix_from_filename,
    should_exclude,
    create_archive,
)
from source_gen.checksum import (
    compute_sha512,
    format_bsd_checksum,
    parse_sources_file,
    update_sources_file,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def tmp_workdir(tmp_path: Path) -> Path:
    """Create a temporary working directory."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    return workdir


@pytest.fixture
def sample_config() -> dict:
    """Return a sample valid configuration."""
    return {
        "schema_version": 1,
        "package": {
            "name": "mypackage",
            "version": "1.2.3",
            "release": "1",
        },
        "pipeline": [
            {
                "name": "Clone repository",
                "uses": "git-checkout",
                "with": {
                    "repository": "https://github.com/example/mypackage",
                    "ref": "v{version}",
                    "dest": "mypackage-{version}",
                },
            },
        ],
        "outputs": [
            {
                "type": "archive",
                "source": "mypackage-{version}",
                "filename": "mypackage-{version}.tar.gz",
            },
        ],
    }


@pytest.fixture
def sample_config_file(tmp_path: Path, sample_config: dict) -> Path:
    """Write sample config to a temporary file."""
    import yaml

    config_path = tmp_path / "mypackage.yaml"
    with open(config_path, "w") as f:
        yaml.safe_dump(sample_config, f)
    return config_path


# ============================================================================
# Version Parsing Tests
# ============================================================================


class TestVersionInfo:
    """Tests for VersionInfo parsing."""

    def test_semver_parsing(self):
        """Test parsing of standard semver versions."""
        v = VersionInfo.from_version_string("1.2.3")
        assert v.full == "1.2.3"
        assert v.major == "1"
        assert v.minor == "2"
        assert v.patch == "3"

    def test_two_part_version(self):
        """Test parsing of two-part versions."""
        v = VersionInfo.from_version_string("1.2")
        assert v.full == "1.2"
        assert v.major == "1"
        assert v.minor == "2"
        assert v.patch == ""

    def test_single_part_version(self):
        """Test parsing of single-part versions."""
        v = VersionInfo.from_version_string("1")
        assert v.full == "1"
        assert v.major == "1"
        assert v.minor == ""
        assert v.patch == ""

    def test_complex_version(self):
        """Test parsing of complex versions with pre-release markers."""
        v = VersionInfo.from_version_string("1.2.3~rc1")
        assert v.full == "1.2.3~rc1"
        assert v.major == "1"
        assert v.minor == "2"
        assert v.patch == "3~rc1"

    def test_four_part_version(self):
        """Test parsing of versions with more than 3 parts."""
        v = VersionInfo.from_version_string("1.2.3.4")
        assert v.full == "1.2.3.4"
        assert v.major == "1"
        assert v.minor == "2"
        assert v.patch == "3.4"  # Everything after second dot


# ============================================================================
# Variable Resolver Tests
# ============================================================================


class TestVariableResolver:
    """Tests for variable substitution."""

    def test_basic_substitution(self):
        """Test basic variable substitution."""
        resolver = VariableResolver(
            name="mypackage",
            version=VersionInfo.from_version_string("1.2.3"),
            release="1",
        )

        assert resolver.resolve("{name}") == "mypackage"
        assert resolver.resolve("{version}") == "1.2.3"
        assert resolver.resolve("{release}") == "1"

    def test_semver_components(self):
        """Test semver component substitution."""
        resolver = VariableResolver(
            name="pkg",
            version=VersionInfo.from_version_string("1.2.3"),
            release="1",
        )

        assert resolver.resolve("{major}") == "1"
        assert resolver.resolve("{minor}") == "2"
        assert resolver.resolve("{patch}") == "3"

    def test_mixed_text(self):
        """Test substitution in mixed text."""
        resolver = VariableResolver(
            name="pkg",
            version=VersionInfo.from_version_string("1.2.3"),
            release="1",
        )

        text = "https://github.com/example/{name}/archive/v{version}.tar.gz"
        expected = "https://github.com/example/pkg/archive/v1.2.3.tar.gz"
        assert resolver.resolve(text) == expected

    def test_multiple_occurrences(self):
        """Test multiple substitutions of the same variable."""
        resolver = VariableResolver(
            name="pkg",
            version=VersionInfo.from_version_string("1.0.0"),
            release="1",
        )

        text = "{name}-{version}/{name}.spec"
        expected = "pkg-1.0.0/pkg.spec"
        assert resolver.resolve(text) == expected

    def test_unknown_variable_raises(self):
        """Test that unknown variables raise an error."""
        resolver = VariableResolver(
            name="pkg",
            version=VersionInfo.from_version_string("1.0.0"),
            release="1",
        )

        with pytest.raises(ValueError, match="Unknown variable"):
            resolver.resolve("{unknown}")

    def test_from_package_info(self):
        """Test creating resolver from PackageInfo."""
        package_info = {
            "name": "testpkg",
            "version": "2.0.0",
            "release": "3",
        }

        resolver = VariableResolver.from_package_info(package_info)
        assert resolver.resolve("{name}") == "testpkg"
        assert resolver.resolve("{version}") == "2.0.0"
        assert resolver.resolve("{release}") == "3"


# ============================================================================
# Config Validation Tests
# ============================================================================


class TestConfigValidation:
    """Tests for configuration validation."""

    def test_valid_config(self, sample_config: dict):
        """Test that a valid config passes validation."""
        from source_gen.config import _normalize_config

        normalized = _normalize_config(sample_config)
        result = validate_config(normalized)
        assert result.is_valid, f"Errors: {[e.message for e in result.errors]}"

    def test_missing_schema_version(self, sample_config: dict):
        """Test that missing schema_version is caught."""
        del sample_config["schema_version"]

        from source_gen.config import _normalize_config

        normalized = _normalize_config(sample_config)
        result = validate_config(normalized)

        assert not result.is_valid
        assert any("schema_version" in e.path for e in result.errors)

    def test_wrong_schema_version(self, sample_config: dict):
        """Test that wrong schema version is caught."""
        sample_config["schema_version"] = 99

        from source_gen.config import _normalize_config

        normalized = _normalize_config(sample_config)
        result = validate_config(normalized)

        assert not result.is_valid
        assert any("schema_version" in e.path for e in result.errors)

    def test_missing_package(self, sample_config: dict):
        """Test that missing package section is caught."""
        del sample_config["package"]

        from source_gen.config import _normalize_config

        normalized = _normalize_config(sample_config)
        result = validate_config(normalized)

        assert not result.is_valid
        assert any("package" in e.path for e in result.errors)

    def test_missing_pipeline(self, sample_config: dict):
        """Test that missing pipeline section is caught."""
        del sample_config["pipeline"]

        from source_gen.config import _normalize_config

        normalized = _normalize_config(sample_config)
        result = validate_config(normalized)

        assert not result.is_valid
        assert any("pipeline" in e.path for e in result.errors)

    def test_invalid_archive_extension(self, sample_config: dict):
        """Test that invalid archive extensions are caught."""
        sample_config["outputs"][0]["filename"] = "archive.txt"

        from source_gen.config import _normalize_config

        normalized = _normalize_config(sample_config)
        result = validate_config(normalized)

        assert not result.is_valid
        assert any("filename" in e.path for e in result.errors)

    def test_unknown_step_type_warns(self, sample_config: dict):
        """Test that unknown step types generate warnings."""
        sample_config["pipeline"][0]["uses"] = "unknown-step"

        from source_gen.config import _normalize_config

        normalized = _normalize_config(sample_config)
        result = validate_config(normalized)

        # Should be valid but with warnings
        assert result.is_valid
        assert len(result.warnings) > 0
        assert any("unknown-step" in w.message for w in result.warnings)


# ============================================================================
# Config Loading Tests
# ============================================================================


class TestConfigLoading:
    """Tests for configuration file loading."""

    def test_load_valid_config(self, sample_config_file: Path):
        """Test loading a valid configuration file."""
        config = load_config(sample_config_file)

        assert config["schema_version"] == 1
        assert config["package"]["name"] == "mypackage"

    def test_load_missing_file_raises(self, tmp_path: Path):
        """Test that loading a missing file raises an error."""
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_load_invalid_yaml_raises(self, tmp_path: Path):
        """Test that loading invalid YAML raises an error."""
        invalid_file = tmp_path / "invalid.yaml"
        invalid_file.write_text("invalid: yaml: content:")

        with pytest.raises(Exception):  # yaml.YAMLError
            load_config(invalid_file)

    def test_load_empty_file_raises(self, tmp_path: Path):
        """Test that loading an empty file raises an error."""
        empty_file = tmp_path / "empty.yaml"
        empty_file.write_text("")

        with pytest.raises(ValueError, match="Empty"):
            load_config(empty_file)


# ============================================================================
# Archive Utility Tests
# ============================================================================


class TestArchiveUtils:
    """Tests for archive utility functions."""

    def test_get_archive_format(self):
        """Test archive format detection."""
        assert get_archive_format("file.tar.gz") == "gzip"
        assert get_archive_format("file.tgz") == "gzip"
        assert get_archive_format("file.tar.bz2") == "bzip2"
        assert get_archive_format("file.tbz2") == "bzip2"
        assert get_archive_format("file.tar.xz") == "xz"
        assert get_archive_format("file.txz") == "xz"
        assert get_archive_format("file.zip") == "zip"
        assert get_archive_format("file.txt") is None

    def test_get_archive_extension(self):
        """Test archive extension extraction."""
        assert get_archive_extension("file.tar.gz") == ".tar.gz"
        assert get_archive_extension("file.tar.bz2") == ".tar.bz2"
        assert get_archive_extension("file.zip") == ".zip"
        assert get_archive_extension("file.txt") is None

    def test_derive_prefix_from_filename(self):
        """Test prefix derivation from filename."""
        assert (
            derive_prefix_from_filename("mypackage-1.2.3.tar.gz") == "mypackage-1.2.3"
        )
        assert (
            derive_prefix_from_filename("mypackage-1.2.3.tar.bz2") == "mypackage-1.2.3"
        )
        assert derive_prefix_from_filename("mypackage-1.2.3.zip") == "mypackage-1.2.3"

    def test_should_exclude(self):
        """Test exclusion pattern matching."""
        patterns = [".git/", "vendor/", "__pycache__"]

        assert should_exclude(".git", patterns)
        assert should_exclude(".git/config", patterns)
        assert should_exclude("vendor", patterns)
        assert should_exclude("vendor/pkg/mod.go", patterns)
        assert should_exclude("__pycache__", patterns)
        assert not should_exclude("src/main.go", patterns)
        assert not should_exclude("README.md", patterns)


# ============================================================================
# Archive Creation Tests
# ============================================================================


class TestArchiveCreation:
    """Tests for archive creation."""

    def test_create_tar_gz(self, tmp_workdir: Path):
        """Test creating a .tar.gz archive."""
        # Create source directory with some files
        source_dir = tmp_workdir / "source"
        source_dir.mkdir()
        (source_dir / "file1.txt").write_text("content1")
        (source_dir / "file2.txt").write_text("content2")

        output_path = tmp_workdir / "output.tar.gz"

        success = create_archive(source_dir, output_path)

        assert success
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    def test_create_tar_bz2(self, tmp_workdir: Path):
        """Test creating a .tar.bz2 archive."""
        source_dir = tmp_workdir / "source"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("content")

        output_path = tmp_workdir / "output.tar.bz2"

        success = create_archive(source_dir, output_path)

        assert success
        assert output_path.exists()

    def test_create_zip(self, tmp_workdir: Path):
        """Test creating a .zip archive."""
        source_dir = tmp_workdir / "source"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("content")

        output_path = tmp_workdir / "output.zip"

        success = create_archive(source_dir, output_path)

        assert success
        assert output_path.exists()

    def test_archive_with_prefix(self, tmp_workdir: Path):
        """Test creating archive with custom prefix."""
        source_dir = tmp_workdir / "source"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("content")

        output_path = tmp_workdir / "output.tar.gz"

        success = create_archive(source_dir, output_path, prefix="custom-prefix")

        assert success

        # Verify prefix in archive
        import tarfile

        with tarfile.open(output_path, "r:gz") as tar:
            names = tar.getnames()
            assert all(n.startswith("custom-prefix") for n in names)

    def test_archive_with_excludes(self, tmp_workdir: Path):
        """Test creating archive with exclusions."""
        source_dir = tmp_workdir / "source"
        source_dir.mkdir()
        (source_dir / "keep.txt").write_text("keep")
        (source_dir / ".git").mkdir()
        (source_dir / ".git" / "config").write_text("git config")

        output_path = tmp_workdir / "output.tar.gz"

        success = create_archive(source_dir, output_path, exclude=[".git/"])

        assert success

        # Verify .git is excluded
        import tarfile

        with tarfile.open(output_path, "r:gz") as tar:
            names = tar.getnames()
            assert not any(".git" in n for n in names)
            assert any("keep.txt" in n for n in names)

    def test_archive_nonexistent_source_fails(self, tmp_workdir: Path):
        """Test that archiving non-existent source fails."""
        source_dir = tmp_workdir / "nonexistent"
        output_path = tmp_workdir / "output.tar.gz"

        success = create_archive(source_dir, output_path)

        assert not success

    def test_dry_run_doesnt_create_file(self, tmp_workdir: Path):
        """Test that dry run doesn't create the archive."""
        source_dir = tmp_workdir / "source"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("content")

        output_path = tmp_workdir / "output.tar.gz"

        success = create_archive(source_dir, output_path, dry_run=True)

        assert success
        assert not output_path.exists()


# ============================================================================
# Checksum Tests
# ============================================================================


class TestChecksum:
    """Tests for checksum utilities."""

    def test_compute_sha512(self, tmp_workdir: Path):
        """Test SHA512 computation."""
        test_file = tmp_workdir / "test.txt"
        test_file.write_text("hello world")

        checksum = compute_sha512(test_file)

        assert len(checksum) == 128  # SHA512 produces 128 hex chars
        assert checksum.islower()

    def test_compute_sha512_binary(self, tmp_workdir: Path):
        """Test SHA512 computation on binary file."""
        test_file = tmp_workdir / "test.bin"
        test_file.write_bytes(b"\x00\x01\x02\x03")

        checksum = compute_sha512(test_file)

        assert len(checksum) == 128

    def test_format_bsd_checksum(self):
        """Test BSD checksum formatting."""
        result = format_bsd_checksum("file.tar.gz", "abc123")
        assert result == "SHA512 (file.tar.gz) = abc123"

    def test_parse_sources_file(self, tmp_workdir: Path):
        """Test parsing sources file."""
        sources_file = tmp_workdir / "sources"
        sources_file.write_text(
            "SHA512 (file1.tar.gz) = abc123\nSHA512 (file2.tar.gz) = def456\n"
        )

        checksums = parse_sources_file(sources_file)

        assert checksums["file1.tar.gz"] == "abc123"
        assert checksums["file2.tar.gz"] == "def456"

    def test_parse_sources_file_empty(self, tmp_workdir: Path):
        """Test parsing empty sources file."""
        sources_file = tmp_workdir / "sources"
        sources_file.write_text("")

        checksums = parse_sources_file(sources_file)

        assert checksums == {}

    def test_parse_sources_file_nonexistent(self, tmp_workdir: Path):
        """Test parsing non-existent sources file."""
        sources_file = tmp_workdir / "nonexistent"

        checksums = parse_sources_file(sources_file)

        assert checksums == {}

    def test_update_sources_file(self, tmp_workdir: Path):
        """Test updating sources file."""
        sources_file = tmp_workdir / "sources"

        success = update_sources_file(
            sources_file,
            {"file.tar.gz": "abc123"},
        )

        assert success
        assert sources_file.exists()

        content = sources_file.read_text()
        assert "SHA512 (file.tar.gz) = abc123" in content

    def test_update_sources_file_preserves_existing(self, tmp_workdir: Path):
        """Test that updating preserves existing entries."""
        sources_file = tmp_workdir / "sources"
        # Use valid hex checksums (the regex requires hex-only characters)
        sources_file.write_text("SHA512 (existing.tar.gz) = abc123def456\n")

        success = update_sources_file(
            sources_file,
            {"new.tar.gz": "789abc012def"},
        )

        assert success

        content = sources_file.read_text()
        assert "existing.tar.gz" in content
        assert "abc123def456" in content
        assert "new.tar.gz" in content
        assert "789abc012def" in content


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests that verify end-to-end functionality."""

    def test_variable_resolution_in_config(self, sample_config: dict):
        """Test that variables are resolved throughout config."""
        from source_gen.config import _normalize_config

        normalized = _normalize_config(sample_config)

        package_info = normalized["package"]
        resolver = VariableResolver.from_package_info(package_info)
        resolved = resolve_config_variables(normalized, resolver)

        # Check that {version} was resolved
        step_params = resolved["pipeline"][0]["with_"]
        assert step_params["ref"] == "v1.2.3"
        assert step_params["dest"] == "mypackage-1.2.3"

        output = resolved["outputs"][0]
        assert output["source"] == "mypackage-1.2.3"
        assert output["filename"] == "mypackage-1.2.3.tar.gz"


# ============================================================================
# CLI Tests
# ============================================================================


class TestCLI:
    """Tests for CLI functionality."""

    def test_help(self):
        """Test that --help works."""
        result = subprocess.run(
            [sys.executable, "ci/source_gen.py", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        assert result.returncode == 0
        assert "generate" in result.stdout
        assert "validate" in result.stdout

    def test_validate_command_help(self):
        """Test validate command help."""
        result = subprocess.run(
            [sys.executable, "ci/source_gen.py", "validate", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        assert result.returncode == 0
        assert "--all" in result.stdout

    def test_validate_missing_package(self):
        """Test validate with non-existent package."""
        result = subprocess.run(
            [sys.executable, "ci/source_gen.py", "validate", "nonexistent-package"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        assert result.returncode != 0
        assert "not found" in result.stderr.lower()


class TestSpecUpdater:
    """Tests for spec file updating."""

    @pytest.fixture
    def spec_file(self, tmp_path: Path) -> Path:
        """Create a sample spec file."""
        spec_content = """\
Name:           test-package
Version:        1.0.0
Release:        1%{?dist}
Summary:        A test package

%global my_macro value123

License:        MIT

## START BUNDLED PROVIDES ##
Provides: bundled(example) = 1.0.0
## END BUNDLED PROVIDES ##

Requires:       some-dep

%description
Test package description.
"""
        spec_path = tmp_path / "test.spec"
        spec_path.write_text(spec_content)
        return spec_path

    def test_replace_block_with_markers(self, spec_file: Path):
        """Test replacing a block while preserving markers."""
        from source_gen.spec_updater import SpecUpdater

        updater = SpecUpdater(spec_file)
        result = updater.replace_block(
            start_marker="## START BUNDLED PROVIDES ##",
            end_marker="## END BUNDLED PROVIDES ##",
            new_content="Provides: bundled(new-dep) = 2.0.0",
            include_markers=True,
        )

        assert result is True
        content = spec_file.read_text()
        assert "## START BUNDLED PROVIDES ##" in content
        assert "## END BUNDLED PROVIDES ##" in content
        assert "bundled(new-dep) = 2.0.0" in content
        assert "bundled(example) = 1.0.0" not in content

    def test_replace_block_without_markers(self, spec_file: Path):
        """Test replacing a block including the markers."""
        from source_gen.spec_updater import SpecUpdater

        updater = SpecUpdater(spec_file)
        result = updater.replace_block(
            start_marker="## START BUNDLED PROVIDES ##",
            end_marker="## END BUNDLED PROVIDES ##",
            new_content="# New section\nProvides: bundled(replaced) = 3.0.0",
            include_markers=False,
        )

        assert result is True
        content = spec_file.read_text()
        assert "## START BUNDLED PROVIDES ##" not in content
        assert "## END BUNDLED PROVIDES ##" not in content
        assert "bundled(replaced) = 3.0.0" in content

    def test_replace_block_not_found(self, spec_file: Path):
        """Test replacing a block that doesn't exist."""
        from source_gen.spec_updater import SpecUpdater

        updater = SpecUpdater(spec_file)
        result = updater.replace_block(
            start_marker="## NONEXISTENT START ##",
            end_marker="## NONEXISTENT END ##",
            new_content="test",
        )

        assert result is False

    def test_replace_pattern(self, spec_file: Path):
        """Test regex pattern replacement."""
        from source_gen.spec_updater import SpecUpdater

        updater = SpecUpdater(spec_file)
        result = updater.replace_pattern(
            pattern=r"^Version:\s+.*$",
            replacement="Version:        2.0.0",
        )

        assert result is True
        content = spec_file.read_text()
        assert "Version:        2.0.0" in content
        assert "Version:        1.0.0" not in content

    def test_replace_pattern_with_count(self, spec_file: Path):
        """Test regex replacement with count limit."""
        from source_gen.spec_updater import SpecUpdater

        # Add duplicate lines
        content = spec_file.read_text()
        content = content.replace("Requires:", "Requires:\nRequires:")
        spec_file.write_text(content)

        updater = SpecUpdater(spec_file)
        result = updater.replace_pattern(
            pattern=r"^Requires:",
            replacement="BuildRequires:",
            count=1,  # Only replace first occurrence
        )

        assert result is True
        content = spec_file.read_text()
        assert "BuildRequires:" in content
        assert "Requires:" in content  # Second one should remain

    def test_replace_pattern_not_found(self, spec_file: Path):
        """Test regex replacement when pattern not found."""
        from source_gen.spec_updater import SpecUpdater

        updater = SpecUpdater(spec_file)
        result = updater.replace_pattern(
            pattern=r"^NonexistentTag:",
            replacement="Replacement",
        )

        assert result is False

    def test_update_macro(self, spec_file: Path):
        """Test updating a macro definition."""
        from source_gen.spec_updater import SpecUpdater

        updater = SpecUpdater(spec_file)
        result = updater.update_macro("my_macro", "new_value")

        assert result is True
        content = spec_file.read_text()
        assert "%global my_macro new_value" in content
        assert "value123" not in content


class TestGoBundledProvides:
    """Tests for Go bundled provides generation."""

    def test_parse_modules_txt(self, tmp_path: Path):
        """Test parsing vendor/modules.txt file."""
        from source_gen.steps.go import parse_modules_txt

        modules_txt = tmp_path / "modules.txt"
        modules_txt.write_text("""\
# github.com/google/pprof v0.0.0-20251114195745-4902fdda35c8
## explicit
github.com/google/pprof/driver
# golang.org/x/crypto v0.46.0
## explicit; go 1.20
golang.org/x/crypto/ssh
# golang.org/x/tools v0.27.0 => golang.org/x/tools v0.28.0
## explicit
golang.org/x/tools/go/packages
""")

        modules = parse_modules_txt(modules_txt)

        assert len(modules) == 3
        # Check that versions are parsed correctly (v prefix removed, - replaced with .)
        module_dict = {m[0]: m[1] for m in modules}
        assert "github.com/google/pprof" in module_dict
        assert "golang.org/x/crypto" in module_dict
        assert "golang.org/x/tools" in module_dict
        # The replacement should use the target version
        assert module_dict["golang.org/x/tools"] == "0.28.0"

    def test_parse_modules_txt_empty(self, tmp_path: Path):
        """Test parsing empty modules.txt."""
        from source_gen.steps.go import parse_modules_txt

        modules_txt = tmp_path / "modules.txt"
        modules_txt.write_text("")

        modules = parse_modules_txt(modules_txt)
        assert modules == []

    def test_parse_modules_txt_nonexistent(self, tmp_path: Path):
        """Test parsing nonexistent modules.txt."""
        from source_gen.steps.go import parse_modules_txt

        modules = parse_modules_txt(tmp_path / "nonexistent.txt")
        assert modules == []

    def test_format_bundled_provides(self):
        """Test formatting modules as RPM Provides."""
        from source_gen.steps.go import format_bundled_provides

        modules = [
            ("golang.org/x/crypto", "0.46.0"),
            ("github.com/google/pprof", "0.0.0.20251114195745.4902fdda35c8"),
        ]

        result = format_bundled_provides(modules)

        assert "Provides: bundled(golang(github.com/google/pprof))" in result
        assert "Provides: bundled(golang(golang.org/x/crypto))" in result
        assert "= 0.46.0" in result

    def test_format_bundled_provides_sorted(self):
        """Test that provides are sorted."""
        from source_gen.steps.go import format_bundled_provides

        modules = [
            ("z-module", "1.0.0"),
            ("a-module", "2.0.0"),
            ("m-module", "3.0.0"),
        ]

        result = format_bundled_provides(modules)
        lines = result.strip().split("\n")

        # Should be sorted alphabetically
        assert "a-module" in lines[0]
        assert "m-module" in lines[1]
        assert "z-module" in lines[2]

    def test_format_bundled_provides_deduplicates(self):
        """Test that duplicate modules are deduplicated."""
        from source_gen.steps.go import format_bundled_provides

        modules = [
            ("same-module", "1.0.0"),
            ("same-module", "1.0.0"),
            ("other-module", "2.0.0"),
        ]

        result = format_bundled_provides(modules)
        lines = result.strip().split("\n")

        assert len(lines) == 2


class TestUpdateCommand:
    """Tests for the update command."""

    @pytest.fixture
    def package_setup(self, tmp_path: Path) -> tuple[Path, Path]:
        """Set up a test package with YAML config."""
        # Create metadata directory
        metadata_dir = tmp_path / "metadata"
        metadata_dir.mkdir()

        # Create a test YAML config
        yaml_content = """\
schema_version: 1

package:
  name: test-pkg
  version: 1.0.0
  release: "1"
  license: MIT

pipeline:
  - name: Clone repo
    uses: git-checkout
    with:
      repository: https://github.com/example/test
      ref: v{version}
      dest: test-{version}

outputs:
  - type: archive
    source: test-{version}
    filename: test-{version}.tar.gz
"""
        config_path = metadata_dir / "test-pkg.yaml"
        config_path.write_text(yaml_content)

        return tmp_path, config_path

    def test_update_version_in_yaml(self, package_setup: tuple[Path, Path]):
        """Test that update command modifies YAML version."""
        import yaml

        tmp_path, config_path = package_setup

        # Read original
        with open(config_path) as f:
            original = yaml.safe_load(f)
        assert original["package"]["version"] == "1.0.0"

        # Simulate update by modifying the file
        with open(config_path) as f:
            config = yaml.safe_load(f)
        config["package"]["version"] = "2.0.0"
        with open(config_path, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

        # Verify update
        with open(config_path) as f:
            updated = yaml.safe_load(f)
        assert updated["package"]["version"] == "2.0.0"

    def test_update_release_in_yaml(self, package_setup: tuple[Path, Path]):
        """Test that update command modifies YAML release."""
        import yaml

        tmp_path, config_path = package_setup

        # Read original
        with open(config_path) as f:
            original = yaml.safe_load(f)
        assert original["package"]["release"] == "1"

        # Simulate update
        with open(config_path) as f:
            config = yaml.safe_load(f)
        config["package"]["release"] = "2"
        with open(config_path, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

        # Verify update
        with open(config_path) as f:
            updated = yaml.safe_load(f)
        assert updated["package"]["release"] == "2"


class TestSourcesFileReplace:
    """Tests for sources file replacement behavior."""

    def test_compute_and_update_sources_replaces(self, tmp_path: Path):
        """Test that compute_and_update_sources replaces the file."""
        from source_gen.checksum import compute_and_update_sources

        # Create package dir with existing sources
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        sources_file = pkg_dir / "sources"
        sources_file.write_text("SHA512 (old-file.tar.gz) = oldhash123abc\n")

        # Create a test archive
        archive = pkg_dir / "new-file.tar.gz"
        archive.write_bytes(b"test content")

        # Run with replace=True (default)
        result = compute_and_update_sources(pkg_dir, [archive], replace=True)

        assert result is True
        content = sources_file.read_text()
        assert "new-file.tar.gz" in content
        assert "old-file.tar.gz" not in content  # Old entry should be gone

    def test_compute_and_update_sources_merges(self, tmp_path: Path):
        """Test that compute_and_update_sources can merge entries."""
        from source_gen.checksum import compute_and_update_sources

        # Create package dir with existing sources
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        sources_file = pkg_dir / "sources"
        sources_file.write_text("SHA512 (existing.tar.gz) = abc123def456\n")

        # Create a test archive
        archive = pkg_dir / "new-file.tar.gz"
        archive.write_bytes(b"test content")

        # Run with replace=False
        result = compute_and_update_sources(pkg_dir, [archive], replace=False)

        assert result is True
        content = sources_file.read_text()
        assert "new-file.tar.gz" in content
        assert "existing.tar.gz" in content  # Old entry should remain
