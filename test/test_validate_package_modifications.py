"""Tests for validate_package_modifications."""

import json
import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import yaml


#
# Fixtures
#


@pytest.fixture
def validator(tmp_path: Path):
    """Load validate_package_modifications.py with paths pointed at tmp_path."""
    # Set up a git repo so the module's dist_git import can call git
    subprocess.run(['git', 'init'], cwd=tmp_path, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=tmp_path, check=True)

    (tmp_path / 'rpms').mkdir()
    (tmp_path / 'metadata').mkdir()
    (tmp_path / 'ci').mkdir()

    subprocess.run(['git', 'add', '.'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit', '--allow-empty'],
                   cwd=tmp_path, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Load the module with ROOT_DIR/RPMS_DIR/METADATA_DIR patched via dist_git
    script_path = Path(__file__).parent.parent / 'ci' / 'validate_package_modifications.py'

    # First, load dist_git so we can patch its paths before validate imports them
    dist_git_path = Path(__file__).parent.parent / 'ci' / 'dist_git.py'
    dg = types.ModuleType('dist_git')
    dg.__file__ = str(dist_git_path)
    code = compile(dist_git_path.read_text(), str(dist_git_path), 'exec')
    exec(code, dg.__dict__)
    sys.modules['dist_git'] = dg

    # Patch dist_git paths so validate_package_modifications picks them up
    dg.ROOT_DIR = tmp_path  # type: ignore[attr-defined]
    dg.RPMS_DIR = tmp_path / 'rpms'  # type: ignore[attr-defined]
    dg.METADATA_DIR = tmp_path / 'metadata'  # type: ignore[attr-defined]
    dg.PACKAGE_OVERRIDES_YAML = tmp_path / 'ci' / 'package-overrides.yaml'  # type: ignore[attr-defined]
    dg._METADATA_REPO = dg.MetadataRepository(tmp_path)  # type: ignore[attr-defined]

    module = types.ModuleType('validate_package_modifications')
    module.__file__ = str(script_path)
    code = compile(script_path.read_text(), str(script_path), 'exec')
    exec(code, module.__dict__)

    # Also patch the module's own references
    module.METADATA_DIR = tmp_path / 'metadata'  # type: ignore[attr-defined]
    module.RPMS_DIR = tmp_path / 'rpms'  # type: ignore[attr-defined]
    module.ROOT_DIR = tmp_path  # type: ignore[attr-defined]
    module.PACKAGE_OVERRIDES_YAML = tmp_path / 'ci' / 'package-overrides.yaml'  # type: ignore[attr-defined]
    module._METADATA_REPO = dg._METADATA_REPO  # type: ignore[attr-defined]

    module._tmp_path = tmp_path  # type: ignore[attr-defined]
    return module


def _create_package(root: Path, name: str, metadata: dict) -> None:
    """Create a package directory with a spec file and metadata."""
    pkg_dir = root / 'rpms' / name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    spec = pkg_dir / f'{name}.spec'
    spec.write_text(f"""\
Name: {name}
Version: 1.0
Release: 1%{{?dist}}
Summary: Test
License: MIT

%description
Test
""")

    meta_file = root / 'metadata' / f'{name}.json'
    with open(meta_file, 'w') as f:
        json.dump(metadata, f, indent=2)
        f.write('\n')


#
# Tests — superseded historical package commits
#


def test_superseded_qt6_cve_backport_does_not_mark_package_modified(validator, monkeypatch) -> None:
    """The Qt 6.12 update supersedes the historical CVE-2026-15037 backport."""
    commit = '376c36ccdf1f4d8b2ec652575e77a79c7878e97f'

    def fake_run_git(*args, **kwargs):
        return subprocess.CompletedProcess(
            args, 0, f'{commit} Backport CVE-2026-15037 fix to qt6-qtbase\n', ''
        )

    monkeypatch.setattr(validator, 'run_git', fake_run_git)

    assert validator.check_git_history_state('qt6-qtbase', 'sync-sha') == (True, None)


#
# Tests — track_upstream type validation
#


def test_track_upstream_boolean_rejected(validator) -> None:
    """track_upstream=true (boolean) fails validation; must be a string."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'clean',
        'version': '1.0',
        'fedora': {'git_url': 'https://example.com', 'branch': 'main', 'sha': 'abc123', 'release': '1'},
        'upstream_repo': 'https://example.com',
        'track_upstream': True,
    })
    # Commit the package so git history checks have something
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'Sync pkg from 1.0-1 to 1.0-1',
                     '--trailer', 'Upstream: https://example.com'],
                   cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert not valid
    assert 'track_upstream' in error
    assert 'str' in error


def test_track_upstream_string_latest_accepted(validator) -> None:
    """track_upstream="latest" (string) passes validation."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'clean',
        'version': '1.0',
        'fedora': {'git_url': 'https://example.com', 'branch': 'main', 'sha': 'abc123', 'release': '1'},
        'upstream_repo': 'https://example.com',
        'track_upstream': 'latest',
        'release_monitoring_project_id': 12345,
    })
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'Sync pkg from 1.0-1 to 1.0-1',
                     '--trailer', 'Upstream: https://example.com'],
                   cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert valid, f"Expected valid but got: {error}"


