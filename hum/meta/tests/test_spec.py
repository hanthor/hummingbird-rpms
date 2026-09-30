"""Unit tests for hum.meta.spec.

Uses real spec files from this repo's own rpms/<package>/ trees rather than
synthetic fixtures, because the whole point of this module is correctly
driving the real rpmspec tool.
"""

from pathlib import Path

import pytest

from hum.meta.spec import resolve_spec_version

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestResolveSpecVersion:
    def test_resolves_a_real_spec(self) -> None:
        # bash's real, current spec in this repo -- if this ever fails
        # because bash was updated, update the expected value, the
        # function's behavior isn't what's under test here.
        assert resolve_spec_version(REPO_ROOT / "rpms" / "bash") == "5.3.15"

    def test_missing_rpmspec_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "bash.spec").write_text("Name: bash\n")
        monkeypatch.setenv("PATH", str(tmp_path))  # a real PATH with no rpmspec on it
        with pytest.raises(ValueError, match="rpmspec not found on PATH"):
            resolve_spec_version(tmp_path)

    def test_no_spec_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            resolve_spec_version(tmp_path)

    def test_multiple_spec_files_raises(self, tmp_path: Path) -> None:
        (tmp_path / "a.spec").write_text("Name: a\n")
        (tmp_path / "b.spec").write_text("Name: b\n")
        with pytest.raises(ValueError, match="exactly one"):
            resolve_spec_version(tmp_path)

    def test_malformed_spec_raises_with_rpmspec_diagnostics(self, tmp_path: Path) -> None:
        (tmp_path / "bad.spec").write_text("not a valid spec file at all {{{\n")
        with pytest.raises(ValueError, match="rpmspec failed"):
            resolve_spec_version(tmp_path)