def test_track_upstream_version_prefix_accepted(validator) -> None:
    """track_upstream="1.26" (version prefix string) passes validation."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'clean',
        'version': '1.0',
        'fedora': {'git_url': 'https://example.com', 'branch': 'main', 'sha': 'abc123', 'release': '1'},
        'upstream_repo': 'https://example.com',
        'track_upstream': '1.26',
        'release_monitoring_project_id': 12345,
    })
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'Sync pkg from 1.0-1 to 1.0-1',
                     '--trailer', 'Upstream: https://example.com'],
                   cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert valid, f"Expected valid but got: {error}"


def test_track_upstream_integer_rejected(validator) -> None:
    """track_upstream=42 (integer) fails validation."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'clean',
        'version': '1.0',
        'fedora': {'git_url': 'https://example.com', 'branch': 'main', 'sha': 'abc123', 'release': '1'},
        'upstream_repo': 'https://example.com',
        'track_upstream': 42,
    })
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'Sync pkg from 1.0-1 to 1.0-1',
                     '--trailer', 'Upstream: https://example.com'],
                   cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert not valid
    assert 'track_upstream' in error
    assert 'str' in error


#
# Tests — independent packages require forked_from/lookaside_cache_url
#


def _write_overrides(root: Path, overrides: dict) -> None:
    (root / 'ci' / 'package-overrides.yaml').write_text(yaml.safe_dump(overrides))


def _write_sources(root: Path, name: str, content: str) -> None:
    (root / 'rpms' / name / 'sources').write_text(content)


def test_independent_package_missing_lookaside_override_rejected(validator) -> None:
    """An independent package with remote sources but no forked_from/
    lookaside_cache_url override fails.

    Without one of these, the build's source-fetch step defaults to Fedora's
    lookaside cache, where an independent package's sources were never uploaded.
    """
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'independent',
        'version': '1.0',
        'upstream_repo': 'https://example.com',
    })
    _write_sources(root, 'pkg', 'SHA512 (pkg-1.0.tar.gz) = abc123\n')
    _write_overrides(root, {})

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert not valid
    assert 'forked_from' in error


def test_independent_package_with_no_overrides_file_and_sources_rejected(validator) -> None:
    """An independent package with sources but no overrides file at all still fails.

    Exercises the PACKAGE_OVERRIDES_YAML.exists() == False branch, which
    falls back to an empty overrides dict rather than erroring out.
    """
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'independent',
        'version': '1.0',
        'upstream_repo': 'https://example.com',
    })
    _write_sources(root, 'pkg', 'SHA512 (pkg-1.0.tar.gz) = abc123\n')
    # Deliberately do NOT write ci/package-overrides.yaml

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert not valid
    assert 'forked_from' in error


def test_independent_package_with_forked_from_accepted(validator) -> None:
    """An independent package with forked_from set passes validation."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'independent',
        'version': '1.0',
        'upstream_repo': 'https://example.com',
    })
    _write_sources(root, 'pkg', 'SHA512 (pkg-1.0.tar.gz) = abc123\n')
    _write_overrides(root, {
        'pkg': {'forked_from': 'https://gitlab.com/redhat/hummingbird/rpms'},
    })

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert valid, f"Expected valid but got: {error}"


def test_independent_package_with_lookaside_cache_url_accepted(validator) -> None:
    """An independent package with lookaside_cache_url set passes validation."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'independent',
        'version': '1.0',
        'upstream_repo': 'https://example.com',
    })
    _write_sources(root, 'pkg', 'SHA512 (pkg-1.0.tar.gz) = abc123\n')
    _write_overrides(root, {
        'pkg': {'lookaside_cache_url': 'https://example.cloudfront.net/'},
    })

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert valid, f"Expected valid but got: {error}"


def test_independent_package_with_no_sources_file_accepted(validator) -> None:
    """An independent package with no `sources` file (all Source files shipped
    locally, e.g. hummingbird-release) never hits the lookaside fetch path,
    so it doesn't need forked_from/lookaside_cache_url.
    """
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'independent',
        'version': '1.0',
        'upstream_repo': 'https://example.com',
    })
    _write_overrides(root, {})

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert valid, f"Expected valid but got: {error}"


def test_independent_package_release_rejected(validator) -> None:
    """Independent packages cannot have a top-level release field;
    the library rejects it as an unrecognized field."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'independent',
        'release': '1',
    })

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert not valid
    assert "unrecognized field" in error and "release" in error


def test_modified_package_without_fedora_rejected(validator) -> None:
    """A modified package must have a fedora origin block."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'modified',
        'version': '1.0',
        'upstream_repo': 'https://example.com',
        'modification_reason': 'Local change',
    })

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert not valid
    assert 'fedora' in error.lower()


#
# Tests — get_changed_packages_in_mr()
#


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_get_changed_packages_in_mr_uses_diff_base_sha_across_disconnected_history(
    validator, tmp_path_factory, monkeypatch,
):
    """CI_MERGE_REQUEST_DIFF_BASE_SHA is GitLab's own merge-base for this
    MR's diff. Using it directly (two-commit diff, no ancestry search) must
    work even when the base commit and HEAD share no local common ancestor
    -- exactly the shallow-clone shape that makes a origin/<target>...HEAD
    triple-dot diff fail with "no merge base" in a long-lived MR (see
    ci/check_nevr_conflicts.py's get_changed_rpm_packages(), which hit this
    in practice in !3887)."""
    root = validator.ROOT_DIR  # already an initialized git repo, per the `validator` fixture

    # Kept fully outside root's working tree (not a subdirectory of it) so
    # it can never be mistaken for a nested repo/submodule by a future
    # `git add .` in either repo.
    remote_repo = tmp_path_factory.mktemp('validate-remote')
    _git(['init', '--initial-branch=main'], remote_repo)
    _git(['config', 'user.name', 'Test'], remote_repo)
    _git(['config', 'user.email', 'test@example.com'], remote_repo)
    (remote_repo / 'rpms' / 'unchanged-pkg').mkdir(parents=True)
    (remote_repo / 'rpms' / 'unchanged-pkg' / 'unchanged-pkg.spec').write_text('v1\n')
    _git(['add', '.'], remote_repo)
    _git(['commit', '-m', 'Base commit'], remote_repo)
    base_sha = _git(['rev-parse', 'HEAD'], remote_repo).stdout.strip()

    _git(['remote', 'add', 'origin', str(remote_repo)], root)
    # Same path + content as the remote's base -- must NOT show as changed.
    (root / 'rpms' / 'unchanged-pkg').mkdir(parents=True)
    (root / 'rpms' / 'unchanged-pkg' / 'unchanged-pkg.spec').write_text('v1\n')
    # A genuinely new package -- must show as changed.
    (root / 'rpms' / 'changed-pkg').mkdir(parents=True)
    (root / 'rpms' / 'changed-pkg' / 'changed-pkg.spec').write_text('v1\n')
    _git(['add', 'rpms'], root)
    _git(['commit', '-m', 'MR commit'], root)

    monkeypatch.setenv('CI_MERGE_REQUEST_DIFF_BASE_SHA', base_sha)
    monkeypatch.delenv('CI_MERGE_REQUEST_TARGET_BRANCH_NAME', raising=False)

    result = validator.get_changed_packages_in_mr()

    assert result == ['changed-pkg']


def test_get_changed_packages_in_mr_diff_base_sha_fetch_failure_raises(validator, monkeypatch):
    """A bogus/unfetchable CI_MERGE_REQUEST_DIFF_BASE_SHA must fail (no
    'origin' remote is configured in the `validator` fixture's repo).

    Unlike check_nevr_conflicts.py (which has its own NevrCheckError to
    wrap failures in), this module has no custom exception type, so the
    function logs a clarifying message and then re-raises the original
    subprocess.CalledProcessError unchanged rather than inventing one just
    for this call site."""
    monkeypatch.setenv('CI_MERGE_REQUEST_DIFF_BASE_SHA', '0' * 40)
    with patch('time.sleep') as mock_sleep, \
         patch.object(validator.subprocess, 'run', wraps=validator.subprocess.run) as mock_run, \
         pytest.raises(subprocess.CalledProcessError) as exc_info:
        validator.get_changed_packages_in_mr()

    assert mock_run.call_count == 3
    assert exc_info.value.cmd == ['git', 'fetch', '--quiet', 'origin', '0' * 40]
    assert mock_sleep.call_args_list == [call(2), call(4)]


def test_get_changed_packages_in_mr_fallback_to_target_branch(validator, monkeypatch):
    """When CI_MERGE_REQUEST_DIFF_BASE_SHA is not set (e.g. running locally
    outside a GitLab MR pipeline), falls back to the pre-existing
    origin/<target>...HEAD triple-dot diff behavior."""
    root = validator.ROOT_DIR
    base_sha = _git(['rev-parse', 'HEAD'], root).stdout.strip()
    _git(['update-ref', 'refs/remotes/origin/main', base_sha], root)

    (root / 'rpms' / 'changed-pkg').mkdir(parents=True)
    (root / 'rpms' / 'changed-pkg' / 'changed-pkg.spec').write_text('v1\n')
    (root / 'metadata' / 'meta-only-pkg.json').write_text('{}\n')
    _git(['add', '.'], root)
    _git(['commit', '-m', 'MR commit'], root)

    monkeypatch.delenv('CI_MERGE_REQUEST_DIFF_BASE_SHA', raising=False)
    monkeypatch.delenv('CI_MERGE_REQUEST_TARGET_BRANCH_NAME', raising=False)

    result = validator.get_changed_packages_in_mr()

    assert result == ['changed-pkg', 'meta-only-pkg']


def _commit(root: Path, message: str, date: str = '2026-04-01T12:00:00+00:00') -> str:
    _git(['add', '-A'], root)
    subprocess.run(
        ['git', 'commit', '--allow-empty', '-m', message], cwd=root, check=True,
        capture_output=True, text=True,
        env={**os.environ, 'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date},
    )
    return _git(['rev-parse', 'HEAD'], root).stdout.strip()


def _fedora_metadata() -> dict:
    return {
        'modification_status': 'clean', 'version': '1.0',
        'upstream_repo': 'https://example.com/pkg',
        'fedora': {'git_url': 'https://example.com/pkg.git', 'branch': 'rawhide',
                   'sha': 'abc123', 'release': '1'},
    }


@pytest.mark.parametrize('pattern_type', [None, 'fixed', 'extended', 'extended-config'])
def test_sync_index_matches_native_grep(validator, pattern_type: str | None) -> None:
    root = validator.ROOT_DIR
    packages = ['pkg', 'pkg-1', 'pkg-child', 'pkg+extra', 'pkg.1', 'never']
    _commit(root, 'Import pkg-1-2')
    _commit(root, 'Import pkg-child-1.0')
    _commit(root, 'Sync pkg-child to 1.0 (mark)')
    _commit(root, 'Notes\n\nSync pkg+extra to 1.0 (mark)')
    _commit(root, 'Notes\n\nImport pkgx1-1.0')
    _commit(root, 'Import never-beta')
    _commit(root, 'Notes\n\n^Sync pkg to 1.0 (mark)')
    if pattern_type == 'extended-config':
        _git(['config', 'grep.extendedRegexp', 'true'], root)
    elif pattern_type:
        _git(['config', 'grep.patternType', pattern_type], root)

    history = validator.GitHistory(packages)
    for package in packages:
        assert history.last_sync_commit(package) == validator.find_last_sync_commit(package)
    if pattern_type is None:
        assert history.last_sync_commit('never') is None
        assert history.last_sync_commit('pkg') == history.last_sync_commit('pkg-1')


@pytest.mark.parametrize('merge_mode', [None, 'separate'])
def test_indexed_merge_history_and_batches_match_native_git(validator, merge_mode: str | None) -> None:
    root = validator.ROOT_DIR
    _create_package(root, 'pkg', _fedora_metadata())
    base = _commit(root, 'Import pkg-1.0-1\n\nUpstream: test', '2026-01-01T12:00:00+00:00')
    branch = _git(['branch', '--show-current'], root).stdout.strip()
    _git(['checkout', '-b', 'side'], root)
    (root / 'rpms/pkg/notes.txt').write_text('Substantive local change\n')
    side = _commit(root, 'Add local notes', '2026-04-02T12:00:00+00:00')
    _git(['checkout', branch], root)
    spec = root / 'rpms/pkg/pkg.spec'
    spec.write_text(spec.read_text().replace('Release: 1', 'Release: 1.1'))
    release = _commit(root, 'Rebuild pkg', '2026-04-03T12:00:00+00:00')
    _git(['merge', '--no-ff', 'side', '-m', 'Merge side'], root)
    merge = _git(['rev-parse', 'HEAD'], root).stdout.strip()
    if merge_mode:
        _git(['config', 'log.diffMerges', merge_mode], root)

    history = validator.GitHistory(['pkg'])
    assert history.last_sync_commit('pkg') == base
    for since in (False, True):
        native = validator.run_git(
            'log', '--format=%H %s', *(['--since=2026-03-05'] if since else []),
            '--invert-grep', '--grep=^Upstream:', f'{base}..HEAD', '--', 'rpms/pkg', cwd=root,
        ).stdout.strip()
        assert history.log('pkg', base, since=since, subjects=True).strip() == native

    commits = [merge, release, side]
    expected = {sha for sha in commits if validator.is_release_only_commit(sha, 'pkg')}
    actual = history.release_only_commits('pkg', commits)
    assert actual == expected
    assert release in actual
    assert side not in actual  # A merge must not abort a batch and hide this change.
    statuses = history.spec_statuses('rpms/pkg/pkg.spec', commits)
    for sha in commits:
        native = validator.run_git('show', '--name-status', '--format=', sha,
                                   '--', 'rpms/pkg/pkg.spec', cwd=root).stdout
        assert statuses[sha].strip() == native.strip()
    assert validator.check_git_history_state('pkg', base, history) == validator.check_git_history_state('pkg', base)
    assert validator.check_local_modifications('pkg', base, history) == validator.check_local_modifications('pkg', base)


@pytest.mark.parametrize('change,policy_valid,history_clean', [
    ('release', True, True),
    ('whitespace', True, True),
    ('release-macro', True, True),
    ('integer-release', False, True),
    ('integer-decimal-release', False, True),
    ('changelog', False, False),
    ('added-spec', True, False),
    ('renamed-spec', True, False),
    ('content', True, False),
])
def test_indexed_policy_checks_match_native_git(
    validator, change: str, policy_valid: bool, history_clean: bool,
) -> None:
    root = validator.ROOT_DIR
    _create_package(root, 'pkg', _fedora_metadata())
    spec = root / 'rpms/pkg/pkg.spec'
    spec.write_text('%global baserelease 1\n' + spec.read_text()
                    + '\n%changelog\n* Old entry\n- Original change\n')
    base = _commit(root, 'Import pkg-1.0-1\n\nUpstream: test', '2026-01-01T12:00:00+00:00')
    text = spec.read_text()
    if change == 'release':
        spec.write_text(text.replace('Release: 1', 'Release: 1.1'))
    elif change == 'whitespace':
        spec.write_text(text.replace('Summary: Test', 'Summary:    Test'))
    elif change == 'release-macro':
        spec.write_text(text.replace('%global baserelease 1', '%global baserelease 2'))
    elif change in ('integer-release', 'integer-decimal-release'):
        value = '2' if change == 'integer-release' else '2.0'
        spec.write_text(text.replace('Release: 1', f'Release: {value}'))
    elif change == 'changelog':
        spec.write_text(text + '- Local changelog addition\n')
    elif change == 'added-spec':
        (spec.parent / 'extra.spec').write_text(text.replace('Release: 1', 'Release: 2'))
    elif change == 'renamed-spec':
        spec.rename(spec.parent / 'renamed.spec')
    else:
        spec.write_text(text.replace('Summary: Test', 'Summary: Local change'))
    sha = _commit(root, f'Local {change}')

    history = validator.GitHistory(['pkg'])
    legacy_policy = validator.check_local_modifications('pkg', base)
    legacy_history = validator.check_git_history_state('pkg', base)
    assert legacy_policy[0] is policy_valid
    assert legacy_history[0] is history_clean
    assert validator.check_local_modifications('pkg', base, history) == legacy_policy
    assert validator.check_git_history_state('pkg', base, history) == legacy_history
    for current in (None, history):
        assert validator.validate_package('pkg', check_actual_state=False, history=current) == (
            validator.validate_package('pkg', check_actual_state=False)
        )
    if change in ('added-spec', 'renamed-spec'):
        path = 'rpms/pkg/' + ('extra.spec' if change == 'added-spec' else 'renamed.spec')
        assert history.spec_statuses(path, [sha])[sha].strip().startswith(('A', 'R'))


def test_indexed_since_retains_git_date_pruning(validator) -> None:
    root = validator.ROOT_DIR
    _create_package(root, 'pkg', _fedora_metadata())
    base = _commit(root, 'Import pkg-1.0-1\n\nUpstream: test', '2026-01-01T12:00:00+00:00')
    notes = root / 'rpms/pkg/notes.txt'
    notes.write_text('Ancestor with a newer date\n')
    hidden = _commit(root, 'Newer ancestor', '2026-05-01T12:00:00+00:00')
    notes.write_text(notes.read_text() + 'Older intermediate date\n')
    _commit(root, 'Older intermediate commit', '2026-02-01T12:00:00+00:00')
    notes.write_text(notes.read_text() + 'Current date\n')
    head = _commit(root, 'Current commit', '2026-06-01T12:00:00+00:00')

    history = validator.GitHistory(['pkg'])
    native = validator.run_git('log', '--format=%H', '--since=2026-03-05',
                               '--invert-grep', '--grep=^Upstream:', f'{base}..HEAD',
                               '--', 'rpms/pkg', cwd=root).stdout.strip()
    assert head in native
    assert hidden not in native
    assert history.log('pkg', base, since=True).strip() == native


@pytest.mark.parametrize('sha', [
    '376c36ccdf1f4d8b2ec652575e77a79c7878e97f',
    'e5066a9dca178d5eeba0b1bfdacec5269314e99e',
])
def test_indexed_history_retains_historical_exclusions(validator, sha: str) -> None:
    history = MagicMock(spec=validator.GitHistory)
    history.log.return_value = f'{sha} Superseded local change\n'
    assert validator.check_git_history_state('pkg', 'sync', history) == (True, None)
    history.release_only_commits.assert_not_called()


def test_failed_show_batch_falls_back_to_individual_commits(validator, monkeypatch) -> None:
    root = validator.ROOT_DIR
    _create_package(root, 'pkg', _fedora_metadata())
    base = _commit(root, 'Import pkg-1.0-1\n\nUpstream: test')
    spec = root / 'rpms/pkg/pkg.spec'
    spec.write_text(spec.read_text().replace('Release: 1', 'Release: 1.1'))
    release = _commit(root, 'Rebuild')
    spec.write_text(spec.read_text().replace('Summary: Test', 'Summary: Substantive change'))
    substantive = _commit(root, 'Change summary')
    commits = [release, substantive]
    expected_release = {sha for sha in commits if validator.is_release_only_commit(sha, 'pkg')}
    expected_status = {sha: validator.run_git('show', '--name-status', '--format=', sha,
                                             '--', 'rpms/pkg/pkg.spec', cwd=root).stdout
                       for sha in commits}
    history = validator.GitHistory(['pkg'])
    assert history.last_sync_commit('pkg') == base
    original_run = validator.run_git

    def fail_batch(*args, **kwargs):
        if args[0] == 'show' and '--format=%x00%H%x00' in args:
            return subprocess.CompletedProcess(args, 128, f'\0{release}\0\n', '')
        return original_run(*args, **kwargs)

    monkeypatch.setattr(validator, 'run_git', fail_batch)
    assert history.release_only_commits('pkg', commits) == expected_release
    assert substantive not in expected_release
    assert {sha: text.strip() for sha, text in history.spec_statuses('rpms/pkg/pkg.spec', commits).items()} == {
        sha: text.strip() for sha, text in expected_status.items()
    }


def test_fast_validation_shares_one_index_across_workers(validator) -> None:
    root = validator.ROOT_DIR
    packages = ['one', 'two', 'three', 'four']
    for package in packages:
        _create_package(root, package, _fedora_metadata())
    _commit(root, '\n'.join(f'Import {package}-1.0-1' for package in packages) + '\n\nUpstream: test')
    with patch.object(validator, 'run_git', wraps=validator.run_git) as run:
        assert validator.validate_packages(packages, check_actual_state=False) == 0
    metadata_reads = [c for c in run.call_args_list if '--format=%H%x00%P%x00%s%x00' in c.args]
    assert len(metadata_reads) == 1
    assert run.call_args_list[0].args == ('rev-parse', '--is-shallow-repository')


def test_validation_rebuilds_index_after_head_changes(validator, capsys) -> None:
    root = validator.ROOT_DIR
    _create_package(root, 'pkg', _fedora_metadata())
    _commit(root, 'Import pkg-1.0-1\n\nUpstream: test', '2026-01-01T12:00:00+00:00')
    assert validator.validate_packages(['pkg'], check_actual_state=False) == 0
    spec = root / 'rpms/pkg/pkg.spec'
    spec.write_text(spec.read_text().replace('Release: 1', 'Release: 2'))
    _commit(root, 'Invalid local rebuild')
    assert validator.validate_packages(['pkg'], check_actual_state=False) == 1
    assert 'sets Release to 2' in capsys.readouterr().err


def test_shallow_clone_is_rejected_before_index_or_workers(validator, tmp_path_factory, monkeypatch) -> None:
    root = validator.ROOT_DIR
    _commit(root, 'Additional history')
    shallow = tmp_path_factory.mktemp('shallow-parent') / 'shallow'
    _git(['clone', '--quiet', '--depth=1', f'file://{root}', str(shallow)], root)
    monkeypatch.setattr(validator, 'ROOT_DIR', shallow)
    with patch.object(validator, 'GitHistory', side_effect=AssertionError('Must reject before indexing')), \
         patch.object(validator, 'validate_package', side_effect=AssertionError('Must reject before workers')), \
         pytest.raises(SystemExit) as error:
        validator.validate_packages(['pkg'], check_actual_state=False)
    assert error.value.code == 1


def test_thorough_dispatch_does_not_use_history_index(validator) -> None:
    with patch.object(validator, 'GitHistory', side_effect=AssertionError('No index in thorough mode')), \
         patch.object(validator, 'validate_package', return_value=(True, None)) as validate:
        assert validator.validate_packages(['pkg'], check_actual_state=True) == 0
    validate.assert_called_once_with('pkg', True, history=None)


def test_independent_packages_do_not_load_history(validator) -> None:
    _git(['checkout', '--orphan', 'unborn'], validator.ROOT_DIR)
    _create_package(validator.ROOT_DIR, 'pkg', {
        'modification_status': 'independent', 'version': '1.0', 'upstream_repo': 'https://example.com/pkg',
    })
    with patch.object(validator.GitHistory, '_load', side_effect=AssertionError('No Fedora history needed')):
        assert validator.validate_packages(['pkg'], check_actual_state=False) == 0


def test_unborn_fedora_history_retains_native_behavior(validator) -> None:
    _git(['checkout', '--orphan', 'unborn'], validator.ROOT_DIR)
    _create_package(validator.ROOT_DIR, 'pkg', _fedora_metadata())
    expected = validator.validate_package('pkg', check_actual_state=False)
    history = validator.GitHistory(['pkg'])
    assert validator.validate_package('pkg', check_actual_state=False, history=history) == expected


def test_packages_without_specs_do_not_walk_local_policy_history(validator) -> None:
    (validator.RPMS_DIR / 'pkg').mkdir()
    with patch.object(validator, 'run_git', side_effect=AssertionError('No spec history needed')):
        assert validator.check_local_modifications('pkg', None, validator.GitHistory(['pkg'])) == (True, None)


def test_index_load_failure_is_not_silently_accepted_or_repeated(validator, monkeypatch) -> None:
    original_run = validator.run_git
    failure = subprocess.CalledProcessError(128, ['git', 'log'])
    attempts = []

    def fail_metadata(*args, **kwargs):
        if '--format=%H%x00%P%x00%s%x00' in args:
            attempts.append(args)
            raise failure
        return original_run(*args, **kwargs)

    monkeypatch.setattr(validator, 'run_git', fail_metadata)
    history = validator.GitHistory(['pkg'])
    for _ in range(2):
        with pytest.raises(subprocess.CalledProcessError) as error:
            history.last_sync_commit('pkg')
        assert error.value is failure
    assert len(attempts) == 1


def test_diff_batches_bound_commit_arguments(validator) -> None:
    history = validator.GitHistory(['pkg'])
    history._loaded = True
    commits = [f'{number:040x}' for number in range(35)]
    with patch.object(history, '_show', return_value={}) as show, \
         patch.object(validator, 'run_git', side_effect=AssertionError('No fallback for successful batches')):
        assert history.release_only_commits('pkg', commits) == set(commits)
    assert [len(c.args[0]) for c in show.call_args_list] == [32, 3]


def test_indexed_history_preserves_empty_subject_diagnostic(validator) -> None:
    root = validator.ROOT_DIR
    _create_package(root, 'pkg', _fedora_metadata())
    base = _commit(root, 'Import pkg-1.0-1\n\nUpstream: test', '2026-01-01T12:00:00+00:00')
    spec = root / 'rpms/pkg/pkg.spec'
    spec.write_text(spec.read_text().replace('Summary: Test', 'Summary: Local change'))
    _git(['add', '-A'], root)
    _git(['commit', '--allow-empty-message', '-m', ''], root)
    expected = validator.check_git_history_state('pkg', base)
    assert expected[0] is False
    assert '(no subject)' in expected[1]
    assert validator.check_git_history_state('pkg', base, validator.GitHistory(['pkg'])) == expected


@pytest.mark.parametrize('output', ['not a framed diff', '\0unexpected-sha\0patch'])
def test_diff_batch_rejects_unexpected_output(validator, monkeypatch, output: str) -> None:
    monkeypatch.setattr(validator, 'run_git', lambda *a, **kw: subprocess.CompletedProcess(a, 0, output, ''))
    with pytest.raises(ValueError, match='Unexpected'):
        validator.GitHistory(['pkg'])._show(['expected-sha'], 'rpms/pkg/pkg.spec', '--name-status')


@pytest.mark.parametrize('value', ['true', 'false'])
def test_signature_display_settings_retain_native_queries(validator, value: str) -> None:
    root = validator.ROOT_DIR
    _create_package(root, 'pkg', _fedora_metadata())
    sha = _commit(root, 'Add package')
    _git(['config', 'log.showSignature', value], root)

    history = validator.GitHistory(['pkg'])
    assert history.last_sync_commit('pkg') == validator.find_last_sync_commit('pkg')
    expected = validator.run_git('log', '--format=%H %s', '--invert-grep', '--grep=^Upstream:',
                                 'HEAD', '--', 'rpms/pkg', cwd=root).stdout.strip()
    assert history.log('pkg', None, subjects=True).strip() == expected
    expected_status = validator.run_git('show', '--name-status', '--format=', sha,
                                        '--', 'rpms/pkg/pkg.spec', cwd=root).stdout.strip()
    assert history.spec_statuses('rpms/pkg/pkg.spec', [sha])[sha].strip() == expected_status
    assert history._legacy_only
