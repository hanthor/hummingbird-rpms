"""Integration tests for dist_git importer."""

import http.client
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import types
import urllib.request
import xmlrpc.client
from collections.abc import Callable
from email.message import Message
from inspect import getsource
from io import BytesIO
from pathlib import Path
from textwrap import indent
from urllib.error import HTTPError, URLError
from urllib.request import Request
from unittest.mock import MagicMock, call, patch

import pytest
import yaml

project_root = Path(__file__).parent.parent

#
# Fixtures
#

# _anitya_pythonpath copies this helper's source into child interpreters.
# Keep it self-contained and declare its imports in the generated source.
def _stub_anitya_urlopen(request: Request | str, *args, **kwargs):
    """Serve missing fake packages without replacing the lookup implementation."""
    url = request.full_url if isinstance(request, Request) else request
    if not url.startswith('https://release-monitoring.org/api/project/Fedora/'):
        raise AssertionError(f'Unexpected HTTP request in dist-git test: {url}')
    raise HTTPError(url, 404, 'Not Found', Message(), None)


@pytest.fixture(scope='session')
def _anitya_pythonpath(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Install the same transport stub in child dist_git.py processes only."""
    directory = tmp_path_factory.mktemp('anitya-http-stub')
    source = (
        'import urllib.request\n'
        'from email.message import Message\n'
        'from urllib.error import HTTPError\n'
        'from urllib.request import Request\n'
        + getsource(_stub_anitya_urlopen)
        + '\nurllib.request.urlopen = _stub_anitya_urlopen\n'
    )
    # Prepending this module to PYTHONPATH shadows other sitecustomize hooks,
    # even for non-dist_git.py children. If startup/coverage hooks are added,
    # preserve their initialization here; the basename gate scopes HTTP only.
    (directory / 'sitecustomize.py').write_text(
        "import sys\nif sys.argv[0].rsplit('/', 1)[-1] == 'dist_git.py':\n"
        + indent(source, '    ')
    )
    return directory


@pytest.fixture(autouse=True)
def _stub_anitya_http(monkeypatch: pytest.MonkeyPatch, _anitya_pythonpath: Path) -> None:
    """Keep real lookup/error handling in both parent and child CLI processes."""
    monkeypatch.setattr(urllib.request, 'urlopen', _stub_anitya_urlopen)
    pythonpath = os.environ.get('PYTHONPATH')
    monkeypatch.setenv('PYTHONPATH', str(_anitya_pythonpath)
                       + (os.pathsep + pythonpath if pythonpath else ''))


def _load_dist_git_module():
    """Load a fresh module so tests do not share mutable script globals."""
    script_path = Path(__file__).parent.parent / 'ci' / 'dist_git.py'
    module = types.ModuleType("dist_git")
    module.__file__ = str(script_path)
    code = compile(script_path.read_text(), str(script_path), 'exec')
    exec(code, module.__dict__)
    return module


@pytest.fixture
def dist_git_module():
    """Load the dist_git script for tests which mock internal functions."""
    return _load_dist_git_module()


def test_run_git_retries_ls_remote(dist_git_module) -> None:
    """Transient ls-remote failures use the standard bounded retry policy."""
    failure = subprocess.CalledProcessError(128, ['git', 'ls-remote', 'https://example.invalid/repo.git'])
    success = MagicMock(spec=subprocess.CompletedProcess)

    with patch.object(dist_git_module, '_run_git_network_attempt', side_effect=[failure, failure, success]) as run, \
         patch.object(dist_git_module.time, 'sleep') as sleep:
        assert dist_git_module.run_git(
            'ls-remote', 'https://example.invalid/repo.git', 'rawhide'
        ) is success

    assert run.call_count == 3
    assert [call.args for call in sleep.call_args_list] == [
        (dist_git_module.GIT_NETWORK_INITIAL_RETRY_DELAY_SECONDS,),
        (dist_git_module.GIT_NETWORK_INITIAL_RETRY_DELAY_SECONDS * 2,),
    ]


def test_run_git_reraises_final_ls_remote_failure(dist_git_module) -> None:
    """The final ls-remote error is preserved after bounded retries."""
    failure = subprocess.CalledProcessError(128, ['git', 'ls-remote', 'https://example.invalid/repo.git'])

    with patch.object(dist_git_module, '_run_git_network_attempt', side_effect=[failure, failure, failure]) as run, \
         patch.object(dist_git_module.time, 'sleep') as sleep, \
         pytest.raises(subprocess.CalledProcessError) as raised:
        dist_git_module.run_git('ls-remote', 'https://example.invalid/repo.git', 'rawhide')

    assert raised.value is failure
    assert run.call_count == dist_git_module.GIT_NETWORK_MAX_ATTEMPTS
    assert [call.args for call in sleep.call_args_list] == [
        (dist_git_module.GIT_NETWORK_INITIAL_RETRY_DELAY_SECONDS,),
        (dist_git_module.GIT_NETWORK_INITIAL_RETRY_DELAY_SECONDS * 2,),
    ]


@pytest.mark.parametrize('operation', ['clone', 'fetch', 'pull', 'ls-remote'])
def test_run_git_network_attempt_has_timeout(dist_git_module, operation: str) -> None:
    """Every network attempt has a wall-clock timeout, regardless of protocol."""
    with patch.object(dist_git_module, '_run_git_network_attempt') as run:
        dist_git_module.run_git(operation, 'https://example.invalid/repo.git')

    assert run.call_args.kwargs.get('timeout') == 120


@pytest.mark.parametrize('operation', ['status', 'show', 'commit'])
def test_run_git_local_command_has_no_network_timeout(dist_git_module, operation: str) -> None:
    """Network limits must not restrict local operations or commit hooks."""
    with patch.object(dist_git_module.subprocess, 'run') as run:
        dist_git_module.run_git(operation)

    assert run.call_args.kwargs.get('timeout') is None


@pytest.mark.parametrize('check', [False, True])
def test_run_git_retries_timeout(dist_git_module, check: bool) -> None:
    """A timeout is retryable even when Git exit-code checking is disabled."""
    failure = subprocess.TimeoutExpired(['git', 'ls-remote'], 120)
    success = subprocess.CompletedProcess(['git', 'ls-remote'], 0, stdout='sha\trefs/heads/rawhide\n')
    with patch.object(dist_git_module, '_run_git_network_attempt', side_effect=[failure, failure, success]) as run, \
         patch.object(dist_git_module.time, 'sleep') as sleep:
        assert dist_git_module.run_git('ls-remote', 'https://example.invalid/repo.git', check=check) is success

    assert run.call_count == 3
    assert sleep.call_args_list == [call(2), call(4)]
    assert all(attempt.kwargs.get('timeout') == 120 for attempt in run.call_args_list)


def test_run_git_reraises_final_timeout(dist_git_module) -> None:
    """Exhausted timeout retries preserve the final error and do not sleep again."""
    failures = [subprocess.TimeoutExpired(['git', 'clone'], 120) for _ in range(3)]
    with patch.object(dist_git_module, '_run_git_network_attempt', side_effect=failures) as run, \
         patch.object(dist_git_module.time, 'sleep') as sleep, \
         pytest.raises(subprocess.TimeoutExpired) as raised:
        dist_git_module.run_git('clone', 'https://example.invalid/repo.git')

    assert raised.value is failures[-1]
    assert run.call_count == 3
    assert sleep.call_args_list == [call(2), call(4)]


def test_git_timeout_kills_transport_helpers(
    dist_git_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timing out Git must kill its helper, not just the parent process."""
    marker = tmp_path / 'helper-survived'
    pid_file = tmp_path / 'helper.pid'
    fake_git = tmp_path / 'git'
    fake_git.write_text(
        f'#!{sys.executable}\n'
        'import subprocess, sys, time\n'
        f'child = subprocess.Popen([sys.executable, "-c", '
        f'"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).touch(); time.sleep(30)"])\n'
        f'open({str(pid_file)!r}, "w").write(str(child.pid))\n'
        'time.sleep(30)\n'
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.setattr(dist_git_module, 'GIT_NETWORK_TIMEOUT_SECONDS', 0.3)
    monkeypatch.setattr(dist_git_module, 'GIT_NETWORK_MAX_ATTEMPTS', 1)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            dist_git_module.run_git('ls-remote', 'https://example.invalid/repo.git')
        assert pid_file.exists(), 'The test helper did not start before the timeout'
        time.sleep(1.2)
        assert not marker.exists(), 'The Git transport helper survived its parent timeout'
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize('recover', [False, True], ids=['exhausted', 'recovers'])
def test_git_clone_retries_do_not_leave_partial_destination(
    dist_git_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recover: bool,
) -> None:
    """A killed clone must not poison later retries or a future import."""
    counter = tmp_path / 'attempts'
    destination = tmp_path / 'package'
    fake_git = tmp_path / 'git'
    fake_git.write_text(
        f'#!{sys.executable}\n'
        'from pathlib import Path\n'
        'import sys, time\n'
        f'counter = Path({str(counter)!r})\n'
        'attempt = int(counter.read_text()) + 1 if counter.exists() else 1\n'
        'counter.write_text(str(attempt))\n'
        'destination = Path(sys.argv[-1])\n'
        'if destination.exists():\n'
        '    sys.exit(128)\n'
        '(destination / ".git").mkdir(parents=True)\n'
        f'if not {recover!r} or attempt == 1:\n'
        '    time.sleep(30)\n'
        '(destination / "package.spec").write_text("complete clone")\n'
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.setattr(dist_git_module, 'GIT_NETWORK_TIMEOUT_SECONDS', 0.3)
    monkeypatch.setattr(dist_git_module, 'GIT_NETWORK_INITIAL_RETRY_DELAY_SECONDS', 0)

    if recover:
        result = dist_git_module.run_git('clone', 'https://example.invalid/repo.git', str(destination))
        assert result.returncode == 0
        assert (destination / 'package.spec').read_text() == 'complete clone'
        assert counter.read_text() == '2'
    else:
        with pytest.raises(subprocess.TimeoutExpired):
            dist_git_module.run_git('clone', 'https://example.invalid/repo.git', str(destination))
        assert counter.read_text() == '3'
        assert not destination.exists(), 'Failed clone still blocks future imports'


@pytest.mark.parametrize('existing_empty', [False, True])
def test_git_clone_relative_destination(
    dist_git_module, tmp_path: Path, upstream_repos: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch, existing_empty: bool,
) -> None:
    """Staging preserves Git's relative cwd/destination and empty-dir behavior."""
    monkeypatch.chdir(tmp_path)
    Path('working').mkdir()
    if existing_empty:
        Path('working/package').mkdir()
    result = dist_git_module.run_git('clone', '--quiet', str(upstream_repos['vanilla']), 'package', cwd='working')
    assert result.returncode == 0
    assert Path('working/package/vanilla.spec').exists()
    assert not list(Path('working').glob('.dist-git-clone-*'))


def test_git_clone_timeout_preserves_existing_destination(
    dist_git_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry cleanup must never delete a destination the caller already owned."""
    destination = tmp_path / 'package'
    destination.mkdir()
    important = destination / 'keep-me'
    important.write_text('existing user data')
    fake_git = tmp_path / 'git'
    fake_git.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(30)\n')
    fake_git.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.setattr(dist_git_module, 'GIT_NETWORK_TIMEOUT_SECONDS', 0.1)
    monkeypatch.setattr(dist_git_module, 'GIT_NETWORK_MAX_ATTEMPTS', 1)
    with pytest.raises(subprocess.TimeoutExpired):
        dist_git_module.run_git('clone', 'https://example.invalid/repo.git', str(destination))
    assert important.read_text() == 'existing user data'


@pytest.fixture(scope='session')
def _workdir_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the empty project once; tests only receive independent clones."""
    tmp_path = tmp_path_factory.mktemp('workdir-seed')

    rpms_dir = tmp_path / 'workdir'
    rpms_dir.mkdir()

    subprocess.run(['git', 'init'], cwd=rpms_dir, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=rpms_dir, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=rpms_dir, check=True)

    # Copy ci/, config, and templates. Excludes any stale __pycache__ from the
    # real checkout (e.g. dist_git.py now imports check_upstream_versions.py
    # as a sibling module, so running it -- even just once, in a prior local
    # `./ci/dist_git.py ...` invocation -- populates ci/__pycache__/ in the
    # real repo, which would otherwise leak into every test's "clean" copy).
    shutil.copytree(project_root / 'ci', rpms_dir / 'ci',
                     ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(project_root / 'hum', rpms_dir / 'hum',
                     ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy(project_root / 'target-packages.yml', rpms_dir / 'target-packages.yml')
    shutil.copy(project_root / '.gitignore', rpms_dir / '.gitignore')
    shutil.copytree(project_root / '.tekton', rpms_dir / '.tekton')
    shutil.copytree(project_root / 'konflux-templates', rpms_dir / 'konflux-templates')

    # Create directories for imports
    (rpms_dir / 'rpms').mkdir()
    (rpms_dir / 'metadata').mkdir()

    # Create default upstream-releases.json for tests
    # Note: rawhide is not included as it's auto-resolved to the highest version
    (rpms_dir / 'upstream-releases.json').write_text(
        json.dumps({'fedora': {'f40': 'f40', 'f99': 'f99'}}) + '\n'
    )

    subprocess.run(['git', 'add', '.'], cwd=rpms_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=rpms_dir, check=True)

    return rpms_dir


@pytest.fixture
def workdir(tmp_path: Path, _workdir_seed: Path) -> Path:
    """Clone the empty project for import tests and custom package setups."""
    return _clone_workdir(_workdir_seed, tmp_path / 'workdir')


@pytest.fixture(scope='session')
def _upstream_seeds(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Build the mock upstream history once, without exposing it to tests."""
    tmp_path = tmp_path_factory.mktemp('upstream-seeds')
    repos: dict[str, Path] = {}

    # Create first upstream repo: vanilla
    vanilla_dir = tmp_path / 'upstream' / 'vanilla.git'
    vanilla_dir.mkdir(parents=True)
    subprocess.run(['git', 'init', '--initial-branch=rawhide'], cwd=vanilla_dir, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=vanilla_dir, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=vanilla_dir, check=True)

    # Create a simple spec file
    (vanilla_dir / 'vanilla.spec').write_text("""Name: vanilla
Version: 1.0
Release: 1%{?dist}
Summary: Test package vanilla
License: MIT

%description
Test package

%files
""")
    subprocess.run(['git', 'add', 'vanilla.spec'], cwd=vanilla_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=vanilla_dir, check=True)
    repos['vanilla'] = vanilla_dir

    # Create second upstream repo: chocolate with a stable branch
    chocolate_dir = tmp_path / 'upstream' / 'chocolate.git'
    chocolate_dir.mkdir(parents=True)
    subprocess.run(['git', 'init', '--initial-branch=rawhide'], cwd=chocolate_dir, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=chocolate_dir, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=chocolate_dir, check=True)

    choc_spec = chocolate_dir / 'chocolate.spec'
    choc_spec.write_text("""Name: chocolate
Version: 10
Release: 1%{?dist}
Summary: Test package chocolate
License: GPL

%description
Test package chocolate

%files
""")
    subprocess.run(['git', 'add', 'chocolate.spec'], cwd=chocolate_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=chocolate_dir, check=True)

    # Create f40 branch
    subprocess.run(['git', 'checkout', '-b', 'f40'], cwd=chocolate_dir, check=True)
    choc_spec.write_text("""Name: chocolate
Version: 4
Release: 1%{?dist}
Summary: Test package chocolate
License: GPL

%description
Test package chocolate (f40)

%files
""")
    subprocess.run(['git', 'add', 'chocolate.spec'], cwd=chocolate_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Update for f40'], cwd=chocolate_dir, check=True)

    # Create f99 branch (higher version than f40, for testing release upgrades)
    subprocess.run(['git', 'checkout', '-b', 'f99'], cwd=chocolate_dir, check=True)
    choc_spec.write_text("""Name: chocolate
Version: 99
Release: 1%{?dist}
Summary: Test package chocolate
License: GPL

%description
Test package chocolate (f99)

%files
""")
    subprocess.run(['git', 'add', 'chocolate.spec'], cwd=chocolate_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Update for f99'], cwd=chocolate_dir, check=True)
    subprocess.run(['git', 'checkout', 'rawhide'], cwd=chocolate_dir, check=True)

    repos['chocolate'] = chocolate_dir

    return repos


@pytest.fixture
def upstream_repos(tmp_path: Path, _upstream_seeds: dict[str, Path]) -> dict[str, Path]:
    """Copy upstreams, including every local branch, without sharing Git files."""
    repos = {}
    for name, seed in _upstream_seeds.items():
        destination = tmp_path / 'upstream' / seed.name
        shutil.copytree(seed, destination)
        repos[name] = destination
    return repos


@pytest.fixture(scope='session')
def _imported_workdir_seeds(
    tmp_path_factory: pytest.TempPathFactory,
    _workdir_seed: Path,
    _upstream_seeds: dict[str, Path],
) -> Callable[..., Path]:
    """Cache real imports for each package/branch combination, not module state."""
    seeds: dict[tuple[tuple[str, ...], str], Path] = {}

    def get_seed(*packages: str, branch: str = 'rawhide') -> Path:
        key = (packages, branch)
        if key not in seeds:
            destination = tmp_path_factory.mktemp('imported-workdir-seed') / 'workdir'
            _clone_workdir(_workdir_seed, destination)
            module = _load_dist_git_module()
            for package in packages:
                run_dist_git(module, destination, 'import', '--branch', branch,
                             f'file://{_upstream_seeds[package]}')
            seeds[key] = destination
        return seeds[key]

    return get_seed


@pytest.fixture
def imported_workdir(
    tmp_path: Path,
    upstream_repos: dict[str, Path],
    _imported_workdir_seeds: Callable[..., Path],
) -> Callable[..., Path]:
    """Create private imported workdirs pointing at this test's private upstreams."""
    created = 0

    def create(*packages: str, branch: str = 'rawhide') -> Path:
        nonlocal created
        seed = _imported_workdir_seeds(*packages, branch=branch)
        destination = _clone_workdir(seed, tmp_path / f'imported-workdir-{created}')
        created += 1
        for package in packages:
            metadata_file = destination / 'metadata' / f'{package}.json'
            metadata = json.loads(metadata_file.read_text())
            metadata['fedora']['git_url'] = f'file://{upstream_repos[package]}'
            metadata_file.write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')
        # Amend to preserve import counts and trailers. Only HEAD's metadata
        # is retargeted; earlier commits may retain seed upstream URLs.
        # Tests inspecting historical metadata must use workdir and perform
        # their own imports.
        subprocess.run(['git', 'add', 'metadata'], cwd=destination, check=True)
        subprocess.run(['git', 'commit', '--amend', '--no-edit'], cwd=destination, check=True)
        return destination

    return create


#
# Helpers
#

def _clone_workdir(seed: Path, destination: Path) -> Path:
    subprocess.run(
        ['git', 'clone', '--quiet', '--local', '--no-hardlinks',
         '--config', 'user.name=Test', '--config', 'user.email=test@example.com',
         str(seed), str(destination)],
        check=True,
    )
    # The old fixtures had no remote. Do not leave a push target into the seed.
    subprocess.run(['git', 'remote', 'remove', 'origin'], cwd=destination, check=True)
    # Git does not track the empty directories in the unimported seed.
    for directory in ('rpms', 'metadata'):
        (destination / directory).mkdir(exist_ok=True)
    return destination


def add_upstream_commit(repo_path: Path, package_name: str, old_version: str, new_version: str) -> str:
    """Add a commit to an upstream repository updating the version.

    Returns the new commit SHA.
    """
    spec_file = repo_path / f'{package_name}.spec'
    spec_file.write_text(spec_file.read_text().replace(f'Version: {old_version}', f'Version: {new_version}'))
    subprocess.run(['git', 'add', f'{package_name}.spec'], cwd=repo_path, check=True)
    subprocess.run(['git', 'commit', '-m', f'Update to {new_version}-1'], cwd=repo_path, check=True)
    return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo_path,
                          text=True, stdout=subprocess.PIPE, check=True).stdout.strip()


def get_last_commit_info(workdir: Path) -> tuple[str, str]:
    """Get subject and body of the last commit.

    Returns (subject, body) tuple.
    """
    result = subprocess.run(['git', 'log', '-1', '--format=%s%n%n%b'], cwd=workdir,
                            text=True, stdout=subprocess.PIPE, check=True)
    lines = result.stdout.strip().split('\n')
    assert lines
    subject = lines[0]
    body = '\n'.join(lines[2:]) if len(lines) > 2 else ''
    return subject, body


def run_dist_git(dist_git_module, workdir: Path, *args: str) -> None:
    """Helper to run dist_git.main() in-process (when using mocks)."""
    # Set up module to use workdir
    dist_git_module.ROOT_DIR = workdir
    dist_git_module.RPMS_DIR = workdir / 'rpms'
    dist_git_module.METADATA_DIR = workdir / 'metadata'
    dist_git_module.RELEASES_JSON = workdir / 'upstream-releases.json'
    dist_git_module._METADATA_REPO = dist_git_module.MetadataRepository(workdir)

    old_argv = sys.argv
    try:
        sys.argv = ['dist_git.py'] + list(args)
        dist_git_module.main()
    finally:
        sys.argv = old_argv


#
# Tests
#

@pytest.mark.parametrize('arguments,operation', [
    (['rename', 'vanilla'], 'rename'),
    (['update-releases'], 'update_releases'),
    (['list'], 'list_packages'),
    (['diff', 'vanilla'], 'diff_package'),
    (['check-paths'], 'check_paths'),
    (['ls-sources', 'vanilla'], 'ls_sources'),
])
def test_dry_run_rejects_unsupported_commands_before_dispatch(
    dist_git_module, workdir: Path, arguments: list[str], operation: str,
) -> None:
    """Ignored dry-run flags must fail before invoking the command or Git checks."""
    with patch.object(dist_git_module, operation) as command, \
         patch.object(dist_git_module, 'check_git_config') as git_config, \
         pytest.raises(SystemExit, match=f'--dry-run is not supported with the {arguments[0]} command'):
        run_dist_git(dist_git_module, workdir, '--dry-run', *arguments)
    command.assert_not_called()
    git_config.assert_not_called()


@pytest.mark.parametrize('command,policy', [
    ('import', 'regenerate resources'),
    ('update', 'without saving metadata'),
    ('sync', 'Replace package files'),
    ('rebuild', 'Modify spec releases'),
    ('rebuild-rev-deps', 'Modify spec releases'),
    ('mark-modified', 'accepted for compatibility'),
    ('set-upstream', 'accepted for compatibility'),
    ('sanitize-paths', 'Preview portable path changes'),
    ('add-private-product', 'do not contact Pulp'),
    ('rename', '--dry-run is not supported'),
    ('update-releases', '--dry-run is not supported'),
    ('list', '--dry-run is not supported'),
    ('diff', '--dry-run is not supported'),
    ('check-paths', '--dry-run is not supported'),
    ('ls-sources', '--dry-run is not supported'),
])
def test_command_help_documents_dry_run_policy(
    dist_git_module, workdir: Path, capsys: pytest.CaptureFixture[str], command: str, policy: str,
) -> None:
    with pytest.raises(SystemExit) as exited:
        run_dist_git(dist_git_module, workdir, command, '--help')
    assert exited.value.code == 0
    assert policy in ' '.join(capsys.readouterr().out.split())


def test_update_help_explains_dry_run_validation_recovery(
    dist_git_module, workdir: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exited:
        run_dist_git(dist_git_module, workdir, 'update', '--help')
    assert exited.value.code == 0
    help_text = ' '.join(capsys.readouterr().out.split())
    assert 'Version-validation failures exit with code 1 and leave no resumable state' in help_text
    assert 'cannot resume a failed dry-run with --continue' in help_text


@pytest.mark.parametrize('command', ['mark-modified', 'set-upstream'])
def test_metadata_commands_dry_run_preserves_metadata_only_policy(imported_workdir, command: str) -> None:
    """Compatibility means metadata is still written, but never staged or committed."""
    workdir = imported_workdir('vanilla')
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    before = metadata_file.read_bytes()
    head_before = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=workdir)
    if command == 'mark-modified':
        options = ['--modified', '--reason', 'Local packaging change']
    else:
        options = ['--track-version', 'latest', '--project-id', '12345']
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', command, 'vanilla', *options],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert metadata_file.read_bytes() != before
    metadata = json.loads(metadata_file.read_text())
    if command == 'mark-modified':
        assert metadata['modification_status'] == 'modified'
        assert metadata['modification_reason'] == 'Local packaging change'
    else:
        assert metadata['track_upstream'] == 'latest'
        assert metadata['release_monitoring_project_id'] == 12345
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=workdir) == head_before
    assert not subprocess.check_output(['git', 'diff', '--cached'], cwd=workdir)


@pytest.mark.parametrize('command', ['update', 'sync'])
def test_update_sync_dry_run_changes_files_without_metadata_or_commit(
    imported_workdir, upstream_repos: dict[str, Path], command: str,
) -> None:
    workdir = imported_workdir('vanilla')
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    metadata_before = metadata_file.read_bytes()
    head_before = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=workdir)
    add_upstream_commit(upstream_repos['vanilla'], 'vanilla', '1.0', '2.0')
    options = ['--skip-build-check'] if command == 'update' else []
    assert 'Version: 1.0' in (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', command, 'vanilla', *options],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert 'Version: 2.0' in (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()
    assert metadata_file.read_bytes() == metadata_before
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=workdir) == head_before
    assert not subprocess.check_output(['git', 'diff', '--cached'], cwd=workdir)
    assert not (workdir / '.dist_git_update_state.json').exists()


@pytest.mark.parametrize('filename,expected_module', [
    ('dist_git.py', 'sitecustomize'),
    ('other.py', 'urllib.request'),
])
def test_anitya_stub_is_scoped_to_child_dist_git(tmp_path: Path, filename: str, expected_module: str) -> None:
    """The startup hook must affect the CLI, not unrelated Python tools."""
    script = tmp_path / filename
    script.write_text(
        'import urllib.request\n'
        'from urllib.error import HTTPError\n'
        f'assert urllib.request.urlopen.__module__ == {expected_module!r}\n'
        "if urllib.request.urlopen.__module__ == 'sitecustomize':\n"
        '    try:\n'
        "        urllib.request.urlopen('https://release-monitoring.org/api/project/Fedora/vanilla')\n"
        '    except HTTPError as error:\n'
        '        assert error.code == 404\n'
        '    else:\n'
        "        raise AssertionError('Expected the offline Anitya response')\n"
    )
    subprocess.run([sys.executable, str(script)], check=True)


def test_anitya_stub_rejects_unexpected_http_requests(dist_git_module, monkeypatch) -> None:
    monkeypatch.setattr(dist_git_module, 'ANITYA_API_BASE', 'https://example.invalid/api')
    with pytest.raises(AssertionError, match='Unexpected HTTP request'):
        dist_git_module.lookup_anitya_project_id('vanilla')


@pytest.mark.parametrize('project_id', [12345, '12345'])
def test_lookup_anitya_project_id_parses_response(dist_git_module, project_id: int | str) -> None:
    response = BytesIO(json.dumps({'id': project_id}).encode())
    with patch.object(dist_git_module.urllib.request, 'urlopen', return_value=response) as urlopen:
        assert dist_git_module.lookup_anitya_project_id('vanilla') == 12345

    assert urlopen.call_count == 1
    request = urlopen.call_args.args[0]
    assert request.full_url == 'https://release-monitoring.org/api/project/Fedora/vanilla'
    assert request.get_header('User-agent') == 'hummingbird-rpms/1.0'
    assert urlopen.call_args.kwargs == {'timeout': 5}


@pytest.mark.parametrize('status', [404, 500])
def test_lookup_anitya_project_id_handles_http_errors(dist_git_module, caplog, status: int) -> None:
    error = HTTPError('https://release-monitoring.org/', status, 'Error', Message(), None)
    with patch.object(dist_git_module.urllib.request, 'urlopen', side_effect=error), \
         caplog.at_level('WARNING'):
        assert dist_git_module.lookup_anitya_project_id('vanilla') is None

    if status == 404:
        assert 'Anitya lookup' not in caplog.text
    else:
        assert 'returned HTTP 500' in caplog.text


@pytest.mark.parametrize('error', [URLError('unavailable'), TimeoutError('timed out')])
def test_lookup_anitya_project_id_handles_transport_errors(dist_git_module, caplog, error) -> None:
    with patch.object(dist_git_module.urllib.request, 'urlopen', side_effect=error), \
         caplog.at_level('WARNING'):
        assert dist_git_module.lookup_anitya_project_id('vanilla') is None
    assert 'Anitya lookup for vanilla failed' in caplog.text


@pytest.mark.parametrize('body', [b'not json', b'{}', b'{"id": "not-an-integer"}'])
def test_lookup_anitya_project_id_handles_invalid_responses(dist_git_module, caplog, body: bytes) -> None:
    with patch.object(dist_git_module.urllib.request, 'urlopen', return_value=BytesIO(body)), \
         caplog.at_level('WARNING'):
        assert dist_git_module.lookup_anitya_project_id('vanilla') is None
    assert 'Anitya lookup for vanilla failed' in caplog.text


def test_workdir_clones_do_not_modify_seed(workdir: Path, _workdir_seed: Path, tmp_path: Path) -> None:
    """Working files, Git configuration, and commits belong to each test clone."""
    other = _clone_workdir(_workdir_seed, tmp_path / 'other-workdir')
    original = (_workdir_seed / 'ci' / 'dist_git.py').read_bytes()
    original_commit = get_last_commit_info(_workdir_seed)
    assert list((workdir / 'rpms').iterdir()) == []
    assert list((workdir / 'metadata').iterdir()) == []
    assert not (workdir / '.git' / 'objects' / 'info' / 'alternates').exists()
    assert subprocess.check_output(['git', 'remote'], cwd=workdir, text=True) == ''

    (workdir / 'ci' / 'dist_git.py').write_bytes(b'# Private test change\n' + original)
    subprocess.run(['git', 'config', 'user.name', 'Private Test'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-am', 'Change private workdir'], cwd=workdir, check=True)

    for untouched in (_workdir_seed, other):
        assert (untouched / 'ci' / 'dist_git.py').read_bytes() == original
        assert get_last_commit_info(untouched) == original_commit
        assert subprocess.check_output(['git', 'config', 'user.name'], cwd=untouched, text=True).strip() == 'Test'


@pytest.mark.parametrize('packages,branch', [
    (('vanilla',), 'rawhide'),
    (('chocolate',), 'f40'),
    (('vanilla', 'chocolate'), 'rawhide'),
], ids=['vanilla', 'chocolate-f40', 'both'])
def test_imported_workdir_clones_do_not_modify_seed(
    imported_workdir, _imported_workdir_seeds, upstream_repos: dict[str, Path],
    packages: tuple[str, ...], branch: str,
) -> None:
    """Retargeting metadata and changing packages must never alter a cached seed."""
    seed = _imported_workdir_seeds(*packages, branch=branch)
    original_commit = get_last_commit_info(seed)
    original_metadata = {package: (seed / 'metadata' / f'{package}.json').read_bytes()
                         for package in packages}
    first = imported_workdir(*packages, branch=branch)
    second = imported_workdir(*packages, branch=branch)
    assert first != second
    assert get_last_commit_info(first) == original_commit
    assert get_last_commit_info(second) == original_commit
    assert not (first / '.git' / 'objects' / 'info' / 'alternates').exists()
    assert subprocess.check_output(['git', 'remote'], cwd=first, text=True) == ''
    for package in packages:
        metadata = json.loads((second / 'metadata' / f'{package}.json').read_text())
        assert metadata['fedora']['git_url'] == f'file://{upstream_repos[package]}'
        assert metadata['fedora']['branch'] == branch
        assert metadata['fedora']['sha'] == subprocess.check_output(
            ['git', 'rev-parse', branch], cwd=upstream_repos[package], text=True,
        ).strip()

    package = packages[0]
    spec_path = first / 'rpms' / package / f'{package}.spec'
    original_spec = spec_path.read_bytes()
    spec_path.write_bytes(original_spec + b'\n# Private test change\n')
    metadata_path = first / 'metadata' / f'{package}.json'
    metadata = json.loads(metadata_path.read_text())
    metadata['version'] = '999'
    metadata_path.write_text(json.dumps(metadata) + '\n')
    subprocess.run(['git', 'commit', '-am', 'Change private package'], cwd=first, check=True)

    assert (second / 'rpms' / package / f'{package}.spec').read_bytes() == original_spec
    assert json.loads((second / 'metadata' / f'{package}.json').read_text())['version'] != '999'
    assert get_last_commit_info(second) == original_commit
    assert _imported_workdir_seeds(*packages, branch=branch) == seed
    assert get_last_commit_info(seed) == original_commit
    for package in packages:
        assert (seed / 'metadata' / f'{package}.json').read_bytes() == original_metadata[package]
    for untouched in (seed, second):
        assert subprocess.check_output(['git', 'status', '--porcelain'], cwd=untouched, text=True) == ''


@pytest.mark.parametrize('package,version,new_version', [
    ('vanilla', '1.0', '2.0'),
    ('chocolate', '10', '11'),
])
def test_upstream_copies_do_not_modify_seed(
    upstream_repos: dict[str, Path], _upstream_seeds: dict[str, Path], tmp_path: Path,
    package: str, version: str, new_version: str,
) -> None:
    """New upstream commits must not leak into another test or its seed."""
    seed = _upstream_seeds[package]
    original_spec = (seed / f'{package}.spec').read_bytes()
    original_commit = get_last_commit_info(seed)
    other = tmp_path / 'other-upstream'
    shutil.copytree(seed, other)

    add_upstream_commit(upstream_repos[package], package, version, new_version)

    for untouched in (seed, other):
        assert (untouched / f'{package}.spec').read_bytes() == original_spec
        assert get_last_commit_info(untouched) == original_commit
    assert subprocess.check_output(
        ['git', 'for-each-ref', '--format=%(refname)', 'refs/heads'],
        cwd=other, text=True,
    ) == subprocess.check_output(
        ['git', 'for-each-ref', '--format=%(refname)', 'refs/heads'],
        cwd=seed, text=True,
    )


def test_import(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Import a new package."""
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, capture_output=True, check=True, text=True
    )

    assert "Successfully imported chocolate" in result.stderr

    chocolate_dir = workdir / 'rpms' / 'chocolate'
    assert (chocolate_dir / 'chocolate.spec').exists()
    assert not (chocolate_dir / '.git').exists()

    import_json_file = workdir / 'metadata' / 'chocolate.json'
    assert import_json_file.exists()
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['fedora']['branch'] == 'rawhide'
    assert import_data['version'] == '10'
    assert import_data['fedora']['release'] == '1'
    assert import_data['upstream_repo'] == 'https://src.fedoraproject.org/rpms/chocolate'

    subject, body = get_last_commit_info(workdir)
    assert subject == 'Import chocolate-10-1'
    assert 'Branch: rawhide' in body
    assert f"Upstream: {import_data['fedora']['sha']}" in body

    # did not miss any changes
    status_result = subprocess.run(
        ['git', 'status', '--porcelain'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    assert status_result.stdout == '', f"git status should be clean after import, found:\n{status_result.stdout}"


def test_import_dry_run(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """--dry-run prevents commits."""
    # Get initial commit (from fixture setup)
    initial_subject, _ = get_last_commit_info(workdir)
    initial_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=workdir)
    initial_index = subprocess.check_output(['git', 'diff', '--cached'], cwd=workdir)
    generated_paths = [workdir / name for name in (
        '.tekton/rpms-on-pull-request.yaml', '.tekton/rpms-on-promotion.yaml', 'konflux-templates/rendered.yml',
    )]
    generated_before = {path: path.read_bytes() for path in generated_paths}

    # Import with --dry-run
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True, capture_output=True, text=True
    )

    assert "Successfully imported chocolate" in result.stderr

    # Verify files were imported
    chocolate_dir = workdir / 'rpms' / 'chocolate'
    assert (chocolate_dir / 'chocolate.spec').exists()

    # Verify metadata was created
    import_json_file = workdir / 'metadata' / 'chocolate.json'
    assert import_json_file.exists()
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['version'] == '10'

    # Verify no commit was created (still at initial commit)
    subject, _ = get_last_commit_info(workdir)
    assert subject == initial_subject
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=workdir) == initial_head
    assert subprocess.check_output(['git', 'diff', '--cached'], cwd=workdir) == initial_index
    for path in generated_paths:
        assert path.read_bytes() != generated_before[path], f'Import did not regenerate {path.name}'
        assert b'chocolate-main' in path.read_bytes()


def test_import_sign_off(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """--sign-off adds Signed-off-by trailer to commits."""
    # Import with --sign-off
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--sign-off', 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, capture_output=True, text=True, check=True
    )

    assert "Successfully imported vanilla" in result.stderr

    # Verify the import worked
    import_json_file = workdir / 'metadata' / 'vanilla.json'
    assert import_json_file.exists()
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['version'] == '1.0'

    # Verify commit has sign-off trailer
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Import vanilla-1.0-1'
    assert f"Upstream: {import_data['fedora']['sha']}" in body
    assert 'Signed-off-by: Test <test@example.com>' in body


def test_import_with_branch(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Importing from a specific branch."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', '--branch', 'f40', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Check metadata has correct branch and version
    import_json_file = workdir / 'metadata' / 'chocolate.json'
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['fedora']['branch'] == 'f40'
    assert import_data['version'] == '4'
    assert import_data['fedora']['release'] == '1'

    subject, body = get_last_commit_info(workdir)
    assert subject == 'Import chocolate-4-1'
    assert 'Branch: f40' in body
    assert f"Upstream: {import_data['fedora']['sha']}" in body


def test_import_existing_package_fails(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Importing an existing package fails."""
    # Create vanilla directory to simulate existing package
    (workdir / 'rpms' / 'vanilla').mkdir()

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, capture_output=True, text=True
    )

    assert result.returncode == 1
    assert "ERROR: Package directory rpms/vanilla/ already exists" in result.stderr


def test_import_with_ref(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Import with --ref to get an older commit, then update moves to latest."""
    # Get initial SHA of chocolate
    old_sha = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=upstream_repos["chocolate"], text=True, stdout=subprocess.PIPE, check=True
    ).stdout.strip()

    # Add new commits to upstream
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')
    new_sha = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '11', '12')

    # Import using --ref to get the old version
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', '--ref', old_sha, f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Check that we imported the old version
    import_json_file = workdir / 'metadata' / 'chocolate.json'
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['fedora']['sha'] == old_sha
    assert import_data['version'] == '10'
    assert import_data['fedora']['branch'] == 'rawhide'

    # Verify commit message includes the old SHA
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Import chocolate-10-1'
    assert f"Upstream: {old_sha}" in body

    # Now run update to move to latest version
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Check that we're now at the latest version
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['fedora']['sha'] == new_sha
    assert import_data['version'] == '12'

    # Verify update commit was created
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Update chocolate from 10-1 to 12-1'
    assert f"Upstream: {new_sha}" in body


@pytest.mark.parametrize('release_line,expected_release', [
    ('Release: %autorelease', 'Release: 5%{?dist}\n'),
    ('Release:        %{autorelease}', 'Release:        5%{?dist}\n'),  # Preserves whitespace
    ('Release: %autorelease -p -s snapshot123', 'Release: 5%{?dist}\n'),  # Arguments consumed
])
def test_import_autorelease(workdir: Path, upstream_repos: dict[str, Path], dist_git_module,
                            release_line: str, expected_release: str) -> None:
    """Test importing a package with %autorelease in Release line."""
    # Modify vanilla to use %autorelease
    vanilla_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    vanilla_spec.write_text(spec_content.replace('Release: 1', release_line))
    subprocess.run(['git', 'commit', '-am', 'Use autorelease'], cwd=upstream_repos['vanilla'], check=True)

    # Mock get_mdapi_latest_build to return release "5"
    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '5.fc99'}

        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_repos["vanilla"]}')

    # Verify %autorelease was replaced in spec file
    spec_content = (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()
    assert expected_release in spec_content
    assert '%autorelease' not in spec_content

    # Verify metadata has correct release
    with open(workdir / 'metadata' / 'vanilla.json') as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.0'
    assert metadata['fedora']['release'] == '5'


# HUM-11324: dist_git.py import --ref resolves %autorelease from wrong Koji build
def test_import_ref_autorelease_uses_koji(workdir: Path, upstream_repos: dict[str, Path],
                                          dist_git_module) -> None:
    """Import --ref with %autorelease resolves release from Koji, not MDAPI.

    When a pinned commit is requested, MDAPI may return the release for a
    different version.  The import must use resolve_release_from_koji() which
    validates both version and commit.  HUM-11324.
    """
    repo = upstream_repos['vanilla']
    spec = repo / 'vanilla.spec'

    # Switch to %autorelease
    spec.write_text(spec.read_text().replace('Release: 1%{?dist}', 'Release: %autorelease'))
    subprocess.run(['git', 'commit', '-am', 'Switch to autorelease'], cwd=repo, check=True)
    autorelease_sha = subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=repo, text=True,
        stdout=subprocess.PIPE, check=True,
    ).stdout.strip()

    # Commit 2: bump to version 2.0 (still %autorelease)
    spec.write_text(spec.read_text().replace('Version: 1.0', 'Version: 2.0'))
    subprocess.run(['git', 'commit', '-am', 'Bump to 2.0'], cwd=repo, check=True)

    # Import the v1.0 autorelease commit by --ref.
    # MDAPI would return the *current* build (version 2.0, release 1) — wrong version.
    # Koji should be queried instead and return release "2" for version 1.0 at that commit.
    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi, \
         patch.object(dist_git_module, 'resolve_release_from_koji', return_value='2') as mock_koji:
        mock_mdapi.return_value = {'version': '2.0', 'release': '1.fc99'}

        run_dist_git(dist_git_module, workdir, 'import', '--ref', autorelease_sha,
                     f'file://{repo}')

    # resolve_release_from_koji must have been called with the pinned version and commit
    mock_koji.assert_called_once_with('vanilla', '1.0', autorelease_sha)
    # MDAPI should NOT have been consulted
    mock_mdapi.assert_not_called()

    # Spec and metadata must reflect the Koji-resolved release
    spec_content = (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()
    assert 'Release: 2%{?dist}' in spec_content

    with open(workdir / 'metadata' / 'vanilla.json') as f:
        metadata = json.load(f)
    assert metadata['fedora']['release'] == '2'
    assert metadata['fedora']['sha'] == autorelease_sha
    assert metadata['version'] == '1.0'


def test_import_ref_autorelease_no_koji_build_fails(workdir: Path, upstream_repos: dict[str, Path],
                                                     dist_git_module) -> None:
    """Import --ref with %autorelease must fail when no Koji build matches.

    Rather than silently falling back to MDAPI (which returns an unrelated
    version's release), the tool should exit with an error.  HUM-11324.
    """
    repo = upstream_repos['vanilla']
    spec = repo / 'vanilla.spec'

    spec.write_text(spec.read_text().replace('Release: 1%{?dist}', 'Release: %autorelease'))
    subprocess.run(['git', 'commit', '-am', 'Switch to autorelease'], cwd=repo, check=True)
    sha = subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=repo, text=True,
        stdout=subprocess.PIPE, check=True,
    ).stdout.strip()

    with patch.object(dist_git_module, 'resolve_release_from_koji', return_value=None), \
         patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi, \
         pytest.raises(SystemExit):
        mock_mdapi.return_value = {'version': '2.0', 'release': '1.fc99'}
        run_dist_git(dist_git_module, workdir, 'import', '--ref', sha,
                     f'file://{repo}')

    mock_mdapi.assert_not_called()
    assert not (workdir / 'rpms' / 'vanilla').exists(), "failed import should clean up the cloned directory"


def test_import_anitya_found(workdir: Path, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Import stores release_monitoring_project_id when Anitya lookup succeeds."""
    with patch.object(dist_git_module.urllib.request, 'urlopen', return_value=BytesIO(b'{"id": 12345}')):
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_repos["vanilla"]}')

    with open(workdir / 'metadata' / 'vanilla.json') as f:
        metadata = json.load(f)
    assert metadata['release_monitoring_project_id'] == 12345
    assert metadata['track_upstream'] == 'latest'


def test_import_anitya_not_found(workdir: Path, upstream_repos: dict[str, Path], dist_git_module,
                                  caplog) -> None:
    """Import warns with set-upstream instructions when Anitya lookup returns None."""
    import logging
    with caplog.at_level(logging.WARNING, logger='root'):
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_repos["vanilla"]}')

    with open(workdir / 'metadata' / 'vanilla.json') as f:
        metadata = json.load(f)
    assert 'release_monitoring_project_id' not in metadata

    warning_text = ' '.join(caplog.messages)
    assert 'set-upstream' in warning_text
    assert '--project-id' in warning_text
    assert 'release-monitoring.org' in warning_text


def test_update(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """update command"""
    workdir = imported_workdir('vanilla', 'chocolate')
    # Get the sha that was imported
    chocolate_import_json = workdir / 'metadata' / 'chocolate.json'
    with open(chocolate_import_json) as f:
        chocolate_import_data = json.load(f)
    initial_sha = chocolate_import_data['fedora']['sha']

    # Update upstream chocolate
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')

    # Case 3: Create a modified package (strawberry) - import old chocolate, modify it, then chocolate gets updated
    strawberry_dir = workdir / 'rpms' / 'strawberry'
    strawberry_dir.mkdir()
    chocolate_spec = (workdir / 'rpms' / 'chocolate' / 'chocolate.spec')
    # Copy chocolate spec to strawberry and modify it
    (strawberry_dir / 'chocolate.spec').write_text(chocolate_spec.read_text() + '\n# Local modification\n')
    # Add strawberry metadata (copy chocolate's old metadata, so it has an update available)
    strawberry_import_json = workdir / 'metadata' / 'strawberry.json'
    strawberry_import_data = chocolate_import_data.copy()
    strawberry_import_data['modification_status'] = 'modified'
    strawberry_import_data['modification_reason'] = 'Test local modification'
    with open(strawberry_import_json, 'w') as f:
        json.dump(strawberry_import_data, f, indent=2, sort_keys=True)
        f.write('\n')

    # Case 4: Downstream-only package (no metadata)
    mango_dir = workdir / 'rpms' / 'mango'
    mango_dir.mkdir()
    (mango_dir / 'mango.spec').write_text('Name: mango\nVersion: 1.0\nRelease: 1\n')

    # Let's not cover Koji check here
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check'],
        cwd=workdir, capture_output=True, text=True, check=True
    )

    # Check results
    with open(chocolate_import_json) as f:
        chocolate_import_data = json.load(f)

    # Case 1: vanilla should be unchanged (already up-to-date)
    assert "already up-to-date" in result.stderr or "vanilla" in result.stderr

    # Case 2: chocolate should be updated to new version
    assert chocolate_import_data['fedora']['sha'] != initial_sha, "chocolate should have been updated"
    assert chocolate_import_data['version'] == '11', "chocolate should be at version 11"
    assert "Updating chocolate" in result.stderr

    # Verify commits were created for chocolate and strawberry updates
    # strawberry is last (alphabetical order) so check it first (last commit)
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Update strawberry from 10-1 to 11-1'

    # Case 3: strawberry should be updated with merge (has upstream update and local modifications)
    assert "Updating strawberry" in result.stderr
    assert "Local modifications applied successfully" in result.stderr

    # Case 4: mango should not be mentioned (no metadata)
    assert "mango" not in result.stderr


def test_sync_restores_release_when_fedora_catches_up(
    imported_workdir, upstream_repos: dict[str, Path],
) -> None:
    """Sync returns an upstream-version package to Fedora's release."""
    workdir = imported_workdir('vanilla')

    spec_file = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_file.write_text(spec_file.read_text().replace(
        'Version: 1.0\nRelease: 1%{?dist}',
        'Version: 2.0\nRelease: 0.1%{?dist}',
    ))
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    metadata = json.loads(metadata_file.read_text())
    metadata['fedora'].pop('release')
    metadata['version'] = '2.0'
    metadata['modification_status'] = 'modified'
    metadata['modification_reason'] = 'Update to upstream version 2.0'
    metadata_file.write_text(json.dumps(metadata, indent=2) + '\n')
    subprocess.run(['git', 'commit', '-am', 'Update vanilla ahead of Fedora'], cwd=workdir, check=True)

    add_upstream_commit(upstream_repos['vanilla'], 'vanilla', '1.0', '2.0')
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', 'vanilla'],
        cwd=workdir, check=True,
    )

    metadata = json.loads(metadata_file.read_text())
    assert metadata['version'] == '2.0'
    assert metadata['fedora']['release'] == '1'
    assert metadata['modification_status'] == 'clean'
    assert 'modification_reason' not in metadata


def test_update_uses_ls_remote(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """update uses ls-remote optimization for up-to-date packages."""
    workdir = imported_workdir('vanilla')

    # Create a mock git wrapper that logs commands
    log_file = workdir / 'git_commands.log'
    bin_dir = workdir / 'bin'
    bin_dir.mkdir()
    mock_git = bin_dir / 'git'
    mock_git.write_text(f"""#!/bin/bash
echo "$@" >> {log_file}
exec /usr/bin/git "$@"
""")
    mock_git.chmod(0o755)

    # Update PATH to use mock git
    env = os.environ.copy()
    env['PATH'] = f"{bin_dir}:{env['PATH']}"

    # Run update with mock git
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True, env=env
    )

    assert "already up-to-date" in result.stderr

    # Should use ls-remote (not clone) to check if package is up-to-date
    git_commands = log_file.read_text().strip().split('\n')
    # Filter out config checks
    non_config_commands = [cmd for cmd in git_commands if not cmd.startswith('config ')]
    assert non_config_commands == ["ls-remote file://" + str(upstream_repos["vanilla"]) + " rawhide"]


def test_update_unbuilt(imported_workdir, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Update skips packages not built in Koji."""
    workdir = imported_workdir('chocolate')

    # Update upstream chocolate
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')

    # Mock Koji to return no build found.
    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()
        mock_server.getBuild.return_value = None  # Build not found
        mock_server.search.return_value = []
        mock_server_class.return_value = mock_server

        # Run update in-process
        run_dist_git(dist_git_module, workdir, 'update', 'chocolate')
        mock_server.getBuild.assert_called_once_with('chocolate-11-1.fc99')
        mock_server.search.assert_called_once_with(
            'chocolate-11-1.*', 'build', 'glob'
        )
        chocolate_import_json = workdir / 'metadata' / 'chocolate.json'
        with open(chocolate_import_json) as f:
            import_data = json.load(f)
        assert import_data['version'] == '10', "Should not update when build missing in Koji"


def test_update_built(imported_workdir, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Update proceeds when built in Koji."""
    workdir = imported_workdir('chocolate')

    # Update upstream chocolate
    new_sha = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')

    # Mock Koji to return a successful build with matching commit
    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()
        mock_server.getBuild.return_value = {
            'build_id': 12345,
            'nvr': 'chocolate-11-1.fc99',
            'state': 1,  # COMPLETE
            'source': f'git+https://example.com/chocolate.git#{new_sha}'
        }
        mock_server_class.return_value = mock_server

        # Run update in-process
        run_dist_git(dist_git_module, workdir, 'update', 'chocolate')
        mock_server.getBuild.assert_called_once_with('chocolate-11-1.fc99')
        with open(workdir / 'metadata' / 'chocolate.json') as f:
            import_data = json.load(f)
        assert import_data['version'] == '11'
        assert import_data['fedora']['sha'] == new_sha


def test_update_branch_dist_tag(imported_workdir, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Update uses correct dist tag for non-rawhide branches."""
    workdir = imported_workdir('chocolate', branch='f40')

    # Switch to f40 branch and update upstream chocolate
    subprocess.run(['git', 'checkout', 'f40'], cwd=upstream_repos["chocolate"], check=True)
    new_sha = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '4', '5')

    # Mock Koji to return a successful build
    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()
        mock_server.getBuild.return_value = {
            'build_id': 12345,
            'nvr': 'chocolate-5-1.fc40',
            'state': 1,  # COMPLETE
            'source': f'git+https://example.com/chocolate.git#{new_sha}'
        }
        mock_server_class.return_value = mock_server

        # Run update in-process
        run_dist_git(dist_git_module, workdir, 'update', 'chocolate')
        # Should query for .fc40 (from branch f40), not rawhide version
        mock_server.getBuild.assert_called_once_with('chocolate-5-1.fc40')
        chocolate_import_json = workdir / 'metadata' / 'chocolate.json'
        with open(chocolate_import_json) as f:
            import_data = json.load(f)
        assert import_data['version'] == '5'
        assert import_data['fedora']['sha'] == new_sha


def test_update_rawhide_fallback(imported_workdir, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Update finds rawhide build with previous release dist tag when current not found."""
    workdir = imported_workdir('vanilla')

    # Update upstream vanilla
    new_sha = add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '2.0')

    # Mock Koji to return no fc99 build and find an fc40 build through search.
    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()
        mock_server.search.return_value = [{'name': 'vanilla-2.0-1.fc40'}]

        def getBuild_side_effect(nvr):
            if nvr == 'vanilla-2.0-1.fc99':
                return None  # Current rawhide not found
            elif nvr == 'vanilla-2.0-1.fc40':
                return {
                    'build_id': 12345,
                    'nvr': 'vanilla-2.0-1.fc40',
                    'state': 1,  # COMPLETE
                    'source': f'git+https://example.com/vanilla.git#{new_sha}'
                }
            return None

        mock_server.getBuild.side_effect = getBuild_side_effect
        mock_server_class.return_value = mock_server

        # Run update in-process
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla')

        # Should have called getBuild twice: once for fc99, then fallback to fc40
        assert mock_server.getBuild.call_count == 2
        mock_server.getBuild.assert_any_call('vanilla-2.0-1.fc99')
        mock_server.getBuild.assert_any_call('vanilla-2.0-1.fc40')
        mock_server.search.assert_called_once_with(
            'vanilla-2.0-1.*', 'build', 'glob'
        )

        # Package should be updated using the fallback
        vanilla_import_json = workdir / 'metadata' / 'vanilla.json'
        with open(vanilla_import_json) as f:
            import_data = json.load(f)
        assert import_data['version'] == '2.0'
        assert import_data['fedora']['sha'] == new_sha


def test_update_rawhide_finds_build_for_unknown_dist_tag(
    imported_workdir, upstream_repos: dict[str, Path], dist_git_module
) -> None:
    """Update finds the rawhide build when the local release list is stale."""
    workdir = imported_workdir('vanilla')

    new_sha = add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '2.0')

    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()
        unknown_nvr = 'vanilla-2.0-1.fc100'
        el_nvr = 'vanilla-2.0-1.el10'
        mock_server.search.return_value = [
            {'name': el_nvr},
            {'name': unknown_nvr},
        ]

        def getBuild_side_effect(nvr):
            if nvr == 'vanilla-2.0-1.fc99':
                return {
                    'nvr': nvr,
                    'state': 1,
                    'source': 'git+https://example.com/vanilla.git#' + 'a' * 40,
                }
            if nvr == unknown_nvr:
                return {
                    'nvr': nvr,
                    'state': 1,
                    'source': f'git+https://example.com/vanilla.git#{new_sha}',
                }
            return None

        mock_server.getBuild.side_effect = getBuild_side_effect
        mock_server_class.return_value = mock_server

        run_dist_git(dist_git_module, workdir, 'update', 'vanilla')

        mock_server.getBuild.assert_any_call('vanilla-2.0-1.fc99')
        mock_server.getBuild.assert_any_call(unknown_nvr)
        assert call(el_nvr) not in mock_server.getBuild.call_args_list
        mock_server.search.assert_called_once_with(
            'vanilla-2.0-1.*', 'build', 'glob'
        )

    with open(workdir / 'metadata' / 'vanilla.json') as f:
        import_data = json.load(f)
    assert import_data['version'] == '2.0'
    assert import_data['fedora']['sha'] == new_sha


def test_check_koji_build_finds_numbered_eln_suffix(dist_git_module) -> None:
    """ELN lookup accepts numbered ELN build suffixes, but not Fedora builds."""
    expected_commit = 'a' * 40
    eln_nvr = 'vanilla-2.0-1.eln159'
    fedora_nvr = 'vanilla-2.0-1.fc46'

    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()
        mock_server.search.return_value = [
            {'name': fedora_nvr},
            {'name': eln_nvr},
        ]

        def getBuild_side_effect(nvr):
            if nvr == eln_nvr:
                return {
                    'nvr': nvr,
                    'state': 1,
                    'source': f'git+https://example.com/vanilla.git#{expected_commit}',
                }
            return None

        mock_server.getBuild.side_effect = getBuild_side_effect
        mock_server_class.return_value = mock_server

        assert dist_git_module.check_koji_build(
            'vanilla', '2.0', '1', expected_commit, 'eln', 'eln'
        )
        assert call(fedora_nvr) not in mock_server.getBuild.call_args_list
        mock_server.getBuild.assert_any_call(eln_nvr)


@pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
def test_koji_retries_protocol_error_by_status(dist_git_module, status: int) -> None:
    """The numeric status wins even when the URL/message contains other codes."""
    failure = xmlrpc.client.ProtocolError(
        'https://koji.example/401/kojihub', status, 'error reference 404', {},
    )
    success = {'build_id': 12345}
    method = MagicMock(side_effect=[failure, failure, success])
    with patch.object(dist_git_module.time, 'sleep') as sleep:
        assert dist_git_module.call_koji_with_retry(method, 'test-nvr') is success

    assert method.call_args_list == [call('test-nvr')] * 3
    assert sleep.call_args_list == [call(2), call(4)]


@pytest.mark.parametrize('status', [400, 401, 403, 404, 405, 501, 505])
def test_koji_does_not_retry_permanent_protocol_error(dist_git_module, status: int) -> None:
    """Permanent HTTP statuses fail immediately, even if their text says 503."""
    failure = xmlrpc.client.ProtocolError('https://koji.example/503', status, '503 Gateway', {})
    method = MagicMock(side_effect=failure)
    with patch.object(dist_git_module.time, 'sleep') as sleep, \
         pytest.raises(xmlrpc.client.ProtocolError) as raised:
        dist_git_module.call_koji_with_retry(method, 'test-nvr')

    assert raised.value is failure
    method.assert_called_once_with('test-nvr')
    sleep.assert_not_called()


def test_koji_reraises_final_protocol_error(dist_git_module) -> None:
    """A persistent transient HTTP error is bounded to three attempts."""
    failures = [xmlrpc.client.ProtocolError('https://koji.example', 503, 'Unavailable', {})
                for _ in range(3)]
    method = MagicMock(side_effect=failures)
    with patch.object(dist_git_module.time, 'sleep') as sleep, \
         pytest.raises(xmlrpc.client.ProtocolError) as raised:
        dist_git_module.call_koji_with_retry(method)

    assert raised.value is failures[-1]
    assert method.call_count == 3
    assert sleep.call_args_list == [call(2), call(4)]


@pytest.mark.parametrize('failure', [
    TimeoutError('request 401 timed out'),
    ConnectionResetError('connection 404 reset'),
    http.client.RemoteDisconnected('remote closed connection'),
    http.client.IncompleteRead(b'partial response'),
    http.client.BadStatusLine('truncated HTTP response'),
])
def test_koji_retries_transport_failure(dist_git_module, failure: Exception) -> None:
    """Transient transport errors must not be classified by message substrings."""
    method = MagicMock(side_effect=[failure, 'success'])
    with patch.object(dist_git_module.time, 'sleep') as sleep:
        assert dist_git_module.call_koji_with_retry(method) == 'success'

    assert method.call_count == 2
    sleep.assert_called_once_with(2)


def test_koji_reraises_final_transport_timeout(dist_git_module) -> None:
    failures = [TimeoutError('timed out') for _ in range(3)]
    method = MagicMock(side_effect=failures)
    with patch.object(dist_git_module.time, 'sleep') as sleep, \
         pytest.raises(TimeoutError) as raised:
        dist_git_module.call_koji_with_retry(method)

    assert raised.value is failures[-1]
    assert method.call_count == 3
    assert sleep.call_args_list == [call(2), call(4)]


@pytest.mark.parametrize('proxy_variable', [None, 'HTTPS_PROXY', 'https_proxy'])
def test_koji_transport_connection_has_timeout(
    dist_git_module, monkeypatch: pytest.MonkeyPatch, proxy_variable: str | None,
) -> None:
    """Direct and tunneled connections have bounded socket operations."""
    monkeypatch.delenv('HTTPS_PROXY', raising=False)
    monkeypatch.delenv('https_proxy', raising=False)
    if proxy_variable:
        monkeypatch.setenv(proxy_variable, 'http://proxy.example:3129')
    context = MagicMock()
    transport = dist_git_module.KojiTransport(context=context)
    with patch.object(dist_git_module.http.client, 'HTTPSConnection') as factory:
        connection = transport.make_connection('koji.fedoraproject.org')
        assert factory.call_args.kwargs.get('timeout') == 30
        assert factory.call_args.kwargs.get('context') is context
        assert connection is factory.return_value
        if proxy_variable:
            assert factory.call_args.args == ('proxy.example', 3129)
            connection.set_tunnel.assert_called_once_with('koji.fedoraproject.org')
        else:
            assert factory.call_args.args[0] == 'koji.fedoraproject.org'
            connection.set_tunnel.assert_not_called()
        assert transport.make_connection('koji.fedoraproject.org') is connection
        factory.assert_called_once()
        transport.close()
        connection.close.assert_called_once()


@pytest.mark.parametrize('status, recover', [(503, True), (503, False), (401, False)])
def test_koji_chunked_error_discards_cached_connection(
    dist_git_module, monkeypatch: pytest.MonkeyPatch, status: int, recover: bool,
) -> None:
    """Real HTTPConnection state must recover after an unread chunked error."""
    monkeypatch.delenv('HTTPS_PROXY', raising=False)
    monkeypatch.delenv('https_proxy', raising=False)
    class FakeSocket:
        def __init__(self, response):
            self.response = response
            self.closed = False

        def makefile(self, *args, **kwargs):
            return BytesIO(self.response)

        def sendall(self, data):
            pass

        def close(self):
            self.closed = True

    sockets: list[FakeSocket] = []

    def connect(*args, **kwargs):
        code = 200 if recover and sockets else status
        if code == 200:
            body = xmlrpc.client.dumps(({'build_id': 12345},), methodresponse=True).encode()
            response = b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body
        else:
            response = (f'HTTP/1.1 {code} Error\r\nTransfer-Encoding: chunked\r\n'
                        'Connection: keep-alive\r\n\r\n3\r\nbad\r\n0\r\n\r\n').encode()
        sock = FakeSocket(response)
        sockets.append(sock)
        connection = http.client.HTTPConnection('koji.example')
        connection.sock = sock
        return connection

    transport = dist_git_module.KojiTransport()
    with patch.object(dist_git_module.http.client, 'HTTPSConnection', side_effect=connect), \
         patch.object(dist_git_module.time, 'sleep') as sleep, \
         xmlrpc.client.ServerProxy('https://koji.example/kojihub', transport=transport) as server:
        if recover:
            assert dist_git_module.call_koji_with_retry(server.getBuild, 'test-nvr') == {'build_id': 12345}
            assert len(sockets) == 2
            sleep.assert_called_once_with(2)
        else:
            with pytest.raises(xmlrpc.client.ProtocolError) as raised:
                dist_git_module.call_koji_with_retry(server.getBuild, 'test-nvr')
            assert raised.value.errcode == status
            assert len(sockets) == (3 if status == 503 else 1)
            assert sleep.call_count == (2 if status == 503 else 0)
        assert sockets[0].closed, 'HTTP error left its socket cached for reuse'


def test_koji_retry_on_transient_error(dist_git_module) -> None:
    """Verify that 502 Bad Gateway errors are retried and eventually succeed."""

    with patch('xmlrpc.client.ServerProxy') as mock_server_class, \
         patch.object(dist_git_module.time, 'sleep') as mock_sleep:
        mock_server = MagicMock()

        # First two attempts fail with 502, third succeeds
        call_count = 0
        def getBuild_side_effect(nvr):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise xmlrpc.client.ProtocolError('https://koji.example', 502, 'Bad Gateway', {})
            return {
                'build_id': 12345,
                'nvr': 'test-1.0-1.fc40',
                'state': 1,
                'source': 'git://example.com/test#abc123',
            }

        mock_server.getBuild.side_effect = getBuild_side_effect
        mock_server_class.return_value = mock_server

        # Call should succeed after retries
        result = dist_git_module.call_koji_with_retry(
            mock_server.getBuild, 'test-1.0-1.fc40',
            method_name="Koji getBuild(test-1.0-1.fc40)"
        )

        assert result is not None
        assert result['build_id'] == 12345
        assert mock_server.getBuild.call_count == 3  # 2 failures + 1 success
        assert mock_sleep.call_args_list == [call(2), call(4)]


def test_koji_no_retry_on_fault(dist_git_module) -> None:
    """Ensure XML-RPC Fault errors fail immediately without retries."""
    import xmlrpc.client

    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()

        # Raise Fault on every call
        mock_server.getBuild.side_effect = xmlrpc.client.Fault(1000, "Invalid build")
        mock_server_class.return_value = mock_server

        # Should raise immediately without retrying
        with pytest.raises(xmlrpc.client.Fault):
            dist_git_module.call_koji_with_retry(
                mock_server.getBuild, 'test-1.0-1.fc40',
                method_name="Koji getBuild(test-1.0-1.fc40)"
            )

        # Should have called only once (no retries)
        assert mock_server.getBuild.call_count == 1


def test_koji_no_retry_on_auth_error(dist_git_module) -> None:
    """Ensure HTTP 401/403 errors fail immediately without retries."""

    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()

        # Raise 401 Unauthorized
        mock_server.getBuild.side_effect = xmlrpc.client.ProtocolError(
            'https://koji.example', 401, 'Unauthorized', {},
        )
        mock_server_class.return_value = mock_server

        # Should raise immediately without retrying
        with pytest.raises(xmlrpc.client.ProtocolError):
            dist_git_module.call_koji_with_retry(
                mock_server.getBuild, 'test-1.0-1.fc40',
                method_name="Koji getBuild(test-1.0-1.fc40)"
            )

        # Should have called only once (no retries)
        assert mock_server.getBuild.call_count == 1


def test_koji_retry_on_connection_error(dist_git_module) -> None:
    """Verify network connection errors are retried with exponential backoff."""
    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()

        # First attempt fails with ConnectionError, second succeeds
        call_count = 0
        def getBuild_side_effect(nvr):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Connection reset by peer")
            return {
                'build_id': 12345,
                'nvr': 'test-1.0-1.fc40',
                'state': 1,
                'source': 'git://example.com/test#abc123',
            }

        mock_server.getBuild.side_effect = getBuild_side_effect
        mock_server_class.return_value = mock_server

        # Mock time.sleep to verify exponential backoff
        with patch('time.sleep') as mock_sleep:
            result = dist_git_module.call_koji_with_retry(
                mock_server.getBuild, 'test-1.0-1.fc40',
                method_name="Koji getBuild(test-1.0-1.fc40)"
            )

            assert result is not None
            assert result['build_id'] == 12345
            assert mock_server.getBuild.call_count == 2  # 1 failure + 1 success

            # Verify sleep was called with 2 seconds (first retry delay)
            mock_sleep.assert_called_once_with(2)


@pytest.mark.parametrize('mark_modified', [False, True], ids=['clean-metadata', 'modified-metadata'])
def test_sync(imported_workdir, upstream_repos: dict[str, Path], mark_modified: bool) -> None:
    """Sync discards local modifications."""
    workdir = imported_workdir('chocolate')

    # Make a local modification, optionally declare it, and commit it
    choc_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    choc_spec.write_text(choc_spec.read_text() + '\n# Local modification\n')
    if mark_modified:
        subprocess.run(
            [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', 'chocolate', '--modified',
             '--reason', 'Local test modification'],
            cwd=workdir, check=True,
        )

    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    new_sha = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')

    # sync succeeds and discards local modifications (automatically commits)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', 'chocolate'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    choc_spec_content = choc_spec.read_text()
    assert '# Local modification' not in choc_spec_content

    chocolate_import_json = workdir / 'metadata' / 'chocolate.json'
    with open(chocolate_import_json) as f:
        import_data = json.load(f)
    assert import_data['fedora']['sha'] == new_sha
    assert import_data['version'] == '11'
    assert import_data['modification_status'] == 'clean'
    assert 'modification_reason' not in import_data

    # Verify sync commit was created
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Sync chocolate from 10-1 to 11-1'
    assert f"Upstream: {new_sha}" in body

    # Add another upstream commit
    new_sha2 = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '11', '12')

    # Update should now pull in the new version
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'chocolate'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "Updating chocolate" in result.stderr

    with open(chocolate_import_json) as f:
        import_data = json.load(f)
    assert import_data['fedora']['sha'] == new_sha2
    assert import_data['version'] == '12'

    # Verify update commit was created
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Update chocolate from 11-1 to 12-1'
    assert f"Upstream: {new_sha2}" in body


def test_sync_already_in_sync(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Sync fails when already at upstream."""
    workdir = imported_workdir('chocolate')

    # Sync without upstream changes fails
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', 'chocolate'],
        cwd=workdir, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "already at upstream" in result.stderr


def test_sync_mark(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Sync --mark creates proper commit when already at upstream."""
    workdir = imported_workdir('chocolate')

    chocolate_import_json = workdir / 'metadata' / 'chocolate.json'
    original_metadata = chocolate_import_json.read_bytes()

    # Get the import commit sha to extract Upstream value
    subject, body = get_last_commit_info(workdir)
    import re
    upstream_match = re.search(r'Upstream: ([0-9a-f]{40})', body)
    assert upstream_match
    original_sha = upstream_match.group(1)

    # Sync --mark when already at upstream should succeed
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', '--mark', 'chocolate'],
        cwd=workdir, capture_output=True, text=True, check=True
    )

    # Verify sync commit was created even though no upstream changes
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Sync chocolate to 10-1 (mark)'
    assert f"Upstream: {original_sha}" in body

    # Metadata file should be bitwise identical
    assert chocolate_import_json.read_bytes() == original_metadata

    # Verify commit is empty (no actual changes)
    diff_result = subprocess.run(
        ['git', 'diff', 'HEAD~1', 'HEAD'],
        cwd=workdir, capture_output=True, text=True, check=True
    )
    assert diff_result.stdout == '', "Commit should be empty"


def test_sync_mark_resets_modification_status(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Sync --mark resets modification_status to clean and removes modification_reason."""
    workdir = imported_workdir('chocolate')

    chocolate_json = workdir / 'metadata' / 'chocolate.json'

    # Mark package as modified
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'chocolate', '--modified',
         '--reason', 'Test modification'],
        cwd=workdir, check=True,
    )

    # Verify it's marked as modified
    metadata = json.loads(chocolate_json.read_text())
    assert metadata['modification_status'] == 'modified'
    assert metadata['modification_reason'] == 'Test modification'

    # Sync --mark should reset to clean
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', '--mark', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify modification_status is clean and modification_reason is removed
    metadata = json.loads(chocolate_json.read_text())
    assert metadata['modification_status'] == 'clean'
    assert 'modification_reason' not in metadata


def test_git_config_required(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Fails with helpful message if git user/email not configured."""

    subprocess.run(['git', 'config', '--unset', 'user.name'], cwd=workdir, check=True)
    subprocess.run(['git', 'config', '--unset', 'user.email'], cwd=workdir, check=True)
    env = os.environ.copy()
    env['GIT_CONFIG_GLOBAL'] = '/dev/null'
    env['GIT_CONFIG_SYSTEM'] = '/dev/null'

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, capture_output=True, text=True, env=env
    )

    assert result.returncode != 0
    assert "Please configure git:" in result.stderr
    assert "git config user.name" in result.stderr

    # No changes were made (rpms/ should be unchanged)
    assert not (workdir / 'rpms' / 'chocolate').exists(), "Package should not be imported"

    # --dry-run doesn't require git config
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, capture_output=True, text=True, env=env, check=True
    )
    assert "Successfully imported chocolate" in result.stderr


def test_update_releases(workdir: Path, dist_git_module) -> None:
    """update-releases fetches from Bodhi and writes upstream-releases.json.

    Rawhide branch itself is not stored, but its dist_tag (e.g., f45) is captured.
    This handles the transition period when a new Fedora version hasn't branched
    yet and only exists as rawhide in Bodhi.
    """
    # mock curl response - rawhide's dist_tag should be captured
    mock_bodhi_response = {
        'releases': [
            {'id_prefix': 'FEDORA', 'branch': 'f41', 'dist_tag': 'f41'},
            {'id_prefix': 'FEDORA', 'branch': 'f43', 'dist_tag': 'f43'},
            {'id_prefix': 'FEDORA', 'branch': 'f44', 'dist_tag': 'f44'},
            {'id_prefix': 'FEDORA', 'branch': 'rawhide', 'dist_tag': 'f45'},
            {'id_prefix': 'FEDORA', 'branch': 'eln', 'dist_tag': 'eln'},
            {'id_prefix': 'FEDORA-EPEL', 'branch': 'epel9', 'dist_tag': 'epel9'},
        ]
    }

    with patch('subprocess.check_output') as mock_check_output:
        mock_check_output.return_value = json.dumps(mock_bodhi_response)

        # Run update-releases in-process
        run_dist_git(dist_git_module, workdir, 'update-releases')

    # Rawhide branch is NOT stored, but its dist_tag (f45) is captured
    assert json.loads((workdir / 'upstream-releases.json').read_text()) == {
        'centos': {
            'c9s': 'el9',
            'c10s': 'el10',
        },
        'fedora': {
            'eln': 'eln',
            'f41': 'f41',
            'f43': 'f43',
            'f44': 'f44',
            'f45': 'f45',  # rawhide's dist_tag is captured
            # EPEL should be filtered out (not FEDORA id_prefix)
        },
    }


def test_get_dist_tag_rawhide_auto_resolution(dist_git_module) -> None:
    """get_dist_tag automatically resolves rawhide to the highest Fedora release."""
    # Set up releases with various Fedora versions (rawhide not in the dict)
    dist_git_module.releases = {
        'fedora': {
            'f40': 'f40',
            'f42': 'f42',
            'f43': 'f43',
            'eln': 'eln',
        },
    }

    # rawhide should resolve to f43 (highest numbered release)
    assert dist_git_module.get_dist_tag('rawhide') == 'f43'

    # Other branches should work normally
    assert dist_git_module.get_dist_tag('f40') == 'f40'
    assert dist_git_module.get_dist_tag('f42') == 'f42'
    assert dist_git_module.get_dist_tag('eln') == 'eln'

    # CentOS Stream branches should still work
    assert dist_git_module.get_dist_tag('c9s') == 'el9'
    assert dist_git_module.get_dist_tag('c10s') == 'el10'


def test_expand_url_shortcut(dist_git_module) -> None:
    """expand_url_shortcut resolves fedora/ and centos/ shortcuts."""
    # Fedora shortcut
    assert dist_git_module.expand_url_shortcut('fedora/bash') == \
        'https://src.fedoraproject.org/rpms/bash.git'

    # CentOS shortcut
    assert dist_git_module.expand_url_shortcut('centos/kernel') == \
        'https://gitlab.com/redhat/centos-stream/rpms/kernel.git'

    # Full URL passes through unchanged
    full_url = 'https://gitlab.com/redhat/centos-stream/rpms/golang.git'
    assert dist_git_module.expand_url_shortcut(full_url) == full_url


@pytest.mark.parametrize('committed', [False, True], ids=['uncommitted', 'committed'])
def test_update_of_rebuild(imported_workdir, upstream_repos: dict[str, Path], committed: bool) -> None:
    """Update proceeds when local changes only affect Release: field.

    This commonly happens for rebuilds.
    """
    workdir = imported_workdir('vanilla')

    # Modify the Release: field locally (bump it for a rebuild)
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Release: 1', 'Release: 2')
    vanilla_spec.write_text(modified_content)
    if committed:
        subprocess.run(['git', 'commit', '-a', '-m', 'Rebuild because reasons'], cwd=workdir, check=True)

    # Add upstream commit with new version
    new_sha = add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '2.0')

    # Update should proceed despite local Release: modification
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Updates successfully
    assert "Updating vanilla" in result.stderr
    assert "local modifications" not in result.stderr
    vanilla_import_json = workdir / 'metadata' / 'vanilla.json'
    with open(vanilla_import_json) as f:
        import_data = json.load(f)
    assert import_data['fedora']['sha'] == new_sha
    assert import_data['version'] == '2.0'
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Update vanilla from 1.0-1 to 2.0-1'
    assert f"Upstream: {new_sha}" in body


@pytest.mark.parametrize('change_state', ['unstaged', 'staged', 'committed'])
@pytest.mark.parametrize('filename', ['vanilla.spec', 'local.patch'])
@pytest.mark.parametrize('dry_run', [False, True], ids=['update', 'dry-run'])
def test_update_refuses_clean_metadata_with_local_changes(
    imported_workdir, upstream_repos: dict[str, Path], change_state: str, filename: str,
    dry_run: bool,
) -> None:
    """A clean metadata flag must not authorize discarding package changes."""
    workdir = imported_workdir('vanilla')
    package_dir = workdir / 'rpms' / 'vanilla'
    local_file = package_dir / filename
    original = local_file.read_text() if local_file.exists() else ''
    local_file.write_text(original + '\n# Local modification\n')
    if change_state != 'unstaged':
        subprocess.run(['git', 'add', str(local_file)], cwd=workdir, check=True)
    if change_state == 'committed':
        subprocess.run(['git', 'commit', '-m', 'Local modification'], cwd=workdir, check=True)

    add_upstream_commit(upstream_repos['vanilla'], 'vanilla', '1.0', '2.0')
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    metadata_before = metadata_file.read_bytes()
    assert json.loads(metadata_before)['modification_status'] == 'clean'
    files_before = {p.name: p.read_bytes() for p in package_dir.iterdir()}

    def git_output(*args: str) -> str:
        return subprocess.run(
            ['git', *args], cwd=workdir, capture_output=True, text=True, check=True,
        ).stdout

    head_before = git_output('rev-parse', 'HEAD')
    status_before = git_output('status', '--porcelain')
    index_before = git_output('diff', '--cached')
    if change_state == 'committed':
        assert not status_before

    args = ['--dry-run'] if dry_run else []
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), *args, 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert 'vanilla' in result.stderr
    assert 'clean' in result.stderr
    assert 'mark-modified vanilla --modified --reason' in result.stderr
    assert 'sync vanilla' in result.stderr
    assert {p.name: p.read_bytes() for p in package_dir.iterdir()} == files_before
    assert metadata_file.read_bytes() == metadata_before
    assert git_output('rev-parse', 'HEAD') == head_before
    assert git_output('status', '--porcelain') == status_before
    assert git_output('diff', '--cached') == index_before


def test_update_refuses_local_changes_after_upstream_clone(
    imported_workdir, upstream_repos: dict[str, Path], dist_git_module,
) -> None:
    """The safety check uses the completed upstream clone before refusing updates."""
    workdir = imported_workdir('vanilla')
    local_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    local_spec.write_text(local_spec.read_text() + '\n# Local modification\n')
    add_upstream_commit(upstream_repos['vanilla'], 'vanilla', '1.0', '2.0')

    run_git = dist_git_module.run_git
    is_package_unmodified = dist_git_module.is_package_unmodified
    completed_clones: list[Path] = []
    compared_clones: list[Path] = []

    def record_git(*args: str, **kwargs):
        result = run_git(*args, **kwargs)
        if args[0] == 'clone':
            completed_clones.append(Path(args[-1]))
        return result

    def check_cloned_contents(package_name, metadata, upstream_dir):
        assert upstream_dir in completed_clones
        assert (upstream_dir / '.git' / 'HEAD').is_file()
        assert 'Version: 2.0' in (upstream_dir / 'vanilla.spec').read_text()
        compared_clones.append(upstream_dir)
        return is_package_unmodified(package_name, metadata, upstream_dir)

    with patch.object(dist_git_module, 'run_git', side_effect=record_git), \
         patch.object(dist_git_module, 'is_package_unmodified', side_effect=check_cloned_contents), \
         pytest.raises(SystemExit, match='metadata says clean but package has local modifications'):
        run_dist_git(dist_git_module, workdir, 'update', '--skip-build-check', 'vanilla')

    assert len(compared_clones) == 1


def test_update_commit_uses_local_package_name(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update commit message uses local directory name, not upstream repo name.

    When a package is imported with --directory (e.g. golang imported as golang1.26),
    the commit message must use the local name so that CI scripts can find the correct
    rpms/ directory for conflict detection.
    """
    # Import vanilla with a different directory name (simulates golang -> golang1.26)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', '--directory', 'vanilla1.0',
         f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Add upstream commit (Release bump only — version stays in 1.0 series)
    upstream_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    upstream_content = upstream_spec.read_text()
    updated = upstream_content.replace('Release: 1', 'Release: 2')
    upstream_spec.write_text(updated)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump release'],
                   cwd=upstream_repos['vanilla'], check=True)

    # Update
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla1.0'],
        cwd=workdir, check=True,
    )

    # Commit message must use local name "vanilla1.0", not upstream "vanilla"
    subject, _ = get_last_commit_info(workdir)
    assert subject == 'Update vanilla1.0 from 1.0-1 to 1.0-2'


def test_mark_modified_with_reason(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Mark a clean package as modified with a reason."""
    workdir = imported_workdir('vanilla')

    # Verify it starts as clean
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['modification_status'] == 'clean'
    assert 'modification_reason' not in metadata

    # Mark as modified with a reason
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Backport CVE fix from upstream'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Verify metadata updated
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['modification_status'] == 'modified'
    assert metadata['modification_reason'] == 'Backport CVE fix from upstream'


def test_mark_clean(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Mark a modified package back to clean."""
    workdir = imported_workdir('vanilla')

    # Mark as modified
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Test modification'],
        cwd=workdir, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['modification_status'] == 'modified'
    assert 'modification_reason' in metadata

    # Mark back to clean
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--clean'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Verify metadata updated
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['modification_status'] == 'clean'
    assert 'modification_reason' not in metadata


def test_mark_clean_refuses_upstream_modified(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Mark-clean is refused for upstream-modified packages without verified Fedora data."""
    workdir = imported_workdir('vanilla')

    # Manually set metadata to modified-upstream state (no fedora.release)
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    metadata['fedora'].pop('release')
    metadata['modification_status'] = 'modified'
    metadata['modification_reason'] = 'Ahead of Fedora'
    metadata['version'] = '2.0'
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2, sort_keys=True)
    subprocess.run(['git', 'commit', '-a', '-m', 'Set upstream-modified'], cwd=workdir, check=True)

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--clean'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert 'Cannot mark upstream-modified package' in result.stderr


@pytest.mark.parametrize("modify_release", [False, True], ids=["clean", "with_release"])
def test_update_merge_clean(imported_workdir, upstream_repos: dict[str, Path], modify_release: bool) -> None:
    """Update automatically merges non-conflicting local and upstream changes.

    Tests both:
    - Clean merge without local Release: changes
    - Merge with local Release: bump that is normalized out
    """
    workdir = imported_workdir('vanilla')

    # Make local modification: add comment and optionally bump Release
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_spec = spec_content.replace('%description', '# Local comment\n%description')
    if modify_release:
        modified_spec = modified_spec.replace('Release: 1', 'Release: 1.1')

    vanilla_spec.write_text(modified_spec)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Local comment added', 'vanilla'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    # Make upstream change: bump Release and add different comment
    upstream_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    upstream_content = upstream_spec.read_text()
    updated_upstream = upstream_content.replace('Release: 1', 'Release: 2')
    updated_upstream = updated_upstream.replace('%files', '# Upstream comment\n%files')
    upstream_spec.write_text(updated_upstream)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump release and add comment'],
                   cwd=upstream_repos['vanilla'], check=True)

    # update automatically merges
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Merge failed: {result.stderr}"

    # Verify both local and upstream comments are present, upstream Release wins
    merged_spec = vanilla_spec.read_text()
    assert '# Local comment' in merged_spec, "Local modification missing"
    assert '# Upstream comment' in merged_spec, "Upstream change missing"
    assert 'Release: 2' in merged_spec, "Should use upstream Release"
    if modify_release:
        assert 'Release: 1.1' not in merged_spec, "Local Release bump should not persist"

    # Verify commit message
    subject, body = get_last_commit_info(workdir)
    assert subject.startswith('Update vanilla from')
    assert 'Upstream:' in body

    # Verify modification_status is still 'modified'
    metadata = json.loads((workdir / 'metadata' / 'vanilla.json').read_text())
    assert metadata['modification_status'] == 'modified'


def test_update_merge_conflict(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update leaves conflicts in working tree for manual resolution."""
    workdir = imported_workdir('chocolate')

    # Make local modification: change License line
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = chocolate_spec.read_text()
    modified_spec = spec_content.replace('License: GPL', 'License: MIT')
    chocolate_spec.write_text(modified_spec)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Local license change', 'chocolate'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    # Make conflicting upstream change: change the same License line differently and bump version
    upstream_spec = upstream_repos['chocolate'] / 'chocolate.spec'
    upstream_content = upstream_spec.read_text()
    updated_upstream = upstream_content.replace('License: GPL', 'License: Apache-2.0')
    updated_upstream = updated_upstream.replace('Version: 10', 'Version: 11')
    upstream_spec.write_text(updated_upstream)
    subprocess.run(['git', 'commit', '-a', '-m', 'Change license to Apache'],
                   cwd=upstream_repos['chocolate'], check=True)

    # update exits with code 2 (conflicts)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'chocolate'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 2, f"Expected exit code 2 for conflicts, got {result.returncode}"
    assert 'conflict' in result.stderr.lower(), "Expected 'conflict' in stderr"
    assert 'update --continue' in result.stderr, "Expected --continue instructions"

    # Verify state file was written for --continue
    state_file = workdir / '.dist_git_update_state.json'
    assert state_file.exists(), "Expected state file for --continue"
    with open(state_file) as f:
        state = json.load(f)
    assert state['package'] == 'chocolate'
    assert 'Update chocolate' in state['commit_msg']
    assert 'metadata' in state, "State file should contain updated metadata for --continue"
    assert state['metadata']['version'] == '11'

    # Verify NO new commit was created (conflicts left in working tree)
    commits_after = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD'],
        cwd=workdir, capture_output=True, text=True, check=True
    ).stdout.strip()
    # Initial commit, Import, Local modification — no Update commit
    assert commits_after == "3"

    # Verify metadata was NOT staged (HUM-2672: prevents leak into subsequent commits)
    metadata_status = subprocess.run(
        ['git', 'diff', '--cached', '--name-only'],
        cwd=workdir, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert 'metadata/chocolate.json' not in metadata_status

    # Verify metadata on disk was not modified (HUM-2672: never written on conflict path)
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        disk_metadata = json.load(f)
    assert disk_metadata['version'] == '10', "Metadata on disk should remain at pre-update version"

    # Verify spec file has conflict markers in working tree
    spec_content = chocolate_spec.read_text()
    assert '<<<<<<< HEAD' in spec_content
    assert '>>>>>>> hummingbird-local' in spec_content
    assert 'License: MIT' in spec_content
    assert 'License: Apache-2.0' in spec_content


def test_multi_mr_commits_conflicted_update_for_dry_run_mr(
    imported_workdir, upstream_repos: dict[str, Path]
) -> None:
    """Multi-MR wrapper turns dist_git exit 2 into a conflict MR candidate."""
    workdir = imported_workdir('chocolate')

    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    chocolate_spec.write_text(chocolate_spec.read_text().replace('License: GPL', 'License: MIT'))
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Local license change', 'chocolate'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    upstream_spec = upstream_repos['chocolate'] / 'chocolate.spec'
    updated_upstream = upstream_spec.read_text().replace('License: GPL', 'License: Apache-2.0')
    updated_upstream = updated_upstream.replace('Version: 10', 'Version: 11')
    upstream_spec.write_text(updated_upstream)
    subprocess.run(['git', 'commit', '-a', '-m', 'Change license to Apache'],
                   cwd=upstream_repos['chocolate'], check=True)

    branch = subprocess.run(
        ['git', 'branch', '--show-current'],
        cwd=workdir, capture_output=True, text=True, check=True
    ).stdout.strip()
    env = os.environ.copy()
    env['CI_COMMIT_BRANCH'] = branch
    env['GITLAB_REMOTE_URL'] = f'file://{workdir}'
    env['DIST_GIT_UPDATE_SKIP_BUILD_CHECK'] = 'true'

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git_update_multi_mr.sh'),
         '--clone', '--only-package=chocolate'],
        cwd=workdir, capture_output=True, text=True, env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Update found (CONFLICTS - needs manual resolution)' in result.stdout
    assert 'Type: CONFLICT (no auto-merge)' in result.stdout
    assert 'Updates failed:          0' in result.stdout


def test_final_spec_version_error_handles_subprocess_failure(imported_workdir, dist_git_module) -> None:
    """A resolver subprocess failure must produce a useful error, not a traceback."""
    workdir = imported_workdir('vanilla')
    metadata = dist_git_module.HumPackageMetadata._from_raw_dict(
        'vanilla', json.loads((workdir / 'metadata' / 'vanilla.json').read_text()),
    )
    failure = subprocess.CalledProcessError(1, ['rpmspec', '-q', 'vanilla.spec'])
    with patch.object(dist_git_module, 'resolve_spec_version', side_effect=failure):
        error = dist_git_module._final_spec_version_error(metadata, workdir / 'rpms' / 'vanilla')
    assert error is not None
    assert 'Cannot validate final spec version for vanilla' in error
    assert 'rpmspec' in error


@pytest.mark.parametrize('version_form', ['literal', 'macro'])
@pytest.mark.parametrize('mismatch', [False, True], ids=['matching', 'mismatched'])
@pytest.mark.parametrize('dry_run', [False, True], ids=['commit', 'dry-run'])
def test_update_validates_final_spec_version(
    imported_workdir, upstream_repos: dict[str, Path], version_form: str, mismatch: bool, dry_run: bool,
) -> None:
    """A clean merge must not commit metadata that disagrees with the final spec."""
    workdir = imported_workdir('vanilla')
    spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    final_version = '1.5' if mismatch else '1.0'
    content = spec.read_text()
    if version_form == 'macro':
        content = f'%global package_version {final_version}\n' + content.replace(
            'Version: 1.0', 'Version: %{package_version}',
        )
    else:
        content = content.replace('Version: 1.0', f'Version: {final_version}')
    spec.write_text('# Local packaging change\n' + content)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Local packaging change'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-am', 'Local packaging change'], cwd=workdir, check=True)
    upstream = upstream_repos['vanilla']
    upstream_spec = upstream / 'vanilla.spec'
    upstream_spec.write_text(upstream_spec.read_text() + '\n# Upstream packaging change\n')
    subprocess.run(['git', 'commit', '-am', 'Upstream packaging change'], cwd=upstream, check=True)

    def git_output(*args: str) -> str:
        return subprocess.run(['git', *args], cwd=workdir, check=True, capture_output=True, text=True).stdout

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    metadata_before = metadata_file.read_bytes()
    head_before = git_output('rev-parse', 'HEAD')
    index_before = git_output('diff', '--cached')
    args = ['--dry-run'] if dry_run else []
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), *args, 'update', 'vanilla', '--skip-build-check'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert '# Local packaging change' in spec.read_text()
    assert '# Upstream packaging change' in spec.read_text()
    assert '<<<<<<<' not in spec.read_text(), 'This regression must exercise a conflict-free merge'
    state_file = workdir / '.dist_git_update_state.json'
    if mismatch:
        assert result.returncode != 0, 'Committed a mismatched final spec and metadata'
        assert '1.5' in result.stderr and '1.0' in result.stderr
        assert 'Version:' in result.stderr and 'metadata version' in result.stderr
        assert metadata_file.read_bytes() == metadata_before
        assert git_output('rev-parse', 'HEAD') == head_before
        assert git_output('diff', '--cached') == index_before
        assert '1.5' in spec.read_text(), 'Must not silently rewrite the resolved spec'
        if not dry_run:
            assert result.returncode == 1, 'Do not let the batch caller commit this as an unresolved merge'
            assert state_file.exists(), 'Preserve the pending update for correction and continuation'
            assert json.loads(state_file.read_text())['metadata']['version'] == '1.0'
            spec.write_text(spec.read_text().replace('1.5', '1.0'))
            resumed = subprocess.run(
                [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
                cwd=workdir, capture_output=True, text=True,
            )
            assert resumed.returncode == 0, resumed.stderr
            assert not state_file.exists()
            assert git_output('rev-parse', 'HEAD') != head_before
        else:
            assert not state_file.exists(), 'dry-run must not write recovery state on version mismatch'
    else:
        assert result.returncode == 0, result.stderr
        assert not state_file.exists()
        assert json.loads(metadata_file.read_text())['version'] == '1.0'
        if dry_run:
            assert metadata_file.read_bytes() == metadata_before
            assert git_output('rev-parse', 'HEAD') == head_before
        else:
            assert git_output('rev-parse', 'HEAD') != head_before


def test_update_merge_version_bump(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update handles modified packages where the local change is a Version bump.

    When the local modification changes Version (adjacent to Release in the spec),
    the Release normalization rebase can fail due to diff hunk context overlap.
    The merge should fall back to applying raw local modifications.
    """
    workdir = imported_workdir('vanilla')

    # Make local modification: bump Version (adjacent to Release — triggers rebase conflict)
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_spec = spec_content.replace('Version: 1.0', 'Version: 1.5')
    modified_spec = modified_spec.replace('Release: 1', 'Release: 0.1')
    vanilla_spec.write_text(modified_spec)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Version bump to 1.5', 'vanilla'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    # Make upstream change: different Version bump + add a comment
    upstream_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    upstream_content = upstream_spec.read_text()
    updated_upstream = upstream_content.replace('Version: 1.0', 'Version: 2.0')
    updated_upstream = updated_upstream.replace('Release: 1', 'Release: 2')
    updated_upstream = updated_upstream.replace('%files', '# Upstream comment\n%files')
    upstream_spec.write_text(updated_upstream)
    subprocess.run(['git', 'commit', '-a', '-m', 'Upstream version bump'],
                   cwd=upstream_repos['vanilla'], check=True)

    # update should succeed (with conflicts on Version, but not crash)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode in (0, 2), f"Update crashed: {result.stderr}"

    # Verify upstream content was merged into the result
    merged_spec = vanilla_spec.read_text()
    assert '# Upstream comment' in merged_spec, "Upstream change should be merged in"
    assert 'Version: 2.0' in merged_spec or '<<<<<<< HEAD' in merged_spec, \
        "Upstream version or conflict markers should be present"


#
# Tests -- gorget re-invocation after a dist-git merge (HUM-4621)
#

def _write_pipeline(path: Path, fetch_steps: list[dict]) -> None:
    path.write_text(yaml.dump({'fetch': fetch_steps}, default_flow_style=False, sort_keys=False))


def test_fixed_pipeline_git_refs_filters_version_templated(dist_git_module) -> None:
    pipeline = {
        'fetch': [
            {'type': 'git', 'repo': '${UPSTREAM_REPO}', 'ref': 'v${VERSION}'},
            {'type': 'git', 'repo': '${UPSTREAM_REPO}',
             'ref': 'jdk-${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}+${VERSION_BUILD}'},
            {'type': 'git', 'repo': '${UPSTREAM_REPO}', 'ref': 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef'},
            {'type': 'url', 'url': 'https://example.com/x-${VERSION}.tar.gz'},
        ],
    }
    assert dist_git_module._fixed_pipeline_git_refs(pipeline) == [
        'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef',
    ]


def test_fixed_pipeline_git_refs_empty_when_no_fetch(dist_git_module) -> None:
    assert dist_git_module._fixed_pipeline_git_refs({}) == []


def test_spec_has_custom_setup_override_true_for_non_default_n(dist_git_module) -> None:
    spec_text = "%prep\n%setup -n %{glibcsrcdir}\n"
    assert dist_git_module._spec_has_custom_setup_override(spec_text) is True


def test_spec_has_custom_setup_override_false_for_default_n(dist_git_module) -> None:
    spec_text = "%prep\n%autosetup -n %{name}-%{version}\n"
    assert dist_git_module._spec_has_custom_setup_override(spec_text) is False


def test_spec_has_custom_setup_override_false_for_no_setup(dist_git_module) -> None:
    assert dist_git_module._spec_has_custom_setup_override("%prep\n") is False


def test_fixed_ref_pipeline_drift_detects_stale_ref(tmp_path: Path, dist_git_module) -> None:
    """A fixed ref whose short SHA no longer appears in the spec, with a
    custom -n override present, is reported as drifted."""
    package_dir = tmp_path / 'gcc'
    package_dir.mkdir()
    (package_dir / 'gcc.spec').write_text(
        "%prep\n%setup -q -c -n %{uniquesuffix}\n%global gitrev deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
    )
    pipeline = {'fetch': [{'type': 'git', 'repo': '${UPSTREAM_REPO}',
                           'ref': '0000000000000000000000000000000000000000'}]}
    drifted = dist_git_module._fixed_ref_pipeline_drift(package_dir, pipeline)
    assert drifted == ['0000000000000000000000000000000000000000']


def test_fixed_ref_pipeline_drift_no_drift_when_ref_matches(tmp_path: Path, dist_git_module) -> None:
    package_dir = tmp_path / 'gcc'
    package_dir.mkdir()
    (package_dir / 'gcc.spec').write_text(
        "%prep\n%setup -q -c -n %{uniquesuffix}\n%global gitrev deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
    )
    pipeline = {'fetch': [{'type': 'git', 'repo': '${UPSTREAM_REPO}',
                           'ref': 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef'}]}
    assert dist_git_module._fixed_ref_pipeline_drift(package_dir, pipeline) == []


def test_fixed_ref_pipeline_drift_skips_without_custom_override(tmp_path: Path, dist_git_module) -> None:
    """No custom -n override -- default naming carries no identifier that
    could drift, so no false-positive drift is reported even though the ref
    doesn't appear anywhere in the spec."""
    package_dir = tmp_path / 'somepkg'
    package_dir.mkdir()
    (package_dir / 'somepkg.spec').write_text("%prep\n%autosetup\n")
    pipeline = {'fetch': [{'type': 'git', 'repo': '${UPSTREAM_REPO}',
                           'ref': '0000000000000000000000000000000000000000'}]}
    assert dist_git_module._fixed_ref_pipeline_drift(package_dir, pipeline) == []


def test_fixed_ref_pipeline_drift_ignores_changelog_only_match(tmp_path: Path, dist_git_module) -> None:
    """A stale ref's short SHA appearing only in %changelog (history) doesn't
    count as still-referenced."""
    package_dir = tmp_path / 'gcc'
    package_dir.mkdir()
    (package_dir / 'gcc.spec').write_text(
        "%prep\n%setup -q -c -n %{uniquesuffix}\n%global gitrev deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
        "%changelog\n* Mon Jan 01 2024 Test <test@example.com>\n- mentions 0000000 here\n"
    )
    pipeline = {'fetch': [{'type': 'git', 'repo': '${UPSTREAM_REPO}',
                           'ref': '0000000000000000000000000000000000000000'}]}
    drifted = dist_git_module._fixed_ref_pipeline_drift(package_dir, pipeline)
    assert drifted == ['0000000000000000000000000000000000000000']


def _fake_pin_refresh_git(commit_date: str):
    """Fake subprocess.run for _refresh_fixed_ref_pin's git init/fetch/log calls."""
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ['git', 'log']:
            return subprocess.CompletedProcess(cmd, 0, stdout=commit_date + "\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return fake_run


_GCC_VERSION_FROM_REF = types.SimpleNamespace(type='commit-date')
_GCC_UPSTREAM_REPO = 'https://gcc.gnu.org/git/gcc.git'


def test_refresh_fixed_ref_pin_success(tmp_path: Path, dist_git_module) -> None:
    package_dir = tmp_path / 'gcc'
    package_dir.mkdir()
    (package_dir / 'gcc.spec').write_text(
        "%global gitrev 1111111111111111111111111111111111111111\n"
    )
    pipeline_file = tmp_path / 'gcc.source-pipeline.yaml'
    pipeline_file.write_text(
        '# a carefully-written comment that must survive\n'
        'fetch:\n'
        '  - type: git\n'
        '    repo: "${UPSTREAM_REPO}"\n'
        '    ref: "0000000000000000000000000000000000000000"\n'
        '    archive_name: "gcc-${VERSION}.tar.xz"\n'
    )
    pipeline = yaml.safe_load(pipeline_file.read_text())

    with patch.object(dist_git_module.subprocess, 'run',
                       side_effect=_fake_pin_refresh_git('20260819')):
        result = dist_git_module._refresh_fixed_ref_pin(
            'gcc', package_dir, pipeline_file, pipeline,
            _GCC_UPSTREAM_REPO, _GCC_VERSION_FROM_REF, '16.2.1',
        )

    assert result is not None
    version, tmp_pipeline_file = result
    assert version == '16.2.1-20260819'

    # The real pipeline_file must be untouched -- only the returned temp
    # copy has the ref rewrite, until a caller confirms gorget actually
    # succeeded against it (see _run_gorget_for_updated_package).
    assert '0000000000000000000000000000000000000000' in pipeline_file.read_text()

    updated_text = tmp_pipeline_file.read_text()
    assert 'ref: "1111111111111111111111111111111111111111"' in updated_text
    assert '0000000000000000000000000000000000000000' not in updated_text
    # Comment and unrelated keys must survive a targeted text substitution,
    # not a yaml.safe_load/dump round-trip (which would strip comments).
    assert 'a carefully-written comment that must survive' in updated_text
    assert 'archive_name: "gcc-${VERSION}.tar.xz"' in updated_text
    tmp_pipeline_file.unlink()


def test_refresh_fixed_ref_pin_unsupported_type_returns_none(tmp_path: Path, dist_git_module) -> None:
    package_dir = tmp_path / 'glibc'
    package_dir.mkdir()
    pipeline_file = tmp_path / 'glibc.source-pipeline.yaml'
    _write_pipeline(pipeline_file, [{'type': 'git', 'repo': '${UPSTREAM_REPO}',
                                     'ref': '0000000000000000000000000000000000000000'}])
    pipeline = yaml.safe_load(pipeline_file.read_text())

    result = dist_git_module._refresh_fixed_ref_pin(
        'glibc', package_dir, pipeline_file, pipeline,
        'https://sourceware.org/git/glibc.git', types.SimpleNamespace(type='git-describe'),
        '2.43',
    )
    assert result is None


def test_refresh_fixed_ref_pin_missing_upstream_repo_returns_none(tmp_path: Path, dist_git_module) -> None:
    package_dir = tmp_path / 'gcc'
    package_dir.mkdir()
    pipeline_file = tmp_path / 'gcc.source-pipeline.yaml'
    _write_pipeline(pipeline_file, [{'type': 'git', 'repo': '${UPSTREAM_REPO}',
                                     'ref': '0000000000000000000000000000000000000000'}])
    pipeline = yaml.safe_load(pipeline_file.read_text())

    result = dist_git_module._refresh_fixed_ref_pin(
        'gcc', package_dir, pipeline_file, pipeline, None, _GCC_VERSION_FROM_REF, '16.2.1',
    )
    assert result is None


def test_refresh_fixed_ref_pin_no_gitrev_in_spec(tmp_path: Path, dist_git_module) -> None:
    package_dir = tmp_path / 'gcc'
    package_dir.mkdir()
    (package_dir / 'gcc.spec').write_text("Name: gcc\n")
    pipeline_file = tmp_path / 'gcc.source-pipeline.yaml'
    _write_pipeline(pipeline_file, [{'type': 'git', 'repo': '${UPSTREAM_REPO}',
                                     'ref': '0000000000000000000000000000000000000000'}])
    pipeline = yaml.safe_load(pipeline_file.read_text())

    result = dist_git_module._refresh_fixed_ref_pin(
        'gcc', package_dir, pipeline_file, pipeline, _GCC_UPSTREAM_REPO,
        _GCC_VERSION_FROM_REF, '16.2.1',
    )
    assert result is None


def test_refresh_fixed_ref_pin_network_failure_returns_none(tmp_path: Path, dist_git_module) -> None:
    package_dir = tmp_path / 'gcc'
    package_dir.mkdir()
    (package_dir / 'gcc.spec').write_text(
        "%global gitrev 1111111111111111111111111111111111111111\n"
    )
    pipeline_file = tmp_path / 'gcc.source-pipeline.yaml'
    _write_pipeline(pipeline_file, [{'type': 'git', 'repo': '${UPSTREAM_REPO}',
                                     'ref': '0000000000000000000000000000000000000000'}])
    pipeline = yaml.safe_load(pipeline_file.read_text())

    def raise_fetch_error(cmd, **kwargs):
        if cmd[:2] == ['git', 'fetch']:
            raise subprocess.CalledProcessError(128, cmd, stderr="429")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch.object(dist_git_module.subprocess, 'run', side_effect=raise_fetch_error):
        result = dist_git_module._refresh_fixed_ref_pin(
            'gcc', package_dir, pipeline_file, pipeline,
            _GCC_UPSTREAM_REPO, _GCC_VERSION_FROM_REF, '16.2.1',
        )
    assert result is None
    # Pipeline file must be untouched on failure.
    assert '0000000000000000000000000000000000000000' in pipeline_file.read_text()


def test_refresh_fixed_ref_pin_no_op_when_ref_unchanged(tmp_path: Path, dist_git_module) -> None:
    """The spec's gitrev already matches the pipeline's ref -- nothing to refresh,
    and no network calls should be made."""
    package_dir = tmp_path / 'gcc'
    package_dir.mkdir()
    (package_dir / 'gcc.spec').write_text(
        "%global gitrev 0000000000000000000000000000000000000000\n"
    )
    pipeline_file = tmp_path / 'gcc.source-pipeline.yaml'
    _write_pipeline(pipeline_file, [{'type': 'git', 'repo': '${UPSTREAM_REPO}',
                                     'ref': '0000000000000000000000000000000000000000'}])
    pipeline = yaml.safe_load(pipeline_file.read_text())

    with patch.object(dist_git_module.subprocess, 'run') as mock_run:
        result = dist_git_module._refresh_fixed_ref_pin(
            'gcc', package_dir, pipeline_file, pipeline,
            _GCC_UPSTREAM_REPO, _GCC_VERSION_FROM_REF, '16.2.1',
        )
    assert result is None
    mock_run.assert_not_called()


def test_run_gorget_for_updated_package_no_pipeline_is_noop(tmp_path: Path, dist_git_module) -> None:
    with patch.object(dist_git_module.cuv, '_load_source_pipeline', return_value=None), \
         patch.object(dist_git_module.cuv, '_run_gorget_pipeline') as mock_gorget:
        reason, uploaded = dist_git_module._run_gorget_for_updated_package(
            'somepkg', tmp_path, '1.0', '2.0', {},
        )
    assert reason is None
    assert uploaded == []
    mock_gorget.assert_not_called()


def test_run_gorget_for_updated_package_version_templated_calls_gorget(
        tmp_path: Path, dist_git_module) -> None:
    package_dir = tmp_path / 'somepkg'
    package_dir.mkdir()
    (package_dir / 'somepkg.spec').write_text("%prep\n%autosetup\n")
    pipeline_file = tmp_path / 'somepkg.source-pipeline.yaml'
    _write_pipeline(pipeline_file, [{'type': 'url', 'url': 'https://example.com/x-${VERSION}.tar.gz'}])

    with patch.object(dist_git_module.cuv, '_load_source_pipeline', return_value=pipeline_file), \
         patch.object(dist_git_module.cuv, '_run_gorget_pipeline',
                       return_value=['x-2.0.tar.gz']) as mock_gorget:
        reason, uploaded = dist_git_module._run_gorget_for_updated_package(
            'somepkg', package_dir, '1.0', '2.0', {},
        )
    assert reason is None
    assert uploaded == ['x-2.0.tar.gz']
    mock_gorget.assert_called_once_with('somepkg', '1.0', '2.0', pipeline_file)


def test_run_gorget_for_updated_package_version_unchanged_skips_gorget(
        tmp_path: Path, dist_git_module) -> None:
    package_dir = tmp_path / 'somepkg'
    package_dir.mkdir()
    (package_dir / 'somepkg.spec').write_text("%prep\n%autosetup\n")
    pipeline_file = tmp_path / 'somepkg.source-pipeline.yaml'
    _write_pipeline(pipeline_file, [{'type': 'url', 'url': 'https://example.com/x-${VERSION}.tar.gz'}])

    with patch.object(dist_git_module.cuv, '_load_source_pipeline', return_value=pipeline_file), \
         patch.object(dist_git_module.cuv, '_run_gorget_pipeline') as mock_gorget:
        reason, uploaded = dist_git_module._run_gorget_for_updated_package(
            'somepkg', package_dir, '1.0', '1.0', {},
        )
    assert reason is None
    assert uploaded == []
    mock_gorget.assert_not_called()


def test_run_gorget_for_updated_package_gorget_failure_returns_reason(
        tmp_path: Path, dist_git_module) -> None:
    package_dir = tmp_path / 'somepkg'
    package_dir.mkdir()
    (package_dir / 'somepkg.spec').write_text("%prep\n%autosetup\n")
    pipeline_file = tmp_path / 'somepkg.source-pipeline.yaml'
    _write_pipeline(pipeline_file, [{'type': 'url', 'url': 'https://example.com/x-${VERSION}.tar.gz'}])

    with patch.object(dist_git_module.cuv, '_load_source_pipeline', return_value=pipeline_file), \
         patch.object(dist_git_module.cuv, '_run_gorget_pipeline',
                       side_effect=RuntimeError('boom')):
        reason, uploaded = dist_git_module._run_gorget_for_updated_package(
            'somepkg', package_dir, '1.0', '2.0', {},
        )
    assert reason is not None
    assert 'boom' in reason
    assert uploaded == []


def test_run_gorget_for_updated_package_fixed_ref_drift_gcc_auto_fix(
        tmp_path: Path, dist_git_module) -> None:
    package_dir = tmp_path / 'gcc'
    package_dir.mkdir()
    (package_dir / 'gcc.spec').write_text(
        "%prep\n%setup -q -c -n %{uniquesuffix}\n%global gitrev 1111111111111111111111111111111111111111\n"
    )
    # Written with literal double-quoted refs, matching real
    # metadata/*.source-pipeline.yaml files' convention -- yaml.dump's
    # default single-quoting would silently defeat _refresh_fixed_ref_pin's
    # targeted text substitution.
    pipeline_file = tmp_path / 'gcc.source-pipeline.yaml'
    pipeline_file.write_text(
        'fetch:\n'
        '  - type: git\n'
        '    repo: "${UPSTREAM_REPO}"\n'
        '    ref: "0000000000000000000000000000000000000000"\n'
        '    archive_name: "gcc-${VERSION}.tar.xz"\n'
    )
    metadata = types.SimpleNamespace(version_from_ref=_GCC_VERSION_FROM_REF, upstream_repo=_GCC_UPSTREAM_REPO)

    with patch.object(dist_git_module.cuv, '_load_source_pipeline', return_value=pipeline_file), \
         patch.object(dist_git_module.cuv, '_run_gorget_pipeline',
                       return_value=['gcc-16.2.1-20260819.tar.xz']) as mock_gorget, \
         patch.object(dist_git_module.subprocess, 'run', side_effect=_fake_pin_refresh_git('20260819')):
        reason, uploaded = dist_git_module._run_gorget_for_updated_package(
            'gcc', package_dir, '16.2.1', '16.2.1', metadata,
        )

    assert reason is None
    assert uploaded == ['gcc-16.2.1-20260819.tar.xz']
    # gorget was called against a staged temp copy, not the real file directly.
    mock_gorget.assert_called_once()
    call_args = mock_gorget.call_args[0]
    assert call_args[:3] == ('gcc', '16.2.1', '16.2.1-20260819')
    assert call_args[3] != pipeline_file
    # Only after gorget succeeded does the real pipeline file get the rewrite.
    assert '1111111111111111111111111111111111111111' in pipeline_file.read_text()
    # The temp file is cleaned up afterward.
    assert not call_args[3].exists()


def test_run_gorget_for_updated_package_fixed_ref_retry_after_gorget_failure_still_retries(
        tmp_path: Path, dist_git_module) -> None:
    """Regression test: a gorget failure must NOT leave the real pipeline
    file looking "already fixed" -- otherwise a retry sees no more drift,
    silently skips re-running gorget, and the caller ends up committing
    whatever was already in `sources` unverified (exactly what happened on
    a real gcc update despite this whole safety net)."""
    package_dir = tmp_path / 'gcc'
    package_dir.mkdir()
    (package_dir / 'gcc.spec').write_text(
        "%prep\n%setup -q -c -n %{uniquesuffix}\n%global gitrev 1111111111111111111111111111111111111111\n"
    )
    pipeline_file = tmp_path / 'gcc.source-pipeline.yaml'
    pipeline_file.write_text(
        'fetch:\n'
        '  - type: git\n'
        '    repo: "${UPSTREAM_REPO}"\n'
        '    ref: "0000000000000000000000000000000000000000"\n'
        '    archive_name: "gcc-${VERSION}.tar.xz"\n'
    )
    metadata = types.SimpleNamespace(version_from_ref=_GCC_VERSION_FROM_REF, upstream_repo=_GCC_UPSTREAM_REPO)

    # First attempt: gorget fails (e.g. the binary is missing).
    with patch.object(dist_git_module.cuv, '_load_source_pipeline', return_value=pipeline_file), \
         patch.object(dist_git_module.cuv, '_run_gorget_pipeline',
                       side_effect=FileNotFoundError("No such file or directory: 'gorget'")), \
         patch.object(dist_git_module.subprocess, 'run', side_effect=_fake_pin_refresh_git('20260819')):
        first_reason, first_uploaded = dist_git_module._run_gorget_for_updated_package(
            'gcc', package_dir, '16.2.1', '16.2.1', metadata,
        )
    assert first_reason is not None
    assert first_uploaded == []
    # The real pipeline file must be untouched by the failed attempt.
    assert '0000000000000000000000000000000000000000' in pipeline_file.read_text()

    # Second attempt (retry): must detect the SAME drift again and actually
    # invoke gorget -- not silently short-circuit as "nothing to do".
    with patch.object(dist_git_module.cuv, '_load_source_pipeline', return_value=pipeline_file), \
         patch.object(dist_git_module.cuv, '_run_gorget_pipeline',
                       return_value=['gcc-16.2.1-20260819.tar.xz']) as mock_gorget, \
         patch.object(dist_git_module.subprocess, 'run', side_effect=_fake_pin_refresh_git('20260819')):
        second_reason, second_uploaded = dist_git_module._run_gorget_for_updated_package(
            'gcc', package_dir, '16.2.1', '16.2.1', metadata,
        )
    assert second_reason is None
    assert second_uploaded == ['gcc-16.2.1-20260819.tar.xz']
    mock_gorget.assert_called_once()
    assert '1111111111111111111111111111111111111111' in pipeline_file.read_text()


def test_run_gorget_for_updated_package_fixed_ref_drift_no_version_from_ref_needs_manual_run(
        tmp_path: Path, dist_git_module) -> None:
    """glibc (and the other packages with no version_from_ref configured) don't
    get automatic pin resolution -- drift must fail loudly, not silently proceed."""
    package_dir = tmp_path / 'glibc'
    package_dir.mkdir()
    (package_dir / 'glibc.spec').write_text(
        "%prep\n%autosetup -n %{glibcsrcdir}\n%global glibcsrcdir glibc-2.43-48-gaaaaaaaaaa\n"
    )
    pipeline_file = tmp_path / 'glibc.source-pipeline.yaml'
    _write_pipeline(pipeline_file, [{'type': 'git', 'repo': '${UPSTREAM_REPO}',
                                     'ref': 'bc95068f5f9d7f57d0f01757fed0900893b122b8'}])

    with patch.object(dist_git_module.cuv, '_load_source_pipeline', return_value=pipeline_file), \
         patch.object(dist_git_module.cuv, '_run_gorget_pipeline') as mock_gorget:
        reason, uploaded = dist_git_module._run_gorget_for_updated_package(
            'glibc', package_dir, '2.43', '2.43',
            types.SimpleNamespace(version_from_ref=None, upstream_repo='https://sourceware.org/git/glibc.git'),
        )

    assert reason is not None
    assert 'glibc' in reason
    assert 'manually' in reason
    assert uploaded == []
    mock_gorget.assert_not_called()


def test_stage_package_for_commit_excludes_lookaside_sources(
        workdir: Path, dist_git_module) -> None:
    """Regression test for HUM-6388: a gorget-fetched (or download_new_sources
    -fetched) source archive left in the package directory for upload to the
    lookaside cache must never be committed, even though staging still needs
    'git add -f' to cope with upstream dist-gits that gitignore their own
    patches (see 66dc563797b7).

    Uses the real project .gitignore (copied into workdir by the fixture),
    which is what actually causes this: 'add -f' would otherwise bypass its
    rpms/*/*.tar.xz etc. exclusions and let a multi-MiB tarball get committed,
    tripping GitLab's per-blob push-size limit on every push."""
    dist_git_module.ROOT_DIR = workdir
    dist_git_module.RPMS_DIR = workdir / 'rpms'
    dist_git_module.METADATA_DIR = workdir / 'metadata'

    package_dir = workdir / 'rpms' / 'somepkg'
    package_dir.mkdir()
    (package_dir / 'somepkg.spec').write_text("Name: somepkg\n")
    (package_dir / 'somepkg-2.0.tar.xz').write_bytes(b'\x00' * 1024)
    (workdir / 'metadata' / 'somepkg.json').write_text('{}\n')

    dist_git_module._stage_package_for_commit(
        'somepkg', package_dir, ('metadata/somepkg.json',), ['somepkg-2.0.tar.xz'],
    )

    staged = subprocess.run(
        ['git', 'diff', '--cached', '--name-only'],
        cwd=workdir, capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    assert 'rpms/somepkg/somepkg.spec' in staged
    assert 'metadata/somepkg.json' in staged
    assert 'rpms/somepkg/somepkg-2.0.tar.xz' not in staged
    # The tarball must still exist on disk (e.g. for local inspection) --
    # only its git tracking is excluded.
    assert (package_dir / 'somepkg-2.0.tar.xz').exists()


def test_stage_package_for_commit_still_force_adds_patches(
        workdir: Path, dist_git_module) -> None:
    """The 'findutils' case (66dc563797b7) must keep working: patch/spec
    files that an upstream dist-git's own .gitignore hides must still be
    force-added when they aren't in new_source_filenames."""
    dist_git_module.ROOT_DIR = workdir
    dist_git_module.RPMS_DIR = workdir / 'rpms'
    dist_git_module.METADATA_DIR = workdir / 'metadata'

    package_dir = workdir / 'rpms' / 'findutils'
    package_dir.mkdir()
    (package_dir / '.gitignore').write_text('*.patch\n')
    (package_dir / 'own-patch.patch').write_text('diff --git a/x b/x\n')
    (workdir / 'metadata' / 'findutils.json').write_text('{}\n')

    dist_git_module._stage_package_for_commit(
        'findutils', package_dir, ('metadata/findutils.json',), [],
    )

    staged = subprocess.run(
        ['git', 'diff', '--cached', '--name-only'],
        cwd=workdir, capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    assert 'rpms/findutils/own-patch.patch' in staged


def test_update_no_pipeline_file_unaffected(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Regression check: a package with no source-pipeline.yaml behaves
    exactly as before -- update() must not even look for gorget."""
    workdir = imported_workdir('vanilla')
    upstream_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    upstream_spec.write_text(upstream_spec.read_text().replace('Version: 1.0', 'Version: 2.0')
                              .replace('Release: 1', 'Release: 1'))
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump version'],
                   cwd=upstream_repos['vanilla'], check=True)

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Update failed: {result.stderr}"
    subject, _ = get_last_commit_info(workdir)
    assert subject.startswith('Update vanilla from')
    assert 'NEEDS MANUAL GORGET RUN' not in subject


def test_update_version_templated_pipeline_failure_needs_manual_run(
        imported_workdir, upstream_repos: dict[str, Path], tmp_path: Path) -> None:
    """End-to-end (real subprocess): a package with a source-pipeline.yaml
    whose gorget run fails must not silently commit -- it needs to fall back
    to the same conflict-style, exit(2), needs-manual-resolution path, with a
    commit message dist_git_update_multi_mr.sh can recognize.

    Uses a fake `gorget` on PATH that always fails, rather than mocking
    Python internals -- update() runs as a real subprocess here, same as
    every other update() test in this file, so there's nothing to patch."""
    workdir = imported_workdir('vanilla')
    pipeline_file = workdir / 'metadata' / 'vanilla.source-pipeline.yaml'
    _write_pipeline(pipeline_file, [{'type': 'url', 'url': 'https://example.com/vanilla-${VERSION}.tar.gz'}])
    subprocess.run(['git', 'add', '-A'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add source-pipeline.yaml'], cwd=workdir, check=True)

    upstream_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    upstream_spec.write_text(upstream_spec.read_text().replace('Version: 1.0', 'Version: 2.0'))
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump version'],
                   cwd=upstream_repos['vanilla'], check=True)

    fake_bin = tmp_path / 'fake-bin'
    fake_bin.mkdir()
    fake_gorget = fake_bin / 'gorget'
    fake_gorget.write_text("#!/bin/sh\necho 'simulated upstream fetch failure' >&2\nexit 1\n")
    fake_gorget.chmod(0o755)

    commits_before = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD'],
        cwd=workdir, capture_output=True, text=True, check=True,
    ).stdout.strip()

    env = {**os.environ, 'PATH': f"{fake_bin}:{os.environ['PATH']}"}
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, env=env,
    )

    assert result.returncode == 2, f"Expected exit code 2, got {result.returncode}: {result.stderr}"
    state_file = workdir / '.dist_git_update_state.json'
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert state['commit_msg'].startswith('NEEDS MANUAL GORGET RUN:')
    assert 'gorget pipeline failed' in state['commit_msg']

    # No commit was created -- same guarantee as a real git merge conflict.
    commits_after = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD'],
        cwd=workdir, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert commits_after == commits_before, "No commit should have been created"


def test_update_skips_package_without_upstream_metadata(workdir: Path) -> None:
    """Independent packages (no Fedora origin) are blocked from update."""
    package_dir = workdir / 'rpms' / 'signed-stub'
    package_dir.mkdir()
    (package_dir / 'signed-stub.spec').write_text("""Name: signed-stub
Version: 1
Release: 1
Summary: Signed stub
License: MIT

%description
Signed stub

%files
""")
    (workdir / 'metadata' / 'signed-stub.json').write_text(json.dumps({
        'version': '1',
        'modification_status': 'independent',
        'upstream_repo': 'https://src.fedoraproject.org/rpms/signed-stub',
    }, indent=2) + '\n')
    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add signed stub'], cwd=workdir, check=True)

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', 'signed-stub'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert 'Cannot update independent package signed-stub' in result.stderr


def test_update_skips_upstream_without_spec(
    imported_workdir, upstream_repos: dict[str, Path]
) -> None:
    """Retired/no-spec upstream branches are skipped instead of failing all updates."""
    workdir = imported_workdir('vanilla')

    vanilla_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    vanilla_spec.unlink()
    (upstream_repos['vanilla'] / 'dead.package').write_text('Retired package\n')
    subprocess.run(['git', 'add', '-A'], cwd=upstream_repos['vanilla'], check=True)
    subprocess.run(['git', 'commit', '-m', 'Retire package'], cwd=upstream_repos['vanilla'], check=True)

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode == 0
    assert 'Skipping vanilla: upstream branch rawhide is retired' in result.stderr
    metadata = json.loads((workdir / 'metadata' / 'vanilla.json').read_text())
    assert metadata['version'] == '1.0'


def test_independent_package_blocks_update(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Verify that independent packages block automatic updates."""
    # Create an independent package manually
    independent_dir = workdir / 'rpms' / 'independent-pkg'
    independent_dir.mkdir()
    (independent_dir / 'independent-pkg.spec').write_text("""Name: independent-pkg
Version: 1.0
Release: 1
Summary: Independent package
License: MIT

%description
Independent package

%files
""")

    # Create metadata for independent package
    metadata_file = workdir / 'metadata' / 'independent-pkg.json'
    with open(metadata_file, 'w') as f:
        json.dump({
            'version': '1.0',
            'modification_status': 'independent',
            'upstream_repo': 'https://src.fedoraproject.org/rpms/independent-pkg',
        }, f, indent=2)

    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add independent package'], cwd=workdir, check=True)

    # Update should fail
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', 'independent-pkg'],
        cwd=workdir, capture_output=True, text=True,
    )

    # Should exit with error
    assert result.returncode != 0
    assert "Cannot update independent package independent-pkg" in result.stderr


def test_list_all_packages(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test list command shows all packages."""
    workdir = imported_workdir('vanilla')

    # Create an independent package
    independent_dir = workdir / 'rpms' / 'independent-pkg'
    independent_dir.mkdir()
    (independent_dir / 'independent-pkg.spec').write_text("""Name: independent-pkg
Version: 1.0
Release: 1
Summary: Independent package
License: MIT

%description
Independent package

%files
""")

    metadata_file = workdir / 'metadata' / 'independent-pkg.json'
    with open(metadata_file, 'w') as f:
        json.dump({
            'version': '1.0',
            'modification_status': 'independent',
            'upstream_repo': 'https://src.fedoraproject.org/rpms/independent-pkg',
        }, f, indent=2)

    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add independent package'], cwd=workdir, check=True)

    # List all packages
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'list'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should show both packages
    assert 'ALL PACKAGES (2)' in result.stdout
    assert 'vanilla' in result.stdout
    assert 'independent-pkg' in result.stdout
    assert '[clean]' in result.stdout
    assert '[independent]' in result.stdout


def test_list_modified_only(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test list --modified shows only modified packages."""
    workdir = imported_workdir('vanilla')

    # Mark as modified
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Test modification'],
        cwd=workdir, check=True,
    )

    # List modified packages
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'list', '--modified'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should show modified package with reason
    assert 'MODIFIED PACKAGES (1)' in result.stdout
    assert 'vanilla' in result.stdout
    assert '[modified]' in result.stdout
    assert 'Test modification' in result.stdout

    # --name-only output
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'list', '--modified', '--name-only'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should show only package name without formatting
    assert result.stdout == 'vanilla\n'


def test_list_clean_only(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test list --clean shows only clean packages."""
    workdir = imported_workdir('vanilla', 'chocolate')

    # Mark vanilla as modified
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Test'],
        cwd=workdir, check=True,
    )

    # List clean packages
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'list', '--clean'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should show only chocolate
    assert 'CLEAN PACKAGES (1)' in result.stdout
    assert 'chocolate' in result.stdout
    assert 'vanilla' not in result.stdout


def test_list_independent_only(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test list --independent shows only independent packages."""
    workdir = imported_workdir('vanilla')

    # Create an independent package
    independent_dir = workdir / 'rpms' / 'independent-pkg'
    independent_dir.mkdir()
    (independent_dir / 'independent-pkg.spec').write_text("""Name: independent-pkg
Version: 1.0
Release: 1
Summary: Independent package
License: MIT

%description
Independent package

%files
""")

    metadata_file = workdir / 'metadata' / 'independent-pkg.json'
    with open(metadata_file, 'w') as f:
        json.dump({
            'version': '1.0',
            'modification_status': 'independent',
            'upstream_repo': 'https://src.fedoraproject.org/rpms/independent-pkg',
        }, f, indent=2)

    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add independent package'], cwd=workdir, check=True)

    # List independent packages
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'list', '--independent'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should show only independent package
    assert 'INDEPENDENT PACKAGES (1)' in result.stdout
    assert 'independent-pkg' in result.stdout
    assert '[independent]' in result.stdout
    assert 'vanilla' not in result.stdout


def test_diff_modified_package(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test diff shows changes for modified package."""
    workdir = imported_workdir('vanilla')

    # Modify spec file
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 1.1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump version'], cwd=workdir, check=True)

    # Mark as modified
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Test modification'],
        cwd=workdir, check=True,
    )

    # Run diff command
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )

    # Should show the modification
    assert result.returncode == 1  # Exit code 1 means diffs found
    assert 'Version: 1.0' in result.stdout
    assert 'Version: 1.1' in result.stdout


def test_diff_clean_package(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test diff shows nothing for clean package."""
    workdir = imported_workdir('vanilla')

    # Run diff command
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should show nothing and exit with 0
    assert result.returncode == 0
    assert result.stdout.strip() == ''


def test_diff_independent_package(workdir: Path) -> None:
    """Test diff skips independent packages with message."""
    # Create an independent package
    independent_dir = workdir / 'rpms' / 'independent-pkg'
    independent_dir.mkdir()
    (independent_dir / 'independent-pkg.spec').write_text("""Name: independent-pkg
Version: 1.0
Release: 1
Summary: Independent package
License: MIT

%description
Independent package

%files
""")

    metadata_file = workdir / 'metadata' / 'independent-pkg.json'
    with open(metadata_file, 'w') as f:
        json.dump({
            'version': '1.0',
            'modification_status': 'independent',
            'upstream_repo': 'https://src.fedoraproject.org/rpms/independent-pkg',
        }, f, indent=2)

    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add independent package'], cwd=workdir, check=True)

    # Run diff command
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', 'independent-pkg'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should skip with message
    assert result.returncode == 0
    assert 'Independent package (no upstream to diff against)' in result.stdout


def test_diff_raw_mode(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test --raw mode shows Release: changes."""
    workdir = imported_workdir('vanilla')

    # Bump Release only
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Release: 1', 'Release: 2')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Rebuild'], cwd=workdir, check=True)

    # Run diff without --raw (should show nothing)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ''

    # Run diff with --raw (should show Release change)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', 'vanilla', '--raw'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert 'Release: 1' in result.stdout
    assert 'Release: 2' in result.stdout


def test_diff_name_only_mode(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test --name-only mode shows only filenames."""
    workdir = imported_workdir('vanilla')

    # Modify spec file
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 1.1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump version'], cwd=workdir, check=True)

    # Run diff --name-only
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', 'vanilla', '--name-only'],
        cwd=workdir, capture_output=True, text=True,
    )

    # Should show only filename
    assert result.returncode == 1
    assert 'vanilla.spec' in result.stdout
    # Should NOT show diff content
    assert 'Version:' not in result.stdout or 'differ' in result.stdout


def test_diff_all_modified(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test --all diffs all modified packages."""
    workdir = imported_workdir('vanilla', 'chocolate')

    # Mark vanilla as modified and modify it
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Test'],
        cwd=workdir, check=True,
    )
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 1.1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump vanilla version'], cwd=workdir, check=True)

    # Mark chocolate as modified and modify it
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'chocolate', '--modified',
         '--reason', 'Test2'],
        cwd=workdir, check=True,
    )
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = chocolate_spec.read_text()
    modified_content = spec_content.replace('Version: 10', 'Version: 11')
    chocolate_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump chocolate version'], cwd=workdir, check=True)

    # Run diff --all
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', '--all'],
        cwd=workdir, capture_output=True, text=True,
    )

    # Should show both modified packages
    assert result.returncode == 1
    assert '=== vanilla ===' in result.stdout
    assert '=== chocolate ===' in result.stdout
    assert 'Version: 1.1' in result.stdout
    assert 'Version: 11' in result.stdout


def test_is_prerelease_tilde_notation(dist_git_module) -> None:
    """Test pre-release detection for tilde notation."""
    assert dist_git_module.is_prerelease("5.3.0~rc1") == (True, "tilde pre-release marker (~rc1)")
    assert dist_git_module.is_prerelease("2.0~beta1")[0] is True
    assert dist_git_module.is_prerelease("1.0~alpha")[0] is True
    assert dist_git_module.is_prerelease("3.0~pre")[0] is True


def test_is_prerelease_suffix_notation(dist_git_module) -> None:
    """Test pre-release detection for suffix notation."""
    assert dist_git_module.is_prerelease("5.3.0-rc1")[0] is True
    assert dist_git_module.is_prerelease("2.0.beta1")[0] is True
    assert dist_git_module.is_prerelease("1.0-alpha")[0] is True
    assert dist_git_module.is_prerelease("3.0.dev")[0] is True


def test_is_prerelease_stable_versions(dist_git_module) -> None:
    """Test that stable versions are not flagged as pre-release."""
    assert dist_git_module.is_prerelease("1.0") == (False, None)
    assert dist_git_module.is_prerelease("2.5.3") == (False, None)
    assert dist_git_module.is_prerelease("10.11.12") == (False, None)
    # Edge case: package with "dev" in name shouldn't match
    assert dist_git_module.is_prerelease("1.0device") == (False, None)


def test_is_prerelease_in_release_field(dist_git_module) -> None:
    """Test pre-release detection in release field (e.g., kernel-headers pattern)."""
    # Kernel-style pre-release in release field (7.0.0-0.rc1.15)
    is_pre, pattern = dist_git_module.is_prerelease("7.0.0", "0.rc1.15")
    assert is_pre is True
    assert "rc1" in pattern
    assert "in release" in pattern

    # Other release field pre-release patterns
    assert dist_git_module.is_prerelease("2.5.0", "0.beta1")[0] is True
    assert dist_git_module.is_prerelease("3.0.0", "0.1.alpha")[0] is True
    assert dist_git_module.is_prerelease("1.0.0", "0.rc2.5")[0] is True

    # Stable version with stable release
    assert dist_git_module.is_prerelease("1.0", "1") == (False, None)
    assert dist_git_module.is_prerelease("2.5.3", "59") == (False, None)

    # Pre-release in version should still be detected
    is_pre, pattern = dist_git_module.is_prerelease("5.3.0~rc1", "1")
    assert is_pre is True
    assert "in release" not in pattern  # Should be detected in version, not release


def test_update_skips_prerelease(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update skips pre-release versions by default."""
    workdir = imported_workdir('vanilla')

    # Update upstream to pre-release version
    vanilla_spec = upstream_repos["vanilla"] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 2.0~rc1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'add', 'vanilla.spec'], cwd=upstream_repos["vanilla"], check=True)
    subprocess.run(['git', 'commit', '-m', 'Update to 2.0~rc1'], cwd=upstream_repos["vanilla"], check=True)

    # Update should skip
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "pre-release version detected" in result.stderr
    assert "2.0~rc1" in result.stderr

    # Verify package not updated
    vanilla_import_json = workdir / 'metadata' / 'vanilla.json'
    with open(vanilla_import_json) as f:
        import_data = json.load(f)
    assert import_data['version'] == '1.0'


def test_update_allow_prerelease_flag(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update with --allow-prerelease accepts pre-release versions."""
    workdir = imported_workdir('vanilla')

    # Update upstream to pre-release version
    vanilla_spec = upstream_repos["vanilla"] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 2.0~rc1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'add', 'vanilla.spec'], cwd=upstream_repos["vanilla"], check=True)
    subprocess.run(['git', 'commit', '-m', 'Update to 2.0~rc1'], cwd=upstream_repos["vanilla"], check=True)

    # Update with --allow-prerelease should proceed
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check',
         '--allow-prerelease', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "Updating vanilla" in result.stderr

    # Verify package updated
    vanilla_import_json = workdir / 'metadata' / 'vanilla.json'
    with open(vanilla_import_json) as f:
        import_data = json.load(f)
    assert import_data['version'] == '2.0~rc1'


def test_update_batch_continues_on_prerelease(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Batch update continues processing after encountering pre-release."""
    workdir = imported_workdir('vanilla', 'chocolate')

    # Update vanilla to pre-release
    vanilla_spec = upstream_repos["vanilla"] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 2.0~rc1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'add', 'vanilla.spec'], cwd=upstream_repos["vanilla"], check=True)
    subprocess.run(['git', 'commit', '-m', 'Update to 2.0~rc1'], cwd=upstream_repos["vanilla"], check=True)

    # Update chocolate to stable version
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')

    # Batch update should skip vanilla but update chocolate
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert "Skipping vanilla" in result.stderr or "pre-release" in result.stderr
    assert "Updating chocolate" in result.stderr
    # Exit code may be non-zero if updates occurred, that's ok
    assert result.returncode in (0, 1)


@pytest.mark.parametrize('local_change', [None, 'comment', 'release-macro'])
def test_is_package_unmodified_with_indirect_autorelease(
    workdir: Path, upstream_repos: dict[str, Path], dist_git_module, local_change: str | None,
) -> None:
    """Generated autorelease literals are not local edits; real edits still count."""
    upstream_dir = upstream_repos['vanilla']
    upstream_spec = upstream_dir / 'vanilla.spec'
    upstream_spec.write_text(upstream_spec.read_text().replace(
        'Release: 1', '%global my_release %{autorelease}\nRelease: %{my_release}',
    ))
    subprocess.run(['git', 'commit', '-am', 'Use indirect autorelease'], cwd=upstream_dir, check=True)
    with patch.object(dist_git_module, 'get_mdapi_latest_build',
                      return_value={'version': '1.0', 'release': '2.fc99'}):
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_dir}')

    local_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    content = local_spec.read_text()
    assert '%global my_release 2' in content
    if local_change == 'comment':
        local_spec.write_text(content + '\n# Local modification\n')
    elif local_change == 'release-macro':
        local_spec.write_text(content.replace('%global my_release 2', '%global my_release 3'))
    before = local_spec.read_bytes()

    assert dist_git_module.is_package_unmodified(
        'vanilla', dist_git_module.imports['vanilla'], upstream_dir,
    ) is (local_change is None)
    assert local_spec.read_bytes() == before


@pytest.mark.parametrize('local_comment', [False, True], ids=['untouched', 'modified'])
def test_update_indirect_autorelease_without_mdapi_build(
    workdir: Path, upstream_repos: dict[str, Path], dist_git_module, local_comment: bool,
) -> None:
    """An unresolved import is valid, but real edits must still block updates."""
    upstream = upstream_repos['vanilla']
    upstream_spec = upstream / 'vanilla.spec'
    upstream_spec.write_text(upstream_spec.read_text().replace(
        'Release: 1%{?dist}', '%global my_release %{autorelease}\nRelease: %{my_release}',
    ))
    subprocess.run(['git', 'commit', '-am', 'Use indirect autorelease'], cwd=upstream, check=True)
    with patch.object(dist_git_module, 'get_mdapi_latest_build', return_value=None):
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream}')

    local_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    assert '%global my_release %{autorelease}' in local_spec.read_text()
    if local_comment:
        local_spec.write_text(local_spec.read_text() + '\n# Local change\n')
    before = local_spec.read_bytes()
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    metadata_before = metadata_file.read_bytes()
    assert json.loads(metadata_before)['modification_status'] == 'clean'
    assert dist_git_module.is_package_unmodified(
        'vanilla', dist_git_module.imports['vanilla'], upstream,
    ) is (not local_comment)
    assert local_spec.read_bytes() == before

    new_sha = add_upstream_commit(upstream, 'vanilla', '1.0', '2.0')
    with patch.object(dist_git_module, 'get_mdapi_latest_build', return_value=None):
        if local_comment:
            with pytest.raises(SystemExit, match='metadata says clean but package has local modifications'):
                run_dist_git(dist_git_module, workdir, 'update', 'vanilla', '--skip-build-check')
            assert local_spec.read_bytes() == before
            assert metadata_file.read_bytes() == metadata_before
        else:
            run_dist_git(dist_git_module, workdir, 'update', 'vanilla', '--skip-build-check')
            metadata = json.loads(metadata_file.read_text())
            assert metadata['fedora']['sha'] == new_sha
            assert metadata['modification_status'] == 'clean'
            assert 'Version: 2.0' in local_spec.read_text()


@pytest.mark.parametrize('committed', [False, True], ids=['uncommitted', 'committed'])
@pytest.mark.parametrize('macro, directive, suffix', [
    ('specrelease', 'define', '%{?buildid}%{?dist}'),
    ('krb5_release', 'global', '%{?dist}'),
])
@pytest.mark.parametrize('autorelease', [False, True], ids=['numeric', 'resolved-autorelease'])
def test_update_of_release_macro_rebuild(
    workdir: Path, upstream_repos: dict[str, Path], dist_git_module,
    committed: bool, macro: str, directive: str, suffix: str, autorelease: bool,
) -> None:
    """Documented release-macro rebuilds stay clean and do not block updates."""
    upstream = upstream_repos['vanilla']
    upstream_spec = upstream / 'vanilla.spec'
    value = '%{autorelease}' if autorelease else f'65{suffix}'
    definition = f'%{directive} {macro} {value}'
    upstream_spec.write_text(upstream_spec.read_text().replace(
        'Release: 1%{?dist}', f'{definition}\nRelease: %{{{macro}}}',
    ))
    subprocess.run(['git', 'commit', '-am', 'Use release macro'], cwd=upstream, check=True)
    with patch.object(dist_git_module, 'get_mdapi_latest_build',
                      return_value={'version': '1.0', 'release': '65.fc99'}):
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream}')

    local_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    local_spec.write_text(local_spec.read_text().replace(
        f'%{directive} {macro} 65', f'%{directive} {macro} 65.1',
    ))
    assert f'%{directive} {macro} 65.1' in local_spec.read_text()
    if committed:
        subprocess.run(['git', 'commit', '-am', 'Release-only rebuild'], cwd=workdir, check=True)
    before = local_spec.read_bytes()
    assert dist_git_module.is_package_unmodified('vanilla', dist_git_module.imports['vanilla'], upstream)
    assert local_spec.read_bytes() == before

    new_sha = add_upstream_commit(upstream, 'vanilla', '1.0', '2.0')
    with patch.object(dist_git_module, 'get_mdapi_latest_build',
                      return_value={'version': '2.0', 'release': '65.fc99'}):
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla', '--skip-build-check')

    metadata = json.loads((workdir / 'metadata' / 'vanilla.json').read_text())
    assert metadata['modification_status'] == 'clean'
    assert metadata['fedora']['sha'] == new_sha
    assert metadata['fedora']['release'] == '65'
    assert 'Version: 2.0' in local_spec.read_text()
    assert f'%{directive} {macro} 65.1' not in local_spec.read_text()


@pytest.mark.parametrize('change', ['comment', 'version', 'macro-suffix', 'patch', 'unrelated-macro'])
def test_release_macro_rebuild_does_not_hide_real_changes(
    workdir: Path, upstream_repos: dict[str, Path], dist_git_module, change: str,
) -> None:
    """Only numeric release changes are ephemeral, not arbitrary spec edits."""
    upstream = upstream_repos['vanilla']
    upstream_spec = upstream / 'vanilla.spec'
    upstream_spec.write_text('%global prerelease 1\n' + upstream_spec.read_text().replace(
        'Release: 1%{?dist}', '%define specrelease 65%{?buildid}%{?dist}\nRelease: %{specrelease}',
    ))
    subprocess.run(['git', 'commit', '-am', 'Use release macro'], cwd=upstream, check=True)
    run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream}')
    package_dir = workdir / 'rpms' / 'vanilla'
    local_spec = package_dir / 'vanilla.spec'
    content = local_spec.read_text().replace('specrelease 65', 'specrelease 65.1')
    if change == 'comment':
        content += '\n# Local change\n'
    elif change == 'version':
        content = content.replace('Version: 1.0', 'Version: 1.1')
    elif change == 'macro-suffix':
        content = content.replace('%{?buildid}', '%{?different_buildid}')
    elif change == 'patch':
        (package_dir / 'local.patch').write_text('Local patch\n')
    else:
        content = content.replace('%global prerelease 1', '%global prerelease 2')
    local_spec.write_text(content)
    files_before = {p.name: p.read_bytes() for p in package_dir.iterdir()}
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    metadata_before = metadata_file.read_bytes()

    assert not dist_git_module.is_package_unmodified('vanilla', dist_git_module.imports['vanilla'], upstream)
    add_upstream_commit(upstream, 'vanilla', '1.0', '2.0')
    with pytest.raises(SystemExit, match='metadata says clean but package has local modifications'):
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla', '--skip-build-check')
    assert {p.name: p.read_bytes() for p in package_dir.iterdir()} == files_before
    assert metadata_file.read_bytes() == metadata_before


@pytest.mark.parametrize('use_indirect', [False, True], ids=['direct', 'indirect'])
def test_update_autorelease(workdir: Path, upstream_repos: dict[str, Path], dist_git_module,
                            use_indirect: bool) -> None:
    """Test updating a package with %autorelease (direct or indirect)."""
    # Modify vanilla to use %autorelease (direct or indirect via macro)
    vanilla_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    vanilla_spec.write_text(spec_content.replace(
        'Release: 1',
        '%global my_release %{autorelease}\nRelease: %{my_release}' if use_indirect
        else 'Release: %autorelease'
    ))

    subprocess.run(['git', 'commit', '-am', 'Use autorelease'], cwd=upstream_repos['vanilla'], check=True)

    # Import with release "2"
    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '2.fc99'}
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_repos["vanilla"]}')

    # Make a minor upstream change (same version)
    spec_content = vanilla_spec.read_text()
    vanilla_spec.write_text(spec_content.replace('Summary: Test package vanilla', 'Summary: Test package update'))
    subprocess.run(['git', 'commit', '-am', 'Update summary'], cwd=upstream_repos['vanilla'], check=True)

    # Update with incremented release "3" (same version)
    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '3.fc99'}
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla', '--skip-build-check')

    # Verify that update: same version, incremented release
    spec_content = (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()
    assert 'Version: 1.0' in spec_content
    assert 'Summary: Test package update' in spec_content
    assert '%autorelease' not in spec_content

    if use_indirect:
        # Indirect: macro definition should have the release, Release: line stays as %{my_release}
        assert '%global my_release 3%{?dist}' in spec_content
        assert 'Release: %{my_release}' in spec_content
    else:
        # Direct: Release line should be updated
        assert 'Release: 3%{?dist}\n' in spec_content

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.0'
    assert metadata['fedora']['release'] == '3'

    # Update upstream to version 2.0, this usually resets release to 1 in MDAPI
    upstream_spec_content = vanilla_spec.read_text()
    vanilla_spec.write_text(upstream_spec_content.replace('Version: 1.0', 'Version: 2.0'))
    subprocess.run(['git', 'commit', '-am', 'Update to 2.0'], cwd=upstream_repos['vanilla'], check=True)

    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '2.0', 'release': '1.fc99'}
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla', '--skip-build-check')

    # Verify second update: new version, reset release
    spec_content = (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()
    assert 'Version: 2.0' in spec_content
    assert '%autorelease' not in spec_content

    if use_indirect:
        # Indirect: macro definition should have reset release
        assert '%global my_release 1%{?dist}' in spec_content
        assert 'Release: %{my_release}' in spec_content
    else:
        # Direct: Release line should be reset
        assert 'Release: 1%{?dist}\n' in spec_content

    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '2.0'
    assert metadata['fedora']['release'] == '1'


@pytest.mark.parametrize('use_indirect', [False, True], ids=['direct', 'indirect'])
def test_update_merged_autorelease_resets_local_release(
    workdir: Path, upstream_repos: dict[str, Path], dist_git_module, use_indirect: bool, caplog,
) -> None:
    """A failed normalization rebase must not retain a local release suffix."""
    upstream = upstream_repos['vanilla']
    spec = upstream / 'vanilla.spec'
    definition = ('%global my_release %{autorelease}\nRelease: %{my_release}'
                  if use_indirect else 'Release: %{autorelease}')
    spec.write_text(spec.read_text().replace('Version: 1.0', 'Version: 1.5')
                    .replace('Release: 1%{?dist}', definition))
    subprocess.run(['git', 'commit', '-am', 'Use autorelease'], cwd=upstream, check=True)
    with patch.object(dist_git_module, 'get_mdapi_latest_build',
                      return_value={'version': '1.5', 'release': '1.fc99'}):
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream}')

    local = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    # An adjacent Epoch edit still makes the normalization rebase fail without
    # introducing a spec/metadata Version mismatch (rejected by HUM-10161).
    local.write_text(local.read_text().replace('Version: 1.5', 'Version: 1.5\nEpoch: 1')
                     .replace('1%{?dist}', '1.5%{?dist}')
                     + '\n%package extra\nSummary: Extra\nRelease: %{release}\n'
                     + '%description extra\nExtra package\n%files extra\n')
    run_dist_git(dist_git_module, workdir, 'mark-modified', 'vanilla',
                 '--modified', '--reason', 'Local package changes')
    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Local changes'], cwd=workdir, check=True)
    spec.write_text(spec.read_text().replace('%files', '# Fedora change\n%files'))
    subprocess.run(['git', 'commit', '-am', 'Fedora change'], cwd=upstream, check=True)

    with patch.object(dist_git_module, 'get_mdapi_latest_build',
                      return_value={'version': '1.5', 'release': '3.fc99'}):
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla', '--skip-build-check')
    result = local.read_text()
    assert '# Fedora change' in result
    assert 'Version: 1.5' in result
    assert 'falling back to direct diff' in caplog.text
    assert '1.5%{?dist}' not in result
    assert 'Release: %{release}' in result
    if use_indirect:
        assert '%global my_release 3%{?dist}' in result
        assert 'Release: %{my_release}' in result
    else:
        assert 'Release: 3%{?dist}' in result
    assert json.loads((workdir / 'metadata' / 'vanilla.json').read_text())['fedora']['release'] == '3'


def _koji_search_server(builds: list[dict]) -> MagicMock:
    """Build a mock Koji server whose search()/getBuild() return the given builds."""
    server = MagicMock()
    server.search.return_value = [{'id': i, 'name': b['nvr']} for i, b in enumerate(builds)]
    server.getBuild.side_effect = lambda nvr: next(
        (b for b in builds if b['nvr'] == nvr), None)
    return server


def test_resolve_release_from_koji_matches_commit(dist_git_module) -> None:
    """Release comes from the completed Koji build whose source commit matches."""
    sha = 'a' * 40
    server = _koji_search_server([{
        'nvr': 'nginx-1.30.5-1.fc46', 'state': 1, 'version': '1.30.5',
        'release': '1.fc46', 'source': f'git+https://example.com/nginx.git#{sha}',
    }])
    with patch.object(dist_git_module, 'get_koji_server', return_value=server):
        assert dist_git_module.resolve_release_from_koji('nginx', '1.30.5', sha) == '1'


def test_resolve_release_from_koji_rejects_other_commit(dist_git_module) -> None:
    """A build of the right version but a different commit is not accepted."""
    server = _koji_search_server([{
        'nvr': 'nginx-1.30.5-1.fc46', 'state': 1, 'version': '1.30.5',
        'release': '1.fc46', 'source': 'git+https://example.com/nginx.git#' + 'b' * 40,
    }])
    with patch.object(dist_git_module, 'get_koji_server', return_value=server):
        assert dist_git_module.resolve_release_from_koji('nginx', '1.30.5', 'a' * 40) is None


def test_resolve_release_from_koji_skips_incomplete_build(dist_git_module) -> None:
    """Builds that are not COMPLETE (state != 1) are ignored."""
    sha = 'a' * 40
    server = _koji_search_server([{
        'nvr': 'nginx-1.30.5-1.fc46', 'state': 0, 'version': '1.30.5',
        'release': '1.fc46', 'source': f'git+https://example.com/nginx.git#{sha}',
    }])
    with patch.object(dist_git_module, 'get_koji_server', return_value=server):
        assert dist_git_module.resolve_release_from_koji('nginx', '1.30.5', sha) is None


def test_resolve_release_from_koji_no_builds(dist_git_module) -> None:
    """No search hits returns None so the caller skips the update.

    There is deliberately no MDAPI fallback; see resolve_release_from_koji().
    """
    server = MagicMock()
    server.search.return_value = []
    with patch.object(dist_git_module, 'get_koji_server', return_value=server):
        assert dist_git_module.resolve_release_from_koji('nginx', '1.30.5', 'a' * 40) is None


def test_resolve_release_from_koji_takes_highest_release(dist_git_module) -> None:
    """One commit built on several branches: never number below Fedora."""
    sha = 'a' * 40
    src = f'git+https://example.com/nginx.git#{sha}'
    server = _koji_search_server([
        {'nvr': 'nginx-1.30.5-1.fc45', 'state': 1, 'version': '1.30.5',
         'release': '1.fc45', 'source': src},
        {'nvr': 'nginx-1.30.5-2.fc46', 'state': 1, 'version': '1.30.5',
         'release': '2.fc46', 'source': src},
    ])
    with patch.object(dist_git_module, 'get_koji_server', return_value=server):
        assert dist_git_module.resolve_release_from_koji('nginx', '1.30.5', sha) == '2'


def test_resolve_release_from_koji_ignores_module_build(dist_git_module) -> None:
    """A module build reuses the dist-git commit but its release is not %autorelease.

    Left unfiltered it survives the dist-suffix strip and wins max(), producing an
    NVR that never existed.
    """
    sha = 'a' * 40
    src = f'git+https://example.com/nginx.git#{sha}'
    server = _koji_search_server([
        {'nvr': 'nginx-1.30.5-1.module_f38+1234+abcdef', 'state': 1, 'version': '1.30.5',
         'release': '1.module_f38+1234+abcdef', 'source': src},
        {'nvr': 'nginx-1.30.5-1.fc46', 'state': 1, 'version': '1.30.5',
         'release': '1.fc46', 'source': src},
    ])
    with patch.object(dist_git_module, 'get_koji_server', return_value=server):
        assert dist_git_module.resolve_release_from_koji('nginx', '1.30.5', sha) == '1'


def test_resolve_release_from_koji_accepts_prerelease(dist_git_module) -> None:
    """A pre-release autorelease value like 0.1.rc1.fc46 must be accepted."""
    sha = 'a' * 40
    server = _koji_search_server([{
        'nvr': 'foo-1.0-0.1.rc1.fc46', 'state': 1, 'version': '1.0',
        'release': '0.1.rc1.fc46', 'source': f'git+https://example.com/foo.git#{sha}',
    }])
    with patch.object(dist_git_module, 'get_koji_server', return_value=server):
        assert dist_git_module.resolve_release_from_koji('foo', '1.0', sha) == '0.1.rc1'


def test_update_autorelease_prefers_koji_over_stale_mdapi(
        workdir: Path, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Regression: a lagging MDAPI must not block an update Koji already has.

    MDAPI reflects a later compose, so right after a version bump it still
    reports the previous version's release. Pairing that with the new spec
    version yields an NVR that never existed and the update was skipped
    (observed on nginx 1.30.5). Koji is authoritative and must win.
    """
    vanilla_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    vanilla_spec.write_text(vanilla_spec.read_text().replace('Release: 1', 'Release: %autorelease'))
    subprocess.run(['git', 'commit', '-am', 'Use autorelease'], cwd=upstream_repos['vanilla'], check=True)

    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '2.fc99'}
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_repos["vanilla"]}')

    new_sha = add_upstream_commit(upstream_repos['vanilla'], 'vanilla', '1.0', '2.0')

    # Koji has the real build (2.0-1); MDAPI still reports the old version (1.0-2).
    server = _koji_search_server([{
        'nvr': 'vanilla-2.0-1.fc99', 'state': 1, 'version': '2.0',
        'release': '1.fc99', 'source': f'git+https://example.com/vanilla.git#{new_sha}',
    }])
    with patch.object(dist_git_module, 'get_koji_server', return_value=server), \
            patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '2.fc99'}
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla')
        # Koji answered, so the stale MDAPI must never be consulted
        mock_mdapi.assert_not_called()

    spec_content = (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()
    assert 'Version: 2.0' in spec_content
    assert 'Release: 1%{?dist}\n' in spec_content
    assert '%autorelease' not in spec_content

    with open(workdir / 'metadata' / 'vanilla.json') as f:
        metadata = json.load(f)
    assert metadata['version'] == '2.0'
    assert metadata['fedora']['release'] == '1'
    assert metadata['fedora']['sha'] == new_sha


def test_call_koji_with_retry_offline_raises_koji_offline(dist_git_module) -> None:
    """A ServerOffline fault becomes KojiOfflineError and is not retried."""
    method = MagicMock(side_effect=xmlrpc.client.Fault(
        dist_git_module.KOJI_SERVER_OFFLINE, 'server is offline'))
    with pytest.raises(dist_git_module.KojiOfflineError):
        dist_git_module.call_koji_with_retry(method, 'x', method_name='test')
    assert method.call_count == 1  # retrying a planned outage is pointless


def test_call_koji_with_retry_other_fault_still_raises_fault(dist_git_module) -> None:
    """Non-offline faults keep their existing behaviour."""
    method = MagicMock(side_effect=xmlrpc.client.Fault(1000, 'boom'))
    with pytest.raises(xmlrpc.client.Fault):
        dist_git_module.call_koji_with_retry(method, 'x', method_name='test')


def test_update_skips_cleanly_when_koji_offline(
        workdir: Path, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """A planned Koji outage is a skip, not a failure.

    The hub returns ServerOffline on every call during e.g. a mass rebuild.
    We cannot verify the build, so we must not update -- but it is expected and
    self-healing, so it must not fail the scheduled job or page anyone.
    """
    run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_repos["chocolate"]}')
    new_sha = add_upstream_commit(upstream_repos['chocolate'], 'chocolate', '10', '11')

    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()
        mock_server.getBuild.side_effect = xmlrpc.client.Fault(
            dist_git_module.KOJI_SERVER_OFFLINE, 'server is offline')
        mock_server_class.return_value = mock_server

        # Must return normally rather than propagating the fault
        run_dist_git(dist_git_module, workdir, 'update', 'chocolate')

    # Nothing was updated: metadata still points at the pre-update commit
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['fedora']['sha'] != new_sha
    assert metadata['version'] == '10'


def test_update_autorelease_skips_when_koji_offline(
        workdir: Path, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Same planned outage, but hit while resolving %autorelease.

    Covers the second KojiOfflineError handler: the one around
    resolve_release_from_koji(), which runs before check_koji_build().
    """
    vanilla_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    vanilla_spec.write_text(vanilla_spec.read_text().replace('Release: 1', 'Release: %autorelease'))
    subprocess.run(['git', 'commit', '-am', 'Use autorelease'], cwd=upstream_repos['vanilla'], check=True)

    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '1.fc99'}
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_repos["vanilla"]}')

    new_sha = add_upstream_commit(upstream_repos['vanilla'], 'vanilla', '1.0', '2.0')

    server = MagicMock()
    server.search.side_effect = xmlrpc.client.Fault(
        dist_git_module.KOJI_SERVER_OFFLINE, 'server is offline')
    with patch.object(dist_git_module, 'get_koji_server', return_value=server):
        # Must return normally rather than propagating the fault
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla')

    with open(workdir / 'metadata' / 'vanilla.json') as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.0', "Should not update while Koji is offline"
    assert metadata['fedora']['sha'] != new_sha


def test_update_autorelease_no_koji_build_skips_without_mdapi(
        workdir: Path, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """No matching Koji build means skip, with no MDAPI fallback.

    Falling back to MDAPI here would only resolve a release the build check is
    about to reject, and would report a misleading reason for the skip.
    """
    vanilla_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    vanilla_spec.write_text(vanilla_spec.read_text().replace('Release: 1', 'Release: %autorelease'))
    subprocess.run(['git', 'commit', '-am', 'Use autorelease'], cwd=upstream_repos['vanilla'], check=True)

    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '2.fc99'}
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_repos["vanilla"]}')

    add_upstream_commit(upstream_repos['vanilla'], 'vanilla', '1.0', '2.0')

    server = MagicMock()
    server.search.return_value = []  # Koji has no build for this version yet
    with patch.object(dist_git_module, 'get_koji_server', return_value=server), \
            patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla')
        mock_mdapi.assert_not_called()

    with open(workdir / 'metadata' / 'vanilla.json') as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.0', "Should not update without a Koji build"


def test_update_autorelease_modified(workdir: Path, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Test updating a modified package with %autorelease (triggers merge path)."""
    # Modify vanilla to use %autorelease
    vanilla_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    vanilla_spec.write_text(spec_content.replace('Release: 1', 'Release: %autorelease'))
    subprocess.run(['git', 'commit', '-am', 'Use autorelease'], cwd=upstream_repos['vanilla'], check=True)

    # Import with release "2"
    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '2.fc99'}
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_repos["vanilla"]}')

    # Make a local modification (to trigger merge path)
    local_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    local_content = local_spec.read_text()
    local_spec.write_text(local_content.replace('License: MIT', 'License: MIT\n# Local comment'))
    run_dist_git(dist_git_module, workdir, 'mark-modified', 'vanilla', '--modified',
                 '--reason', 'Test local modification')
    subprocess.run(['git', 'commit', '-am', 'Local modification'], cwd=workdir, check=True)

    # Make a minor upstream change (same version, still uses %autorelease)
    spec_content = vanilla_spec.read_text()
    vanilla_spec.write_text(spec_content.replace('Summary: Test package vanilla', 'Summary: Test package update'))
    subprocess.run(['git', 'commit', '-am', 'Update summary'], cwd=upstream_repos['vanilla'], check=True)

    # Update with incremented release "3" (should merge local + upstream changes)
    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '3.fc99'}
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla', '--skip-build-check')

    # Verify that update: same version, incremented release, both changes merged
    spec_content = (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()
    assert 'Version: 1.0' in spec_content
    assert 'Release: 3%{?dist}\n' in spec_content
    assert 'Summary: Test package update' in spec_content  # Upstream change
    assert '# Local comment' in spec_content  # Local modification preserved
    assert '%autorelease' not in spec_content

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.0'
    assert metadata['fedora']['release'] == '3'


def test_replace_autorelease_optional_when_local_merge_removed_macro(
    tmp_path: Path, dist_git_module
) -> None:
    """Update merge path tolerates local specs that already replaced %autorelease."""
    package_dir = tmp_path / 'package'
    package_dir.mkdir()
    spec_file = package_dir / 'package.spec'
    spec_file.write_text("""Name: package
Version: 1.0
Release: 0.1%{?dist}
Summary: Test package
License: MIT
""")

    dist_git_module.replace_autorelease_in_spec(package_dir, '3', required=False)

    assert spec_file.read_text().count('Release: 0.1%{?dist}') == 1


def test_update_with_ref(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update with --ref to get a specific older commit."""
    workdir = imported_workdir('chocolate')

    # Get the current SHA
    import_json_file = workdir / 'metadata' / 'chocolate.json'
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['version'] == '10'

    # Add new commits upstream
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')
    intermediate_sha = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '11', '12')
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '12', '13')

    # Update with --ref to get the intermediate version (not latest)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--ref', intermediate_sha,
         '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Check that we updated to the intermediate version
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['fedora']['sha'] == intermediate_sha
    assert import_data['version'] == '12'
    assert import_data['fedora']['release'] == '1'

    # Verify commit message includes the intermediate SHA
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Update chocolate from 10-1 to 12-1'
    assert f"Upstream: {intermediate_sha}" in body

    # Spec should have version 12
    spec_content = (workdir / 'rpms' / 'chocolate' / 'chocolate.spec').read_text()
    assert 'Version: 12' in spec_content


def test_sync_bypasses_prerelease_check(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Sync command bypasses pre-release check."""
    workdir = imported_workdir('vanilla')

    # Update upstream to pre-release version
    vanilla_spec = upstream_repos["vanilla"] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 2.0~rc1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'add', 'vanilla.spec'], cwd=upstream_repos["vanilla"], check=True)
    subprocess.run(['git', 'commit', '-m', 'Update to 2.0~rc1'], cwd=upstream_repos["vanilla"], check=True)

    # Sync should update despite pre-release version
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "Syncing vanilla" in result.stderr

    # Verify package updated
    vanilla_import_json = workdir / 'metadata' / 'vanilla.json'
    with open(vanilla_import_json) as f:
        import_data = json.load(f)
    assert import_data['version'] == '2.0~rc1'


def test_set_upstream_track(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --track-version latest sets track_upstream to 'latest'."""
    workdir = imported_workdir('vanilla')

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert 'track_upstream' not in metadata

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', 'latest', '--project-id', '12345'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['track_upstream'] == 'latest'
    assert metadata['release_monitoring_project_id'] == 12345


def test_set_upstream_no_track(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --no-track-version --no-project-id removes tracking from metadata."""
    workdir = imported_workdir('vanilla')

    # Enable first
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', 'latest', '--project-id', '12345'],
        cwd=workdir, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['track_upstream'] == 'latest'

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--no-track-version', '--no-project-id'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    with open(metadata_file) as f:
        metadata = json.load(f)
    assert 'track_upstream' not in metadata
    assert 'release_monitoring_project_id' not in metadata


def test_set_upstream_project_id_with_track(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --project-id with --track-version sets both fields."""
    workdir = imported_workdir('vanilla')

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--project-id', '99999', '--track-version', 'latest'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['release_monitoring_project_id'] == 99999
    assert metadata['track_upstream'] == 'latest'


def test_set_upstream_track_version(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --track-version stores version prefix in track_upstream."""
    workdir = imported_workdir('vanilla')

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', '1.26', '--project-id', '12345'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['track_upstream'] == '1.26'
    assert metadata['release_monitoring_project_id'] == 12345


def test_set_upstream_no_track_version(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --no-track-version --no-project-id removes upstream tracking."""
    workdir = imported_workdir('vanilla')

    # Set tracking first
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', '1.26', '--project-id', '12345'],
        cwd=workdir, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['track_upstream'] == '1.26'

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--no-track-version', '--no-project-id'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    with open(metadata_file) as f:
        metadata = json.load(f)
    assert 'track_upstream' not in metadata
    assert 'release_monitoring_project_id' not in metadata


def test_set_upstream_project_id(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --project-id with --track-version sets release_monitoring_project_id."""
    workdir = imported_workdir('vanilla')

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--project-id', '13254', '--track-version', 'latest'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['release_monitoring_project_id'] == 13254
    assert metadata['track_upstream'] == 'latest'


def test_set_upstream_no_project_id_requires_no_track(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --no-project-id alone errors when track_upstream is still set."""
    workdir = imported_workdir('vanilla')

    # Set project ID and tracking first
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', 'latest', '--project-id', '13254'],
        cwd=workdir, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['release_monitoring_project_id'] == 13254

    # Removing only project_id while track_upstream is set should error
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla', '--no-project-id'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert 'track_upstream requires release_monitoring_project_id' in result.stderr


def test_set_upstream_combined(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """set-upstream with multiple flags sets all specified fields."""
    workdir = imported_workdir('vanilla')

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', '1.26', '--project-id', '13254'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['track_upstream'] == '1.26'
    assert metadata['release_monitoring_project_id'] == 13254


def test_list_prerelease_packages(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test list --prerelease shows only packages with pre-release versions."""
    workdir = imported_workdir('vanilla', 'chocolate')

    # Update vanilla to pre-release version
    vanilla_spec = upstream_repos["vanilla"] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 2.0~rc1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'add', 'vanilla.spec'], cwd=upstream_repos["vanilla"], check=True)
    subprocess.run(['git', 'commit', '-m', 'Update to 2.0~rc1'], cwd=upstream_repos["vanilla"], check=True)

    # Update vanilla package to pre-release
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', 'vanilla'],
        cwd=workdir, check=True,
    )

    # List pre-release packages
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'list', '--prerelease'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should only show vanilla, not chocolate
    assert 'vanilla' in result.stdout
    assert '2.0~rc1' in result.stdout
    assert 'chocolate' not in result.stdout
    assert 'PRE-RELEASE PACKAGES (1):' in result.stdout


def test_set_upstream_unknown_package(workdir: Path) -> None:
    """set-upstream on non-existent package fails."""
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'nonexistent',
         '--track-version', 'latest'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "Package nonexistent not found" in result.stderr


def test_set_upstream_no_flags(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """set-upstream with no flags exits with error."""
    workdir = imported_workdir('vanilla')

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "set-upstream requires at least one flag" in result.stderr


def test_set_upstream_gitlab_tags_rejects_project_id(
    imported_workdir, upstream_repos: dict[str, Path],
) -> None:
    """set-upstream --project-id is refused for packages using gitlab_tags tracking."""
    workdir = imported_workdir('vanilla')

    # Set up gitlab_tags tracking directly in metadata
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    metadata['version_source'] = 'gitlab_tags'
    metadata['track_upstream'] = 'latest'
    metadata['tag_strip_prefix'] = 'v'
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2, sort_keys=True)
    subprocess.run(['git', 'commit', '-a', '-m', 'Set gitlab_tags'], cwd=workdir, check=True)

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--project-id', '12345'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert 'gitlab_tags' in result.stderr


def test_set_upstream_gitlab_tags_no_project_id_is_noop(
    imported_workdir, upstream_repos: dict[str, Path],
) -> None:
    """set-upstream --no-project-id on a gitlab_tags package is a no-op."""
    workdir = imported_workdir('vanilla')

    # Set up gitlab_tags tracking
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    metadata['version_source'] = 'gitlab_tags'
    metadata['track_upstream'] = 'latest'
    metadata['tag_strip_prefix'] = 'v'
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2, sort_keys=True)
    subprocess.run(['git', 'commit', '-a', '-m', 'Set gitlab_tags'], cwd=workdir, check=True)

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--no-project-id'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Settings should be preserved
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version_source'] == 'gitlab_tags'
    assert metadata['track_upstream'] == 'latest'
    assert metadata['tag_strip_prefix'] == 'v'


def test_set_upstream_gitlab_tags_track_version(
    imported_workdir, upstream_repos: dict[str, Path],
) -> None:
    """set-upstream --track-version on a gitlab_tags package updates only track_upstream."""
    workdir = imported_workdir('vanilla')

    # Set up gitlab_tags tracking
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    metadata['version_source'] = 'gitlab_tags'
    metadata['track_upstream'] = 'latest'
    metadata['tag_strip_prefix'] = 'v'
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2, sort_keys=True)
    subprocess.run(['git', 'commit', '-a', '-m', 'Set gitlab_tags'], cwd=workdir, check=True)

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', '1.0'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version_source'] == 'gitlab_tags'
    assert metadata['track_upstream'] == '1.0'
    assert metadata.get('tag_strip_prefix', 'v') == 'v'


def test_update_skips_when_track_version_mismatch(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update skips package when upstream version doesn't match track_version prefix."""
    workdir = imported_workdir('vanilla')

    # Set track_version to 1.0 (matches current version)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', '1.0', '--project-id', '12345'],
        cwd=workdir, check=True,
    )

    # Update upstream to version 2.0 (doesn't match track_version 1.0)
    add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '2.0')

    # Update should skip due to track_version mismatch
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "doesn't match tracked version" in result.stderr

    # Verify package was NOT updated
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.0'


def test_update_proceeds_when_track_version_matches(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update proceeds when upstream version matches track_version prefix."""
    workdir = imported_workdir('vanilla')

    # Set track_version to 1 (matches 1.0 and 1.x)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', '1', '--project-id', '12345'],
        cwd=workdir, check=True,
    )

    # Update upstream to version 1.5 (matches track_version prefix "1")
    add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '1.5')

    # Update should proceed
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "Updating vanilla" in result.stderr

    # Verify package was updated
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.5'


def test_update_skips_older_version(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update skips when upstream version is older than current version."""
    workdir = imported_workdir('vanilla')

    # Bump local version to 2.0 ahead of upstream
    add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '2.0')
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, check=True,
    )
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '2.0'

    # Now upstream goes back to 1.5 (simulating a branch reset or different branch state)
    add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '2.0', '1.5')

    # Update should skip due to older version
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "upstream version 1.5 is older than current 2.0" in result.stderr

    # Verify package was NOT updated
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '2.0'


def test_sync_allows_older_version(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Sync allows downgrade to older upstream version."""
    workdir = imported_workdir('vanilla')

    # Bump local version to 2.0
    add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '2.0')
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, check=True,
    )

    # Now upstream goes back to 1.5
    add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '2.0', '1.5')

    # Sync should proceed despite older version
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "Syncing vanilla" in result.stderr

    # Verify package WAS updated (downgraded)
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.5'


def test_rebuild(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild command bumps Release field correctly."""
    workdir = imported_workdir('vanilla')

    # Rebuild the package
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'test rebuild'],
        cwd=workdir, check=True,
    )

    # Verify Release was bumped
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    assert 'Release: 1.1%{?dist}' in spec_content

    # Verify commit message
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Rebuild vanilla: test rebuild'

    # Verify git status is clean
    status_result = subprocess.run(
        ['git', 'status', '--porcelain'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    assert status_result.stdout == ''

    # Verify metadata was NOT changed (rebuilds don't affect modification_status)
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['modification_status'] == 'clean'
    assert metadata['version'] == '1.0'
    assert metadata['fedora']['release'] == '1'


def test_rebuild_already_bumped(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild increments existing .N suffix."""
    workdir = imported_workdir('vanilla')

    # Modify to have Release: 2.1%{?dist}
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    # After import, the file has %{?dist} added, so replace the whole line
    modified_content = re.sub(
        r'^Release:.*$',
        'Release: 2.1%{?dist}',
        spec_content,
        flags=re.MULTILINE
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump to 2.1'], cwd=workdir, check=True)

    # Rebuild again
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'second rebuild'],
        cwd=workdir, check=True,
    )

    # Verify Release was bumped to 2.2
    spec_content = vanilla_spec.read_text()
    assert 'Release: 2.2%{?dist}' in spec_content

    # Verify commit message
    subject, _ = get_last_commit_info(workdir)
    assert subject == 'Rebuild vanilla: second rebuild'


def test_rebuild_dry_run(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test --dry-run prevents commit."""
    workdir = imported_workdir('vanilla')

    initial_subject, _ = get_last_commit_info(workdir)

    # Rebuild with --dry-run
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'rebuild', 'vanilla',
         '--reason', 'test'],
        cwd=workdir, check=True,
    )

    # Verify spec was modified
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    assert 'Release: 1.1%{?dist}' in spec_content

    # Verify no new commit
    subject, _ = get_last_commit_info(workdir)
    assert subject == initial_subject

    # Verify git status shows modified files
    status_result = subprocess.run(
        ['git', 'status', '--porcelain'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    assert 'vanilla.spec' in status_result.stdout


def test_rebuild_preserves_macros(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild preserves macros in Release field."""
    workdir = imported_workdir('vanilla')

    # Modify to have Release with macro: 8.%{revision}%{?dist}
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    # After import, the file has %{?dist} added, so replace the whole line
    modified_content = re.sub(
        r'^Release:.*$',
        'Release: 8.%{revision}%{?dist}',
        spec_content,
        flags=re.MULTILINE
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Use macro in Release'], cwd=workdir, check=True)

    # Rebuild
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'test macro preservation'],
        cwd=workdir, check=True,
    )

    # Verify Release was bumped but macro preserved
    spec_content = vanilla_spec.read_text()
    assert 'Release: 8.%{revision}.1%{?dist}' in spec_content


def test_rebuild_nonexistent_package(workdir: Path) -> None:
    """Test rebuild fails gracefully for nonexistent package."""
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'nonexistent',
         '--reason', 'test'],
        cwd=workdir, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "Package 'nonexistent' not found" in result.stderr


def test_rebuild_all(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild --all rebuilds all packages with one commit per package."""
    workdir = imported_workdir('vanilla', 'chocolate')

    # Get commit count before rebuild
    result = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    commits_before = int(result.stdout.strip())

    # Rebuild all packages
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', '--all',
         '--reason', 'test rebuild all'],
        cwd=workdir, check=True,
    )

    # Get commit count after rebuild
    result = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    commits_after = int(result.stdout.strip())

    # Should have created 2 commits (one per package)
    assert commits_after - commits_before == 2

    # Verify both spec files were updated
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    assert 'Release: 1.1%{?dist}' in vanilla_spec.read_text()

    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    assert 'Release: 1.1%{?dist}' in chocolate_spec.read_text()

    # Verify commit messages
    result = subprocess.run(
        ['git', 'log', '--format=%s', '-2'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    commit_subjects = result.stdout.strip().split('\n')
    # Commits are in reverse order (newest first)
    assert 'Rebuild chocolate: test rebuild all' in commit_subjects or 'Rebuild vanilla: test rebuild all' in commit_subjects
    assert len([s for s in commit_subjects if 'Rebuild' in s and 'test rebuild all' in s]) == 2


def test_rebuild_all_with_exclude(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test that rebuild --all --exclude skips the excluded package(s)."""
    workdir = imported_workdir('vanilla', 'chocolate')

    # Get commit count before rebuild
    result = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    commits_before = int(result.stdout.strip())

    # Rebuild all packages except chocolate
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', '--all', '--exclude', 'chocolate',
         '--reason', 'test rebuild all with exclude'],
        cwd=workdir, check=True,
    )

    # Get commit count after rebuild
    result = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    commits_after = int(result.stdout.strip())

    # Only vanilla should have been rebuilt (1 commit)
    assert commits_after - commits_before == 1

    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    assert 'Release: 1.1%{?dist}' in vanilla_spec.read_text()

    # chocolate must remain untouched
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    assert 'Release: 1%{?dist}' in chocolate_spec.read_text()
    assert 'Release: 1.1%{?dist}' not in chocolate_spec.read_text()


@pytest.mark.parametrize(
    "extra_args,expected_stderr",
    [
        (['vanilla', 'chocolate', '--exclude', 'chocolate'], '--exclude can only be used with --all'),
        (['--exclude', 'chocolate'], '--exclude can only be used with --all'),
        (['--all', '--exclude', 'nonexistent'], '--exclude package(s) not found: nonexistent'),
        (['--all', '--exclude', 'vanilla,chocolate'], '--exclude excluded all packages; nothing to rebuild'),
        (['--all', '--exclude', ','], '--exclude value is empty or contains only whitespace/commas'),
        (['--all', '--exclude', ' , '], '--exclude value is empty or contains only whitespace/commas'),
        (['--all', '--exclude', '  '], '--exclude value is empty or contains only whitespace/commas'),
    ],
    ids=[
        'exclude_with_explicit_packages',
        'exclude_without_all_or_packages',
        'unknown_package_name',
        'excludes_everything',
        'value_is_comma_only',
        'value_is_comma_with_spaces',
        'value_is_whitespace_only',
    ],
)
def test_rebuild_exclude_validation_errors(
    imported_workdir, upstream_repos: dict[str, Path], extra_args: list[str], expected_stderr: str,
) -> None:
    """Test that invalid --exclude usage is rejected with a clear, specific error and
    that no package is touched. Covers: combining --exclude with an explicit package
    list, --exclude with neither --all nor packages, an unknown/typo'd package name
    (protects against the original hand-rolled-regex pain point), excluding every
    imported package, and an --exclude value that is empty after stripping
    whitespace/commas.

    Note: 'excludes_everything' with a comma-separated 'vanilla,chocolate' value also
    exercises multi-name comma parsing: if splitting were broken and only one name
    were recognized, the other package would remain in the rebuild set, the command
    would succeed instead of erroring, and this case would fail.
    """
    workdir = imported_workdir('vanilla', 'chocolate')

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', *extra_args, '--reason', 'test'],
        cwd=workdir, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert expected_stderr in result.stderr

    # A rejected command must not touch either package.
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    assert 'Release: 1%{?dist}' in vanilla_spec.read_text()
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    assert 'Release: 1%{?dist}' in chocolate_spec.read_text()


def test_rebuild_exclude_duplicates(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test that duplicate names in --exclude are handled gracefully."""
    workdir = imported_workdir('vanilla', 'chocolate')

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', '--all',
         '--exclude', 'chocolate,chocolate', '--reason', 'test duplicates'],
        cwd=workdir, capture_output=True, text=True
    )
    assert result.returncode == 0

    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    assert 'Release: 1.1%{?dist}' in vanilla_spec.read_text()
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    assert 'Release: 1%{?dist}' in chocolate_spec.read_text()


def test_rebuild_all_vs_package_mutually_exclusive(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test that --all and package name are mutually exclusive."""
    workdir = imported_workdir('vanilla')

    # Should fail when both --all and package name are specified
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla', '--all',
         '--reason', 'test'],
        cwd=workdir, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert 'Cannot specify both package names and --all' in result.stderr


def test_rebuild_requires_package_or_all(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test that rebuild requires either package name or --all."""
    workdir = imported_workdir('vanilla')

    # Should fail when neither --all nor package name is specified
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', '--reason', 'test'],
        cwd=workdir, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert 'Must specify either package name(s) or --all' in result.stderr


def test_rebuild_rejects_macro_without_dist(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test that rebuild rejects macros that don't end with %{?dist}."""
    workdir = imported_workdir('vanilla')

    # Modify spec to use a macro in Release field WITHOUT %{?dist}
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = re.sub(
        r'^Release:.*$',
        'Release: %{some_macro}',
        spec_content,
        flags=re.MULTILINE
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Use macro in Release'], cwd=workdir, check=True)

    # Rebuild should fail with clear error
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'test'],
        cwd=workdir, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert 'uses macros in Release field without' in result.stderr
    assert 'requires manual rebuild' in result.stderr


def test_rebuild_macro_with_dist(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test that rebuild handles macro-based Release that ends with %{?dist}."""
    workdir = imported_workdir('vanilla')

    # Modify spec to use a macro in Release field WITH %{?dist}
    # Simulates packages like rpm (%{baserelease}%{?dist}) or gcc (%{gcc_release}%{?dist})
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = re.sub(
        r'^Release:.*$',
        'Release: %{my_macro}%{?dist}',
        spec_content,
        flags=re.MULTILINE
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Use macro with dist'], cwd=workdir, check=True)

    # First rebuild: should add .1
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'first rebuild'],
        cwd=workdir, check=True,
    )

    # Verify Release was updated correctly
    spec_content = vanilla_spec.read_text()
    assert 'Release: %{my_macro}.1%{?dist}' in spec_content

    # Second rebuild: should increment to .2
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'second rebuild'],
        cwd=workdir, check=True,
    )

    # Verify Release was incremented
    spec_content = vanilla_spec.read_text()
    assert 'Release: %{my_macro}.2%{?dist}' in spec_content


def test_rebuild_with_content_after_dist(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test that rebuild preserves content after %{?dist}."""
    workdir = imported_workdir('vanilla')

    # Modify spec to have content after %{?dist} (like unbound)
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = re.sub(
        r'^Release:.*$',
        'Release: 5%{?dist} %{?extra_version:-e %{extra_version}}',
        spec_content,
        flags=re.MULTILINE
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Add extra_version'], cwd=workdir, check=True)

    # Rebuild
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'first rebuild'],
        cwd=workdir, check=True,
    )

    # Verify Release was updated and content after dist was preserved
    spec_content = vanilla_spec.read_text()
    assert 'Release: 5.1%{?dist} %{?extra_version:-e %{extra_version}}' in spec_content

    # Second rebuild
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'second rebuild'],
        cwd=workdir, check=True,
    )

    # Verify increment and preservation
    spec_content = vanilla_spec.read_text()
    assert 'Release: 5.2%{?dist} %{?extra_version:-e %{extra_version}}' in spec_content


def test_rebuild_real_world_release_strings(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild handles real-world Release field patterns seen in the repo."""
    workdir = imported_workdir('vanilla')
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'

    cases = [
        # (initial Release line, expected after first rebuild, expected after second rebuild)
        # libyuv-style: long timestamp string with no trailing digit
        ('0.62.20260213git6067afd%{?dist}',
         '0.62.20260213git6067afd.1%{?dist}',
         '0.62.20260213git6067afd.2%{?dist}'),
        # ansible-packaging-style: Fedora ships dotted release (20.1)
        # Upstream release in metadata is also "20.1", so first rebuild -> 20.1.1
        ('20.1%{?dist}', '20.1.1%{?dist}', '20.1.2%{?dist}'),
        # Fedora pre-release pattern (0.N): must not be treated as already-bumped
        ('0.1%{?dist}', '0.1.1%{?dist}', '0.1.2%{?dist}'),
        # Content after %{?dist} preserved (unbound-style)
        ('11%{?dist} %{?extra_version:-e %{extra_version}}',
         '11.1%{?dist} %{?extra_version:-e %{extra_version}}',
         '11.2%{?dist} %{?extra_version:-e %{extra_version}}'),
    ]

    for initial, after_first, after_second in cases:
        # Set Release to initial value and update metadata to match (simulates fresh import)
        spec_content = vanilla_spec.read_text()
        modified = re.sub(r'^Release:.*$', f'Release: {initial}', spec_content, flags=re.MULTILINE)
        vanilla_spec.write_text(modified)

        # Update metadata release to the plain value (without %{?dist} and trailing content)
        metadata_file = workdir / 'metadata' / 'vanilla.json'
        import json
        metadata = json.loads(metadata_file.read_text())
        # Strip %{?dist} and anything after it so fedora.release matches the spec
        upstream_rel = re.sub(r'%\{\??dist\}.*', '', initial).strip()
        metadata['fedora']['release'] = upstream_rel
        metadata_file.write_text(json.dumps(metadata, indent=2) + '\n')

        subprocess.run(['git', 'commit', '-a', '-m', f'Set Release: {initial}'],
                       cwd=workdir, check=True)

        # First rebuild
        subprocess.run(
            [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla', '--reason', 'first'],
            cwd=workdir, check=True,
        )
        assert after_first in vanilla_spec.read_text(), \
            f"After first rebuild of {initial!r}: expected {after_first!r}"

        # Second rebuild (upstream_release in metadata is still the original)
        subprocess.run(
            [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla', '--reason', 'second'],
            cwd=workdir, check=True,
        )
        assert after_second in vanilla_spec.read_text(), \
            f"After second rebuild of {initial!r}: expected {after_second!r}"


def test_rebuild_all_continues_on_failure(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test that rebuild --all continues processing when some packages fail."""
    workdir = imported_workdir('vanilla', 'chocolate')

    # Break vanilla by using a macro in Release
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = re.sub(
        r'^Release:.*$',
        'Release: %{broken_macro}',
        spec_content,
        flags=re.MULTILINE
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Break vanilla'], cwd=workdir, check=True)

    # Rebuild all with --dry-run
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'rebuild', '--all',
         '--reason', 'test all'],
        cwd=workdir, capture_output=True, text=True
    )

    # Should exit with error code since one package failed
    assert result.returncode == 1

    # Should show failure for vanilla
    assert 'Failed to rebuild vanilla' in result.stderr
    assert 'uses macros in Release field' in result.stderr

    # Should show success for chocolate
    assert 'Rebuilding chocolate' in result.stderr
    assert 'Bumped release to 1.1' in result.stderr

    # Should show summary
    assert 'Rebuild Summary:' in result.stdout
    assert 'Total packages: 2' in result.stdout
    assert 'Failed rebuilds: 1' in result.stdout
    assert 'Failed packages:' in result.stdout
    assert '- vanilla' in result.stdout

    # chocolate should be modified (in dry-run), vanilla should not
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    assert 'Release: 1.1%{?dist}' in chocolate_spec.read_text()

    # vanilla should still have the broken macro
    assert 'Release: %{broken_macro}' in vanilla_spec.read_text()


def test_rebuild_independent_package(workdir: Path) -> None:
    """Test that rebuilding an independent package bumps release correctly."""
    # Create an independent package manually
    independent_dir = workdir / 'rpms' / 'independent-pkg'
    independent_dir.mkdir()

    # Create spec file with Release: 0.1%{?dist}
    independent_spec = independent_dir / 'independent-pkg.spec'
    independent_spec.write_text("""Name: independent-pkg
Version: 1.0
Release: 0.1%{?dist}
Summary: Test independent package
License: MIT

%description
Test independent package

%files
""")

    # Create metadata for independent package (no fedora origin)
    metadata_file = workdir / 'metadata' / 'independent-pkg.json'
    metadata = {
        'version': '1.0',
        'modification_status': 'independent',
        'upstream_repo': 'https://src.fedoraproject.org/rpms/independent-pkg',
    }
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)
        f.write('\n')

    # Commit the independent package
    subprocess.run(['git', 'add', 'rpms/independent-pkg', 'metadata/independent-pkg.json'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add independent package'], cwd=workdir, check=True)

    # Rebuild the independent package
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'independent-pkg',
         '--reason', 'test independent rebuild'],
        cwd=workdir, check=True,
    )

    # Verify Release was bumped to 0.2, not 0.1.1
    spec_content = independent_spec.read_text()
    assert 'Release: 0.2%{?dist}' in spec_content, f"Expected '0.2', got spec:\n{spec_content}"

    # Verify commit message
    subject, _ = get_last_commit_info(workdir)
    assert subject == 'Rebuild independent-pkg: test independent rebuild'


def test_rebuild_ahead_of_fedora_package_without_metadata_release(
    imported_workdir, upstream_repos: dict[str, Path],
) -> None:
    """An ahead-of-Fedora package increments its local 0.1 release to 0.2."""
    workdir = imported_workdir('vanilla')
    spec_file = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_file.write_text(spec_file.read_text().replace('Release: 1%{?dist}', 'Release: 0.1%{?dist}'))
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    metadata = json.loads(metadata_file.read_text())
    del metadata['fedora']['release']
    metadata['modification_status'] = 'modified'
    metadata['modification_reason'] = 'Update to upstream version 2.0'
    metadata_file.write_text(json.dumps(metadata, indent=2) + '\n')
    subprocess.run(['git', 'commit', '-am', 'Update vanilla ahead of Fedora'], cwd=workdir, check=True)

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla', '--reason', 'test'],
        cwd=workdir, check=True,
    )

    assert 'Release: 0.2%{?dist}' in spec_file.read_text()


def test_rebuild_rev_deps_direct(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild-rev-deps with direct package name (go-rpm-macros)."""
    workdir = imported_workdir('vanilla')

    # Modify vanilla spec to add BuildRequires: go-rpm-macros
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace(
        'Summary: Test package vanilla',
        'Summary: Test package vanilla\nBuildRequires: go-rpm-macros'
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Add BuildRequires'], cwd=workdir, check=True)

    # Rebuild reverse dependencies of go-rpm-macros
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild-rev-deps', 'go-rpm-macros',
         '--reason', 'go-rpm-macros update'],
        cwd=workdir, capture_output=True, text=True, check=True
    )

    # Should rebuild vanilla
    assert 'Rebuilding vanilla: go-rpm-macros update' in result.stderr
    assert 'Release: 1.1%{?dist}' in vanilla_spec.read_text()

    # Check summary
    assert 'Rebuild Summary for go-rpm-macros reverse dependencies:' in result.stdout
    assert 'Total packages: 1' in result.stdout
    assert 'Successful rebuilds: 1' in result.stdout


def test_rebuild_rev_deps_dry_run(imported_workdir) -> None:
    """Forward dry-run to rebuilds: change dependent specs, never stage or commit."""
    workdir = imported_workdir('vanilla', 'chocolate')
    spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec.write_text(spec.read_text().replace('Summary:', 'BuildRequires: go-rpm-macros\nSummary:'))
    subprocess.run(['git', 'commit', '-am', 'Add reverse dependency'], cwd=workdir, check=True)
    initial_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=workdir)
    initial_index = subprocess.check_output(['git', 'diff', '--cached'], cwd=workdir)
    unrelated_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    unrelated_before = unrelated_spec.read_bytes()
    metadata_before = {path: path.read_bytes() for path in (workdir / 'metadata').glob('*.json')}

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'rebuild-rev-deps', 'go-rpm-macros',
         '--reason', 'Refresh reverse dependencies'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert 'Release: 1.1%{?dist}' in spec.read_text()
    assert unrelated_spec.read_bytes() == unrelated_before
    assert {path: path.read_bytes() for path in (workdir / 'metadata').glob('*.json')} == metadata_before
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=workdir) == initial_head
    assert subprocess.check_output(['git', 'diff', '--cached'], cwd=workdir) == initial_index
    assert 'Total packages: 1' in result.stdout
    assert 'Successful rebuilds: 1' in result.stdout


def setup_golang_packages(workdir: Path, versions: list[str]) -> None:
    """Create golang packages for testing virtual BuildRequires."""
    for version in versions:
        golang_dir = workdir / 'rpms' / f'golang{version}'
        golang_dir.mkdir()
        golang_spec = golang_dir / 'golang.spec'
        golang_spec.write_text(f"""Name: golang{version}
Version: {version}.0
Release: 1%{{?dist}}
Summary: Go compiler {version}
License: BSD
Provides: golang = %{{version}}-%{{release}}

%description
Go compiler version {version}

%files
""")
        metadata_file = workdir / 'metadata' / f'golang{version}.json'
        metadata = {
            'version': f'{version}.0',
            'modification_status': 'clean',
            'upstream_repo': 'https://github.com/golang/go',
            'fedora': {
                'git_url': 'https://example.com/golang.git',
                'branch': 'rawhide',
                'sha': '1234567890abcdef',
                'release': '1',
            },
        }
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)
            f.write('\n')


def test_rebuild_rev_deps_virtual_buildrequires_latest(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild-rev-deps with latest virtual BuildRequires (golang1.26)."""
    setup_golang_packages(workdir, ['1.25', '1.26'])

    # Import vanilla which BuildRequires: go-rpm-macros
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace(
        'Summary: Test package vanilla',
        'Summary: Test package vanilla\nBuildRequires: go-rpm-macros'
    )
    vanilla_spec.write_text(modified_content)

    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add golang packages and BR'], cwd=workdir, check=True)

    # Rebuild reverse dependencies of golang1.26 (latest)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild-rev-deps', 'golang1.26',
         '--reason', 'golang 1.26 update'],
        cwd=workdir, capture_output=True, text=True, check=True
    )

    # Should translate golang1.26 to go-rpm-macros and rebuild vanilla
    assert 'Translating golang1.26 to go-rpm-macros (virtual BuildRequires pattern)' in result.stderr
    assert 'Rebuilding vanilla: golang 1.26 update' in result.stderr
    assert 'Release: 1.1%{?dist}' in vanilla_spec.read_text()

    # Check commit message
    subject, _ = get_last_commit_info(workdir)
    assert subject == 'Rebuild vanilla: golang 1.26 update'


def test_rebuild_rev_deps_virtual_buildrequires_not_latest(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild-rev-deps rejects non-latest virtual BuildRequires."""
    setup_golang_packages(workdir, ['1.25', '1.26'])

    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add golang packages'], cwd=workdir, check=True)

    # Try to rebuild reverse dependencies of golang1.25 (not latest)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild-rev-deps', 'golang1.25',
         '--reason', 'golang 1.25 update'],
        cwd=workdir, capture_output=True, text=True
    )

    # Should exit with error
    assert result.returncode != 0
    assert 'golang1.25 is not the latest version' in result.stderr
    assert 'Latest version is golang1.26' in result.stderr


def test_rebuild_rev_deps_no_dependencies(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild-rev-deps with package that has no reverse dependencies."""
    workdir = imported_workdir('vanilla')

    # Rebuild reverse dependencies of vanilla (which nothing depends on)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild-rev-deps', 'vanilla',
         '--reason', 'vanilla update'],
        cwd=workdir, capture_output=True, text=True, check=True
    )

    # Should report no dependencies
    assert 'No packages found with BuildRequires: vanilla' in result.stderr


@pytest.mark.parametrize('independent', [False, True])
def test_ls_sources(
    tmp_path: Path, dist_git_module, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], independent: bool,
) -> None:
    """List a local archive with only the source download replaced."""
    package_dir = tmp_path / 'rpms' / 'example'
    archive = _make_source_tarball(package_dir, 'example-1.0', {'configure': '#!/bin/sh\n'})
    signature = f'{archive.name}.sig'
    (package_dir / signature).write_text('test signature\n')
    (package_dir / 'sources').write_text(
        f'SHA512 ({archive.name}) = abc123\nSHA512 ({signature}) = def456\n'
    )
    fedora_url = 'https://src.fedoraproject.org/rpms/example.git'
    if independent:
        metadata = dist_git_module.IndependentPackageMetadata(
            package_name='example', version='1.0', upstream_repo=fedora_url,
        )
        source_url = 'https://gitlab.com/redhat/hummingbird/rpms/example.git'
    else:
        metadata = dist_git_module.CleanPackageMetadata(
            package_name='example', version='1.0', upstream_repo=fedora_url,
            fedora=dist_git_module.FedoraOriginWithRelease(
                git_url=fedora_url, branch='rawhide', sha='a' * 40, release='1',
            ),
        )
        source_url = fedora_url
    monkeypatch.setattr(dist_git_module, 'RPMS_DIR', tmp_path / 'rpms')
    monkeypatch.setattr(dist_git_module, 'imports', {'example': metadata})
    download_cmd = [
        'dist-git-client', '--configdir', str(dist_git_module.ROOT_DIR / 'mock'),
        '--loglevel', 'warning', '--forked-from', source_url, 'sources',
    ]
    real_run = subprocess.run

    def run(cmd, **kwargs):
        if cmd == download_cmd:
            return subprocess.CompletedProcess(cmd, 0)
        assert cmd == ['tar', 'tf', str(archive)]
        return real_run(cmd, **kwargs)

    with patch.object(dist_git_module.subprocess, 'run', side_effect=run) as mocked_run:
        dist_git_module.ls_sources('example')

    assert mocked_run.call_args_list == [
        call(download_cmd, cwd=str(package_dir), check=True),
        call(['tar', 'tf', str(archive)], check=True),
    ]
    output = capfd.readouterr().out
    assert f'==== {archive.name} ====' in output
    assert f'==== {signature} ====' in output
    assert 'example-1.0/configure' in output


@pytest.mark.parametrize('filename', ['foo.sig', 'foo.asc', 'foo.sign', 'foo.patch'])
def test_list_archive_contents_skips_non_archives(dist_git_module, filename: str) -> None:
    """Signature files and generated patches (e.g. gorget source-pipeline
    transform outputs like a regenerated yarn-cache compression patch) are
    not archives and must be skipped rather than raising."""
    with patch.object(dist_git_module.subprocess, 'run') as run:
        dist_git_module.list_archive_contents(Path(filename))

    run.assert_not_called()


@pytest.mark.parametrize(
    ('filename', 'expected_cmd_prefix'),
    [
        ('foo.tar.gz', ['tar', 'tf']),
        ('foo.tar.bz2', ['tar', 'tf']),
        ('foo.tar.xz', ['tar', 'tf']),
        ('foo.zip', ['unzip', '-l']),
    ],
)
def test_list_archive_contents_known_archives(
    dist_git_module, filename: str, expected_cmd_prefix: list[str],
) -> None:
    """Known archive extensions are listed with the matching tool."""
    with patch.object(dist_git_module.subprocess, 'run') as run:
        dist_git_module.list_archive_contents(Path(filename))

    run.assert_called_once_with(expected_cmd_prefix + [filename], check=True)


def test_list_archive_contents_unknown_extension_raises(dist_git_module) -> None:
    """Genuinely unrecognized extensions still raise, instead of being
    silently skipped alongside signature files and patches."""
    with pytest.raises(RuntimeError, match='Unknown archive type: foo.exe'):
        dist_git_module.list_archive_contents(Path('foo.exe'))


#
# --branch flag tests
#

def test_update_with_branch(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update with --branch switches to a different upstream branch."""
    workdir = imported_workdir('chocolate')

    # Update with --branch f40 (version 4)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f40',
         '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify metadata updated
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['fedora']['branch'] == 'f40'
    assert metadata['version'] == '4'

    # Verify spec file has f40 content
    spec = (workdir / 'rpms' / 'chocolate' / 'chocolate.spec').read_text()
    assert 'Version: 4' in spec

    # Verify commit message includes branch change
    subject, body = get_last_commit_info(workdir)
    assert '(rawhide -> f40)' in subject
    assert 'Upstream:' in body


def test_update_with_branch_release_upgrade(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update with --branch from one numbered release to a higher one (f40 -> f99)."""
    workdir = imported_workdir('chocolate', branch='f40')

    # Update with --branch f99 (version 99)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f99',
         '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify metadata updated
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['fedora']['branch'] == 'f99'
    assert metadata['version'] == '99'

    # Verify commit message includes branch change
    subject, _ = get_last_commit_info(workdir)
    assert '(f40 -> f99)' in subject


def test_update_with_branch_modified_package(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update with --branch merges local modifications across branch change."""
    workdir = imported_workdir('chocolate')

    # Add a local modification (extra comment in spec) and mark as modified
    spec_path = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = spec_path.read_text()
    spec_path.write_text(spec_content + '# Hummingbird local modification\n')
    subprocess.run(['git', 'add', '-A'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add local modification'], cwd=workdir, check=True)

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', '--modified',
         '--reason', 'test modification', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Update with --branch f40 (version 4) — should merge our local mod
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f40',
         '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify metadata updated
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['fedora']['branch'] == 'f40'
    assert metadata['version'] == '4'
    assert metadata['modification_status'] == 'modified'

    # Verify local modification survived the merge
    spec_content = spec_path.read_text()
    assert '# Hummingbird local modification' in spec_content
    assert 'Version: 4' in spec_content


def test_update_with_branch_same_branch(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update with --branch same as current behaves like normal update."""
    workdir = imported_workdir('chocolate')

    # Add upstream commit on rawhide
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')

    # Update with --branch rawhide (same branch)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'rawhide',
         '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify normal update happened
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['fedora']['branch'] == 'rawhide'
    assert metadata['version'] == '11'

    # Verify commit message does NOT include branch change annotation
    subject, _ = get_last_commit_info(workdir)
    assert '->' not in subject


def test_update_with_branch_requires_package(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update with --branch but no package name fails with error."""
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f40'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert '--branch requires a package name' in result.stderr


def test_update_with_branch_lower_version(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Forward branch move allows version downgrade; backward move blocks it."""
    workdir = imported_workdir('chocolate', branch='f99')

    # f99 -> f40 is a backward move — version downgrade should be blocked
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f40',
         '--skip-build-check', 'chocolate'],
        cwd=workdir, capture_output=True, text=True,
    )
    # The update should silently skip (exit 0) with a warning about older version
    assert result.returncode == 0
    assert 'upstream version 4 is older than current 99' in result.stderr

    # Verify metadata NOT changed (branch still f99)
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['fedora']['branch'] == 'f99'
    assert metadata['version'] == '99'

    # rawhide -> f40 IS a forward move (rawhide resolves to f99, the highest)
    # Re-import from rawhide first
    subprocess.run(['git', 'rm', '-rf', 'rpms/chocolate', 'metadata/chocolate.json'],
                   cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Remove chocolate'], cwd=workdir, check=True)

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # rawhide (v10) -> f40 (v4): version goes down but rawhide->f40 is forward
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f40',
         '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['fedora']['branch'] == 'f40'
    assert metadata['version'] == '4'


def test_sync_with_branch(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Sync with --branch switches branch and discards local modifications."""
    workdir = imported_workdir('chocolate')

    # Add local modification and mark as modified
    spec_path = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = spec_path.read_text()
    spec_path.write_text(spec_content + '# Local change to discard\n')
    subprocess.run(['git', 'add', '-A'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Local change'], cwd=workdir, check=True)

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', '--modified',
         '--reason', 'test', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Sync with --branch f40
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', '--branch', 'f40', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify metadata updated and status reset
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['fedora']['branch'] == 'f40'
    assert metadata['version'] == '4'
    assert metadata['modification_status'] == 'clean'

    # Verify local modification discarded
    spec_content = spec_path.read_text()
    assert '# Local change to discard' not in spec_content
    assert 'Version: 4' in spec_content

    # Verify commit message
    subject, _ = get_last_commit_info(workdir)
    assert 'Sync' in subject
    assert '(rawhide -> f40)' in subject


def test_update_with_branch_and_ref(imported_workdir, upstream_repos: dict[str, Path]) -> None:
    """Update with --branch and --ref uses specific commit from new branch."""
    workdir = imported_workdir('chocolate')

    # Add two commits to f40 branch
    subprocess.run(['git', 'checkout', 'f40'], cwd=upstream_repos["chocolate"], check=True)
    intermediate_sha = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '4', '5')
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '5', '6')
    subprocess.run(['git', 'checkout', 'rawhide'], cwd=upstream_repos["chocolate"], check=True)

    # Update with --branch f40 --ref to intermediate commit (version 5, not 6)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f40',
         '--ref', intermediate_sha, '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify we got version 5, not 6
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['fedora']['branch'] == 'f40'
    assert metadata['version'] == '5'
    assert metadata['fedora']['sha'] == intermediate_sha


#
# --continue flag tests
#

def _create_conflict(workdir: Path, upstream_repos: dict[str, Path]) -> str:
    """Set up a conflicted update and return the new upstream SHA.

    Imports chocolate, makes a local License change, makes a conflicting upstream
    change, and runs update which exits with code 2.
    """
    # Import chocolate from rawhide
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Make local modification: change License line
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = chocolate_spec.read_text()
    chocolate_spec.write_text(spec_content.replace('License: GPL', 'License: MIT'))
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Local license change', 'chocolate'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    # Make conflicting upstream change
    upstream_spec = upstream_repos['chocolate'] / 'chocolate.spec'
    upstream_content = upstream_spec.read_text()
    updated = upstream_content.replace('License: GPL', 'License: Apache-2.0')
    updated = updated.replace('Version: 10', 'Version: 11')
    upstream_spec.write_text(updated)
    subprocess.run(['git', 'commit', '-a', '-m', 'Change license to Apache'],
                   cwd=upstream_repos['chocolate'], check=True)

    new_sha = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=upstream_repos['chocolate'], capture_output=True, text=True, check=True
    ).stdout.strip()

    # Run update — exits with code 2 (conflicts)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'chocolate'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 2
    return new_sha


def test_update_continue(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Full conflict -> resolve -> --continue flow."""
    new_sha = _create_conflict(workdir, upstream_repos)

    # Resolve conflicts by picking our License
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = chocolate_spec.read_text()
    resolved = re.sub(r'<<<<<<< HEAD\n.*?=======\n(.*?)>>>>>>> hummingbird-local\n',
                      r'\1', spec_content, flags=re.DOTALL)
    chocolate_spec.write_text(resolved)

    # Run --continue
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
        cwd=workdir, check=True,
    )

    # Verify commit was created with correct message
    subject, body = get_last_commit_info(workdir)
    assert 'Update chocolate from 10-1 to 11-1' in subject
    assert f'Upstream: {new_sha}' in body

    # Verify state file was cleaned up
    assert not (workdir / '.dist_git_update_state.json').exists()

    # Verify resolved spec is committed (no conflict markers)
    committed_spec = chocolate_spec.read_text()
    assert '<<<<<<' not in committed_spec
    assert 'License: MIT' in committed_spec

    # Verify metadata was restored to the updated version (HUM-2672)
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        committed_metadata = json.load(f)
    assert committed_metadata['version'] == '11', \
        "Metadata committed by --continue should contain the updated version"


@pytest.mark.parametrize(('resolved_release', 'legacy_state'), [
    ('%autorelease', False),
    ('0.1%{?dist}', False),
    ('%autorelease', True),
])
def test_update_continue_resolves_autorelease(
    workdir: Path, upstream_repos: dict[str, Path], dist_git_module, monkeypatch,
    resolved_release: str, legacy_state: bool,
) -> None:
    """A conflict outside the spec must not leave an unresolved or stale Release."""
    state_file = workdir / '.dist_git_update_state.json'
    monkeypatch.setattr(dist_git_module, 'UPDATE_STATE_FILE', state_file)
    upstream = upstream_repos['vanilla']
    upstream_spec = upstream / 'vanilla.spec'
    upstream_spec.write_text(upstream_spec.read_text().replace('Release: 1%{?dist}', 'Release: %autorelease'))
    (upstream / 'notes.txt').write_text('original\n')
    subprocess.run(['git', 'add', '-A'], cwd=upstream, check=True)
    subprocess.run(['git', 'commit', '-m', 'Use autorelease'], cwd=upstream, check=True)
    with patch.object(dist_git_module, 'get_mdapi_latest_build',
                      return_value={'version': '1.0', 'release': '2.fc99'}):
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream}')

    package_dir = workdir / 'rpms' / 'vanilla'
    (package_dir / 'notes.txt').write_text('local\n')
    run_dist_git(dist_git_module, workdir, 'mark-modified', 'vanilla', '--modified',
                 '--reason', 'Local notes')
    subprocess.run(['git', 'commit', '-am', 'Local notes'], cwd=workdir, check=True)
    (upstream / 'notes.txt').write_text('upstream\n')
    subprocess.run(['git', 'commit', '-am', 'Upstream notes'], cwd=upstream, check=True)

    with patch.object(dist_git_module, 'get_mdapi_latest_build',
                      return_value={'version': '1.0', 'release': '3.fc99'}), \
         pytest.raises(SystemExit) as exc:
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla', '--skip-build-check')
    assert exc.value.code == 2
    state = json.loads(state_file.read_text())
    assert 'Release: %autorelease' in state['upstream_spec']
    assert state['metadata']['fedora']['release'] == '3'
    if legacy_state:
        del state['upstream_spec']
        state_file.write_text(json.dumps(state))

    # Resolve the unrelated conflict. A user can retain either upstream's
    # macro or a local literal when resolving a simultaneous spec conflict.
    (package_dir / 'notes.txt').write_text('local\n')
    spec = package_dir / 'vanilla.spec'
    spec.write_text(re.sub(r'^Release:.*$', f'Release: {resolved_release}',
                           spec.read_text(), flags=re.MULTILINE))
    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mdapi, \
         patch.object(dist_git_module, 'get_koji_server') as koji:
        run_dist_git(dist_git_module, workdir, 'update', '--continue')
    mdapi.assert_not_called()
    koji.assert_not_called()
    committed_spec = subprocess.run(
        ['git', 'show', 'HEAD:rpms/vanilla/vanilla.spec'], cwd=workdir,
        capture_output=True, text=True, check=True,
    ).stdout
    assert 'Release: 3%{?dist}\n' in committed_spec
    assert '%autorelease' not in committed_spec
    assert not state_file.exists()


@pytest.mark.parametrize('version_form', ['literal', 'macro'])
@pytest.mark.parametrize('resolved_version', ['11', '12', None], ids=['matching', 'mismatched', 'unparseable'])
def test_update_continue_validates_resolved_spec_version(
    workdir: Path, upstream_repos: dict[str, Path], version_form: str, resolved_version: str | None,
) -> None:
    """Validate saved candidate metadata, retaining the state and edits on failure."""
    _create_conflict(workdir, upstream_repos)
    spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    content = re.sub(r'<<<<<<< HEAD\n.*?=======\n(.*?)>>>>>>> hummingbird-local\n',
                     r'\1', spec.read_text(), flags=re.DOTALL)
    assert '<<<<<<<' not in content
    if resolved_version is None:
        content = re.sub(r'^Version:.*\n', '', content, flags=re.MULTILINE)
    elif version_form == 'macro':
        content = f'%global resolved_version {resolved_version}\n' + content.replace(
            'Version: 11', 'Version: %{resolved_version}',
        )
    else:
        content = content.replace('Version: 11', f'Version: {resolved_version}')
    spec.write_text(content)

    def git_output(*args: str) -> str:
        return subprocess.run(['git', *args], cwd=workdir, check=True, capture_output=True, text=True).stdout

    state_file = workdir / '.dist_git_update_state.json'
    state_before = state_file.read_bytes()
    assert json.loads(state_before)['metadata']['version'] == '11'
    metadata_file = workdir / 'metadata' / 'chocolate.json'
    metadata_before = metadata_file.read_bytes()
    assert json.loads(metadata_before)['version'] == '10', 'Use pending metadata, not the old disk version'
    spec_before = spec.read_bytes()
    head_before = git_output('rev-parse', 'HEAD')
    index_before = git_output('diff', '--cached')
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
        cwd=workdir, capture_output=True, text=True,
    )

    if resolved_version != '11':
        assert result.returncode != 0, 'Committed an invalid resolved spec'
        if resolved_version:
            assert '12' in result.stderr and '11' in result.stderr
            assert 'Version:' in result.stderr and 'metadata version' in result.stderr
        else:
            assert 'rpmspec' in result.stderr
        assert state_file.read_bytes() == state_before
        assert metadata_file.read_bytes() == metadata_before
        assert spec.read_bytes() == spec_before
        assert git_output('rev-parse', 'HEAD') == head_before
        assert git_output('diff', '--cached') == index_before
        if resolved_version:
            spec.write_text(content.replace('12', '11'))
        else:
            spec.write_text('Version: 11\n' + content)
        resumed = subprocess.run(
            [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
            cwd=workdir, capture_output=True, text=True,
        )
        assert resumed.returncode == 0, resumed.stderr
    else:
        assert result.returncode == 0, result.stderr

    assert not state_file.exists()
    assert git_output('rev-parse', 'HEAD') != head_before
    assert json.loads(metadata_file.read_text())['version'] == '11'


@pytest.mark.parametrize('packages', [[], ['vanilla'], ['chocolate']], ids=['batch', 'same-package', 'other-package'])
@pytest.mark.parametrize('dry_run', [False, True], ids=['update', 'dry-run'])
def test_new_update_preserves_pending_recovery_state(imported_workdir, packages: list[str], dry_run: bool) -> None:
    """A new invocation must neither replace pending state nor touch any package."""
    workdir = imported_workdir('vanilla', 'chocolate')
    state_file = workdir / '.dist_git_update_state.json'
    metadata = json.loads((workdir / 'metadata' / 'vanilla.json').read_text())
    state_file.write_text(json.dumps({'package': 'vanilla', 'metadata': metadata,
                                      'commit_msg': 'Pending vanilla update'}, indent=2) + '\n')
    spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec.write_text(spec.read_text() + '\n# Pending manual resolution\n')
    state_before = state_file.read_bytes()
    files_before = {str(path.relative_to(workdir)): path.read_bytes()
                    for directory in ('rpms', 'metadata')
                    for path in (workdir / directory).rglob('*') if path.is_file()}

    def git_output(*args: str) -> str:
        return subprocess.run(['git', *args], cwd=workdir, check=True, capture_output=True, text=True).stdout

    head_before = git_output('rev-parse', 'HEAD')
    index_before = git_output('diff', '--cached')
    args = ['--dry-run'] if dry_run else []
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), *args, 'update', '--skip-build-check', *packages],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode == 1, result.stderr
    assert state_file.exists(), 'A new update deleted pending recovery state'
    assert state_file.read_bytes() == state_before
    assert 'pending' in result.stderr.lower()
    assert 'update --continue' in result.stderr
    assert {str(path.relative_to(workdir)): path.read_bytes()
            for directory in ('rpms', 'metadata')
            for path in (workdir / directory).rglob('*') if path.is_file()} == files_before
    assert git_output('rev-parse', 'HEAD') == head_before
    assert git_output('diff', '--cached') == index_before


def test_update_continue_no_state(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """--continue with no state file exits with error."""
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert 'No update in progress' in result.stderr


def test_update_continue_unresolved(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """--continue with remaining conflict markers exits with error."""
    _create_conflict(workdir, upstream_repos)

    # Try --continue WITHOUT resolving conflicts
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert 'Unresolved conflicts' in result.stderr

    # Verify state file still exists (for retry)
    assert (workdir / '.dist_git_update_state.json').exists()


def test_update_continue_legacy_state(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """--continue with a pre-HUM-2672 state file (no 'metadata' key) should not crash.

    Old state files written before the HUM-2672 fix lack the 'metadata' key.
    continue_update() should still work, using whatever metadata is on disk.
    """
    _create_conflict(workdir, upstream_repos)

    # Overwrite state file with the old format (no 'metadata' key)
    state_file = workdir / '.dist_git_update_state.json'
    with open(state_file) as f:
        state = json.load(f)
    state.pop('metadata', None)
    with open(state_file, 'w') as f:
        json.dump(state, f)

    # Resolve conflicts
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = chocolate_spec.read_text()
    resolved = re.sub(r'<<<<<<< HEAD\n.*?=======\n(.*?)>>>>>>> hummingbird-local\n',
                      r'\1', spec_content, flags=re.DOTALL)
    chocolate_spec.write_text(resolved)

    # --continue should succeed without error
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"--continue failed: {result.stderr}"
    assert not state_file.exists()


def test_update_continue_with_pipeline_calls_gorget(
        workdir: Path, upstream_repos: dict[str, Path], tmp_path: Path) -> None:
    """A resolved git conflict for a package with a source-pipeline.yaml must
    still get gorget-verified before --continue commits (HUM-4621 follow-up):
    resolving the git-level conflict alone says nothing about whether
    `sources` is actually gorget-verified -- exactly how the original !4170
    gcc incident happened."""
    pipeline_file = workdir / 'metadata' / 'chocolate.source-pipeline.yaml'
    _write_pipeline(pipeline_file, [{'type': 'url', 'url': 'https://example.com/chocolate-${VERSION}.tar.gz'}])
    subprocess.run(['git', 'add', '-A'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add source-pipeline.yaml'], cwd=workdir, check=True)

    _create_conflict(workdir, upstream_repos)

    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = chocolate_spec.read_text()
    resolved = re.sub(r'<<<<<<< HEAD\n.*?=======\n(.*?)>>>>>>> hummingbird-local\n',
                      r'\1', spec_content, flags=re.DOTALL)
    chocolate_spec.write_text(resolved)

    fake_bin = tmp_path / 'fake-bin'
    fake_bin.mkdir()
    fake_gorget = fake_bin / 'gorget'
    fake_gorget.write_text("#!/bin/sh\necho 'simulated upstream fetch failure' >&2\nexit 1\n")
    fake_gorget.chmod(0o755)
    env = {**os.environ, 'PATH': f"{fake_bin}:{os.environ['PATH']}"}

    commits_before = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD'],
        cwd=workdir, capture_output=True, text=True, check=True,
    ).stdout.strip()

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
        cwd=workdir, capture_output=True, text=True, env=env,
    )

    assert result.returncode != 0, "Should not succeed when gorget fails"
    assert 'gorget pipeline failed' in result.stderr

    # State preserved for retry -- never silently committed a
    # gorget-unverified sources file.
    assert (workdir / '.dist_git_update_state.json').exists()
    commits_after = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD'],
        cwd=workdir, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert commits_after == commits_before, "No commit should have been created"


def test_update_continue_with_gorget_success_excludes_tarball_from_commit(
        workdir: Path, upstream_repos: dict[str, Path], tmp_path: Path) -> None:
    """Regression test for HUM-6388 on the --continue path: once gorget
    re-verifies the resolved conflict successfully, the source archive it
    copies into the package directory for upload to the lookaside cache
    must not be committed -- 'git add -f' would otherwise bypass the root
    .gitignore's rpms/*/*.tar.* exclusions for it, exactly as it did for
    NetworkManager in production.

    Uses a fake `gorget` on PATH that succeeds and emits a 'sources' file
    plus the archive it references (mirroring the real fetch's contract),
    and a stubbed upload-to-lookaside-cache.sh so no network/AWS access is
    needed."""
    pipeline_file = workdir / 'metadata' / 'chocolate.source-pipeline.yaml'
    _write_pipeline(pipeline_file, [{'type': 'url', 'url': 'https://example.com/chocolate-${VERSION}.tar.gz'}])
    subprocess.run(['git', 'add', '-A'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add source-pipeline.yaml'], cwd=workdir, check=True)

    _create_conflict(workdir, upstream_repos)

    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = chocolate_spec.read_text()
    resolved = re.sub(r'<<<<<<< HEAD\n.*?=======\n(.*?)>>>>>>> hummingbird-local\n',
                      r'\1', spec_content, flags=re.DOTALL)
    chocolate_spec.write_text(resolved)

    # Stub the upload script so gorget's real "upload the fetched archive"
    # step succeeds without touching AWS -- the interesting part of this
    # test is what gets committed locally, not the upload itself.
    upload_script = workdir / 'ci' / 'upload-to-lookaside-cache.sh'
    upload_script.write_text("#!/bin/sh\nexit 0\n")
    upload_script.chmod(0o755)

    tarball_name = 'chocolate-11.tar.gz'
    fake_bin = tmp_path / 'fake-bin'
    fake_bin.mkdir()
    fake_gorget = fake_bin / 'gorget'
    fake_gorget.write_text(
        "#!/bin/sh\n"
        "outdir=\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --output-dir) outdir=\"$2\"; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        f"printf 'x%.0s' $(seq 1 1024) > \"$outdir/{tarball_name}\"\n"
        f"hash=$(sha512sum \"$outdir/{tarball_name}\" | cut -d' ' -f1)\n"
        f"echo \"SHA512 ({tarball_name}) = $hash\" > \"$outdir/sources\"\n"
        "exit 0\n"
    )
    fake_gorget.chmod(0o755)
    env = {**os.environ, 'PATH': f"{fake_bin}:{os.environ['PATH']}"}

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
        cwd=workdir, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, f"--continue failed: {result.stderr}"

    committed_files = subprocess.run(
        ['git', 'show', '--stat', '--format=', 'HEAD'],
        cwd=workdir, capture_output=True, text=True, check=True,
    ).stdout

    assert f'rpms/chocolate/{tarball_name}' not in committed_files
    assert 'rpms/chocolate/chocolate.spec' in committed_files

    # The archive stays on disk (gorget put it there for the lookaside
    # upload) but must remain untracked for future runs too.
    assert (workdir / 'rpms' / 'chocolate' / tarball_name).exists()
    tracked = subprocess.run(
        ['git', 'ls-files', f'rpms/chocolate/{tarball_name}'],
        cwd=workdir, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert tracked == ''


@pytest.fixture
def mock_infra_repo(tmp_path: Path) -> Path:
    """Create a mock infrastructure repo with rpms-main templates."""
    infra = tmp_path / 'infrastructure'
    rpms_main = infra / 'kubernetes' / 'rpms-main'
    rpms_main.mkdir(parents=True)
    (rpms_main / '00-application.yml.j2').write_text(
        '---\napiVersion: appstudio.redhat.com/v1alpha1\nkind: Application\n'
        'metadata:\n  name: {{ env["PROJECT_NAME"] }}\n'
    )
    (rpms_main / '01-release-plans.yml.j2').write_text(
        '---\napiVersion: appstudio.redhat.com/v1alpha1\nkind: ReleasePlan\n'
        'metadata:\n  name: hummingbird-rpm-release\n  labels:\n'
        '    release.appstudio.openshift.io/releasePlanAdmission: '
        'hummingbird-rpms-tech-preview-staging\nspec:\n'
        '  application: {{ env["PROJECT_NAME"] }}\n'
    )
    (rpms_main / '10-integration-test-scenarios-testing-farm.yml.j2').write_text(
        '---\napiVersion: appstudio.redhat.com/v1beta2\nkind: IntegrationTestScenario\n'
    )
    (infra / '.gitlab-ci.yml').write_text(
        '      - PROJECT_NAME:\n          - rpms-main\n'
        '          - rpms-releases\n'
    )
    return infra


def test_add_private_product(imported_workdir, upstream_repos: dict[str, Path],
                             mock_infra_repo: Path) -> None:
    """Test add-private-product dry run shows all planned actions."""
    workdir = imported_workdir('chocolate')

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'add-private-product', 'testprod',
         '--packages', 'chocolate', '--infra-repo', str(mock_infra_repo)],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"dry run failed: {result.stderr}"
    assert 'would add RPA entry' in result.stderr
    assert 'would assign packages' in result.stderr
    assert 'would create infrastructure templates' in result.stderr
    assert 'would add private-testprod-rpms-main to .gitlab-ci.yml' in result.stderr

    with open(workdir / 'ci' / 'konflux_rpa_config.yml') as f:
        config = yaml.safe_load(f)
    assert not any(r.get('private_product') == 'testprod' for r in config['rpas'])
    assert not (mock_infra_repo / 'kubernetes' / 'private-testprod-rpms-main').exists()


def test_add_private_product_nonexistent_package(workdir: Path, mock_infra_repo: Path) -> None:
    """Test add-private-product fails for nonexistent package."""
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'add-private-product', 'testprod',
         '--packages', 'nonexistent', '--infra-repo', str(mock_infra_repo)],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert 'does not exist' in result.stderr


def test_add_private_product_duplicate_product(imported_workdir, upstream_repos: dict[str, Path],
                                               mock_infra_repo: Path) -> None:
    """Test add-private-product fails if product already exists."""
    workdir = imported_workdir('chocolate')

    with open(workdir / 'ci' / 'konflux_rpa_config.yml') as f:
        config = yaml.safe_load(f)
    config['rpas'].append({'name': 'test', 'private_product': 'existing'})
    with open(workdir / 'ci' / 'konflux_rpa_config.yml', 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'add existing product'], cwd=workdir, check=True)

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'add-private-product', 'existing',
         '--packages', 'chocolate', '--infra-repo', str(mock_infra_repo)],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert 'already exists' in result.stderr


def test_add_private_product_already_assigned(imported_workdir, upstream_repos: dict[str, Path],
                                              mock_infra_repo: Path) -> None:
    """Test add-private-product fails if package already has a private_product."""
    workdir = imported_workdir('chocolate')

    overrides_path = workdir / 'ci' / 'package-overrides.yaml'
    with open(overrides_path) as f:
        overrides = yaml.safe_load(f) or {}
    overrides['chocolate'] = {'private_product': 'other-product'}
    with open(overrides_path, 'w') as f:
        yaml.dump(overrides, f)
    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'assign to other product'], cwd=workdir, check=True)

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'add-private-product', 'testprod',
         '--packages', 'chocolate', '--infra-repo', str(mock_infra_repo)],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert 'already has private_product' in result.stderr


def test_add_private_product_preserves_formatting(imported_workdir, upstream_repos: dict[str, Path],
                                                   mock_infra_repo: Path, dist_git_module) -> None:
    """A real (non-dry-run) run must only add lines, not reformat/strip comments from
    ci/package-overrides.yaml or ci/konflux_rpa_config.yml (regression test for a bug where
    a full yaml.load/yaml.dump round-trip silently dropped every comment in both files)."""
    workdir = imported_workdir('chocolate')

    overrides_path = workdir / 'ci' / 'package-overrides.yaml'
    rpa_config_path = workdir / 'ci' / 'konflux_rpa_config.yml'
    overrides_before_lines = overrides_path.read_text().splitlines()
    rpa_before_lines = rpa_config_path.read_text().splitlines()

    dist_git_module.ROOT_DIR = workdir
    dist_git_module.PACKAGE_OVERRIDES_YAML = overrides_path

    with patch.object(dist_git_module.subprocess, 'run'):
        dist_git_module.add_private_product(
            'testprod', ['chocolate'], None,
            'hummingbird-pulp-credentials-production-secret', str(mock_infra_repo), False,
        )

    overrides_after_lines = overrides_path.read_text().splitlines()
    rpa_after_lines = rpa_config_path.read_text().splitlines()

    # Every original line (including comments) must still be present verbatim.
    for line in overrides_before_lines:
        assert line in overrides_after_lines
    for line in rpa_before_lines:
        assert line in rpa_after_lines

    # Only the new package/RPA lines were appended.
    assert len(overrides_after_lines) - len(overrides_before_lines) == 3
    assert any('private_product: testprod' in line for line in overrides_after_lines)

    with open(rpa_config_path) as f:
        rpa_config = yaml.safe_load(f)
    assert any(r.get('private_product') == 'testprod' for r in rpa_config['rpas'])


def test_normalize_release_in_specs_plain(dist_git_module, tmp_path: Path) -> None:
    """normalize_release_in_specs normalizes plain Release: lines."""
    spec = tmp_path / 'foo.spec'
    spec.write_text('Name: foo\nRelease: 5%{?dist}\nVersion: 1.0\n')
    dist_git_module.normalize_release_in_specs(tmp_path)
    assert 'Release: 0%{?dist}' in spec.read_text()


def test_normalize_release_in_specs_custom_macros(dist_git_module, tmp_path: Path) -> None:
    """normalize_release_in_specs normalizes known custom release macros."""
    spec = tmp_path / 'java.spec'
    spec.write_text(
        '%global portablerelease 3\n'
        '%global rpmrelease 1\n'
        'Name: java\nRelease: 0%{?dist}\nVersion: 21\n'
    )
    dist_git_module.normalize_release_in_specs(tmp_path)
    content = spec.read_text()
    assert '%global portablerelease 0' in content
    assert '%global rpmrelease 0' in content


def test_normalize_release_in_specs_baserelease(dist_git_module, tmp_path: Path) -> None:
    """normalize_release_in_specs normalizes %global baserelease."""
    spec = tmp_path / 'cmake.spec'
    spec.write_text('%global baserelease 5\nName: cmake\nRelease: %{baserelease}%{?dist}\n')
    dist_git_module.normalize_release_in_specs(tmp_path)
    assert '%global baserelease 0' in spec.read_text()


def test_normalize_release_in_specs_gcc_release(dist_git_module, tmp_path: Path) -> None:
    """normalize_release_in_specs normalizes %global gcc_release."""
    spec = tmp_path / 'gcc.spec'
    spec.write_text('%global gcc_release 2\nName: gcc\nRelease: %{gcc_release}%{?dist}\n')
    dist_git_module.normalize_release_in_specs(tmp_path)
    assert '%global gcc_release 0' in spec.read_text()


def test_normalize_release_in_specs_ignores_prerelease(dist_git_module, tmp_path: Path) -> None:
    """normalize_release_in_specs must NOT touch %global prerelease (version qualifier, not build release)."""
    spec = tmp_path / 'pkg.spec'
    spec.write_text('%global prerelease rc1\nName: pkg\nVersion: 1.0~%{prerelease}\nRelease: 1%{?dist}\n')
    dist_git_module.normalize_release_in_specs(tmp_path)
    content = spec.read_text()
    assert '%global prerelease rc1' in content, "prerelease macro must not be normalized"


def test_normalize_release_in_specs_define(dist_git_module, tmp_path: Path) -> None:
    """normalize_release_in_specs handles %define as well as %global."""
    spec = tmp_path / 'pkg.spec'
    spec.write_text('%define baserelease 7\nName: pkg\nRelease: %{baserelease}%{?dist}\n')
    dist_git_module.normalize_release_in_specs(tmp_path)
    assert '%define baserelease 0' in spec.read_text()


def test_strip_orphaned_patch_directives_removes_orphan(dist_git_module, tmp_path: Path) -> None:
    """Orphaned %patch -P<N> with no Patch<N>: declaration is removed."""
    spec = tmp_path / 'pkg.spec'
    spec.write_text(
        'Name: pkg\nVersion: 1.0\nRelease: 1%{?dist}\n\n'
        '%prep\n%setup -q\n%patch -P0 -p1\n\n'
        '%build\nmake\n'
    )
    removed = dist_git_module.strip_orphaned_patch_directives(spec)
    assert removed == [0]
    content = spec.read_text()
    assert '%patch -P0' not in content
    assert '%setup -q' in content   # rest of %prep untouched


def test_strip_orphaned_patch_directives_keeps_valid(dist_git_module, tmp_path: Path) -> None:
    """%patch directive with a matching Patch<N>: declaration is kept."""
    spec = tmp_path / 'pkg.spec'
    spec.write_text(
        'Name: pkg\nVersion: 1.0\nRelease: 1%{?dist}\n'
        'Patch0: fix.patch\n\n'
        '%prep\n%setup -q\n%patch -P0 -p1\n\n'
        '%build\nmake\n'
    )
    removed = dist_git_module.strip_orphaned_patch_directives(spec)
    assert removed == []
    assert '%patch -P0 -p1' in spec.read_text()


def test_strip_orphaned_patch_directives_mixed(dist_git_module, tmp_path: Path) -> None:
    """Only the orphaned directive is removed; the valid one stays."""
    spec = tmp_path / 'pkg.spec'
    spec.write_text(
        'Name: pkg\nVersion: 1.0\nRelease: 1%{?dist}\n'
        'Patch1: keep.patch\n\n'
        '%prep\n%setup -q\n%patch -P0 -p1\n%patch -P1 -p1\n\n'
        '%build\nmake\n'
    )
    removed = dist_git_module.strip_orphaned_patch_directives(spec)
    assert removed == [0]
    content = spec.read_text()
    assert '%patch -P0' not in content
    assert '%patch -P1 -p1' in content


def test_strip_orphaned_patch_directives_patch_n_style(dist_git_module, tmp_path: Path) -> None:
    """%patch<N> (without -P flag) with no declaration is also removed."""
    spec = tmp_path / 'pkg.spec'
    spec.write_text(
        'Name: pkg\nVersion: 1.0\nRelease: 1%{?dist}\n\n'
        '%prep\n%setup -q\n%patch3 -p1\n\n'
        '%build\nmake\n'
    )
    removed = dist_git_module.strip_orphaned_patch_directives(spec)
    assert removed == [3]
    assert '%patch3' not in spec.read_text()


def test_strip_orphaned_patch_directives_bare_patch_declaration(dist_git_module, tmp_path: Path) -> None:
    """Patch: (no number) counts as Patch0 and keeps %patch -P0 alive."""
    spec = tmp_path / 'pkg.spec'
    spec.write_text(
        'Name: pkg\nVersion: 1.0\nRelease: 1%{?dist}\n'
        'Patch: fix.patch\n\n'
        '%prep\n%setup -q\n%patch -P0 -p1\n\n'
        '%build\nmake\n'
    )
    removed = dist_git_module.strip_orphaned_patch_directives(spec)
    assert removed == []
    assert '%patch -P0 -p1' in spec.read_text()


def test_strip_orphaned_patch_directives_no_prep(dist_git_module, tmp_path: Path) -> None:
    """Spec with no %prep section returns empty list."""
    spec = tmp_path / 'pkg.spec'
    spec.write_text('Name: pkg\nVersion: 1.0\nRelease: 1%{?dist}\n')
    removed = dist_git_module.strip_orphaned_patch_directives(spec)
    assert removed == []


def test_detect_orphaned_patch_directives_reports_without_modifying(
    dist_git_module, tmp_path: Path
) -> None:
    """detect_orphaned_patch_directives finds orphans but leaves the spec untouched."""
    spec = tmp_path / 'pkg.spec'
    original = (
        'Name: pkg\nVersion: 1.0\nRelease: 1%{?dist}\n\n'
        '%prep\n%setup -q\n%patch -P2 -p1\n\n'
        '%build\nmake\n'
    )
    spec.write_text(original)
    assert dist_git_module.detect_orphaned_patch_directives(spec) == [2]
    assert spec.read_text() == original, "detect must not modify the spec"


def test_detect_orphaned_patch_directives_none_when_declared(
    dist_git_module, tmp_path: Path
) -> None:
    """No orphans reported when every %patch has a matching Patch<N>:."""
    spec = tmp_path / 'pkg.spec'
    spec.write_text(
        'Name: pkg\nVersion: 1.0\nRelease: 1%{?dist}\n'
        'Patch2: fix.patch\n\n'
        '%prep\n%setup -q\n%patch -P2 -p1\n\n'
        '%build\nmake\n'
    )
    assert dist_git_module.detect_orphaned_patch_directives(spec) == []


def test_update_flags_orphaned_patch_for_clean_package(
    workdir: Path, upstream_repos: dict[str, Path]
) -> None:
    """A CLEAN package with an orphaned %patch is flagged (exit 2), NOT rewritten.

    This is the real HUM-6099/ucx scenario: ucx was clean, so the spec must stay
    byte-identical to Fedora. We route it to a manual-cleanup draft MR instead of
    silently diverging from upstream.
    """
    vanilla_dir = upstream_repos['vanilla']
    vanilla_spec_path = vanilla_dir / 'vanilla.spec'

    # Upstream v1.0: has a patch declaration and explicit %patch invocation.
    vanilla_spec_path.write_text(
        'Name: vanilla\nVersion: 1.0\nRelease: 1%{?dist}\n'
        'Summary: Test package vanilla\nLicense: MIT\n'
        'Source0: vanilla.tar.gz\n'
        'Patch0: fix.patch\n\n'
        '%description\nTest package\n\n'
        '%prep\n%setup -q\n%patch -P0 -p1\n\n'
        '%files\n'
    )
    (vanilla_dir / 'fix.patch').write_text('--- a\n+++ b\n')
    subprocess.run(['git', 'add', '-A'], cwd=vanilla_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add patch'], cwd=vanilla_dir, check=True)

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{vanilla_dir}'],
        cwd=workdir, check=True,
    )

    # Upstream v2.0: drops Patch0 declaration and the patch file, but FORGETS
    # to remove the %patch -P0 invocation from %prep (the bug scenario).
    vanilla_spec_path.write_text(
        'Name: vanilla\nVersion: 2.0\nRelease: 1%{?dist}\n'
        'Summary: Test package vanilla\nLicense: MIT\n'
        'Source0: vanilla.tar.gz\n\n'
        '%description\nTest package\n\n'
        '%prep\n%setup -q\n%patch -P0 -p1\n\n'
        '%files\n'
    )
    (vanilla_dir / 'fix.patch').unlink()
    subprocess.run(['git', 'add', '-A'], cwd=vanilla_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Drop patch (forgot %patch invocation)'], cwd=vanilla_dir, check=True)

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 2, f"expected flag/exit 2, got {result.returncode}:\n{result.stderr}"

    # The clean spec must NOT have been rewritten (stays identical to Fedora).
    assert '%patch -P0' in (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()

    state = json.loads((workdir / '.dist_git_update_state.json').read_text())
    assert state['commit_msg'].startswith('NEEDS MANUAL PATCH CLEANUP:'), state['commit_msg']


def test_update_strips_orphaned_patch_for_modified_package(
    workdir: Path, upstream_repos: dict[str, Path]
) -> None:
    """A MODIFIED package auto-strips an orphaned %patch during a clean 3-way merge."""
    vanilla_dir = upstream_repos['vanilla']
    vanilla_spec_path = vanilla_dir / 'vanilla.spec'

    # Upstream v1.0: Patch0 + explicit %patch.
    vanilla_spec_path.write_text(
        'Name: vanilla\nVersion: 1.0\nRelease: 1%{?dist}\n'
        'Summary: Test package vanilla\nLicense: MIT\n'
        'Source0: vanilla.tar.gz\n'
        'Patch0: fix.patch\n\n'
        '%description\nTest package\n\n'
        '%prep\n%setup -q\n%patch -P0 -p1\n\n'
        '%build\nmake\n\n'
        '%files\n'
    )
    (vanilla_dir / 'fix.patch').write_text('--- a\n+++ b\n')
    subprocess.run(['git', 'add', '-A'], cwd=vanilla_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add patch'], cwd=vanilla_dir, check=True)

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{vanilla_dir}'],
        cwd=workdir, check=True,
    )

    # Local modification (in %build) + mark modified.
    local_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    local_spec.write_text(local_spec.read_text().replace('%build\nmake', '%build\n# Local tweak\nmake'))
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Local tweak', 'vanilla'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    # Upstream v2.0 drops Patch0 declaration + file, keeps the %patch (the bug).
    vanilla_spec_path.write_text(
        'Name: vanilla\nVersion: 2.0\nRelease: 1%{?dist}\n'
        'Summary: Test package vanilla\nLicense: MIT\n'
        'Source0: vanilla.tar.gz\n\n'
        '%description\nTest package\n\n'
        '%prep\n%setup -q\n%patch -P0 -p1\n\n'
        '%build\nmake\n\n'
        '%files\n'
    )
    (vanilla_dir / 'fix.patch').unlink()
    subprocess.run(['git', 'add', '-A'], cwd=vanilla_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Drop patch (forgot %patch invocation)'], cwd=vanilla_dir, check=True)

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"update failed:\n{result.stderr}"

    updated = local_spec.read_text()
    assert '%patch -P0' not in updated, "Orphaned %patch -P0 should have been stripped"
    assert '# Local tweak' in updated, "Local modification lost after merge"

    log = subprocess.run(['git', 'log', '-1', '--format=%B'], cwd=workdir,
                         capture_output=True, text=True, check=True).stdout
    assert 'Stripped orphaned' in log, "Commit message should mention stripped directives"


def test_update_flags_orphaned_patch_for_modified_but_unchanged_package(
    workdir: Path, upstream_repos: dict[str, Path]
) -> None:
    """A package marked 'modified' but with no actual local diffs is FLAGGED, not stripped.

    modification_status='modified' + has_modifications=False takes the verbatim-copy
    path (took_merge_path=False) and is reset to clean, so it is byte-identical to
    Fedora. Stripping it would make it fail validate --thorough as "clean but
    modified"; it must be flagged for human review instead. (Regression guard for
    the discriminator: took_merge_path, not modification_status.)
    """
    vanilla_dir = upstream_repos['vanilla']
    vanilla_spec_path = vanilla_dir / 'vanilla.spec'

    # Upstream v1.0: Patch0 + explicit %patch.
    vanilla_spec_path.write_text(
        'Name: vanilla\nVersion: 1.0\nRelease: 1%{?dist}\n'
        'Summary: Test package vanilla\nLicense: MIT\n'
        'Source0: vanilla.tar.gz\n'
        'Patch0: fix.patch\n\n'
        '%description\nTest package\n\n'
        '%prep\n%setup -q\n%patch -P0 -p1\n\n'
        '%files\n'
    )
    (vanilla_dir / 'fix.patch').write_text('--- a\n+++ b\n')
    subprocess.run(['git', 'add', '-A'], cwd=vanilla_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add patch'], cwd=vanilla_dir, check=True)

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{vanilla_dir}'],
        cwd=workdir, check=True,
    )

    # Mark modified WITHOUT changing the spec -> has_modifications stays False.
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Marked modified but no actual diffs', 'vanilla'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Mark modified (no spec change)'], cwd=workdir, check=True)

    # Upstream v2.0 drops Patch0 declaration + file, keeps the %patch (the bug).
    vanilla_spec_path.write_text(
        'Name: vanilla\nVersion: 2.0\nRelease: 1%{?dist}\n'
        'Summary: Test package vanilla\nLicense: MIT\n'
        'Source0: vanilla.tar.gz\n\n'
        '%description\nTest package\n\n'
        '%prep\n%setup -q\n%patch -P0 -p1\n\n'
        '%files\n'
    )
    (vanilla_dir / 'fix.patch').unlink()
    subprocess.run(['git', 'add', '-A'], cwd=vanilla_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Drop patch (forgot %patch invocation)'], cwd=vanilla_dir, check=True)

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 2, f"expected flag/exit 2, got {result.returncode}:\n{result.stderr}"

    # Must NOT have been rewritten: it is byte-identical to Fedora and reset to clean.
    assert '%patch -P0' in (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()

    state = json.loads((workdir / '.dist_git_update_state.json').read_text())
    assert state['commit_msg'].startswith('NEEDS MANUAL PATCH CLEANUP:'), state['commit_msg']
    assert state['metadata']['modification_status'] == 'clean', state['metadata']


def test_update_merge_clean_custom_release_macro(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update cleanly merges when upstream bumps a custom release macro like %global baserelease."""
    vanilla_dir = upstream_repos['vanilla']
    vanilla_spec_path = vanilla_dir / 'vanilla.spec'

    # Rewrite spec to use %global baserelease before importing
    vanilla_spec_path.write_text(
        '%global baserelease 1\n'
        'Name: vanilla\nVersion: 1.0\nRelease: %{baserelease}%{?dist}\n'
        'Summary: Test package vanilla\nLicense: MIT\n\n'
        '%description\nTest package\n\n%files\n'
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Use %global baserelease'], cwd=vanilla_dir, check=True)

    # Import
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{vanilla_dir}'],
        cwd=workdir, check=True,
    )

    # Local modification: bump baserelease (simulating a local rebuild) and add a comment.
    # This is the key scenario: local has baserelease=1.1, upstream bumps to 2.
    # Without the normalization fix the 3-way merge sees both sides diverge from 1
    # and produces conflict markers.
    local_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    local_spec.write_text(
        local_spec.read_text()
        .replace('%global baserelease 1', '%global baserelease 1.1')
        .replace('%description', '# Local change\n%description')
    )
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Local comment', 'vanilla'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    # Upstream bumps %global baserelease from 1 to 2
    vanilla_spec_path.write_text(
        vanilla_spec_path.read_text().replace('%global baserelease 1', '%global baserelease 2')
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump baserelease to 2'], cwd=vanilla_dir, check=True)

    # update should merge cleanly - no conflict markers
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Merge failed:\n{result.stderr}"
    merged = local_spec.read_text()
    assert '<<<<<<' not in merged, "Unexpected conflict markers in merged spec"
    assert '%global baserelease 2' in merged, "Upstream baserelease bump not applied"
    assert '# Local change' in merged, "Local modification lost after merge"


#
# Stale patch detection tests
#

def test_get_spec_patches(tmp_path: Path, dist_git_module) -> None:
    """get_spec_patches parses Patch<N>: declarations from a spec file."""
    spec = tmp_path / 'test.spec'
    spec.write_text("""\
Name: test
Version: 1.0
Release: 1%{?dist}
Patch0: fix-build.patch
# CVE-2026-12345: security fix
Patch1: CVE-2026-12345.patch
Patch100: test-i18n.patch

%description
Test

%prep
%autosetup -p1

%files
""")
    patches = dist_git_module.get_spec_patches(spec)
    assert patches == [(0, 'fix-build.patch'), (1, 'CVE-2026-12345.patch'), (100, 'test-i18n.patch')]


def test_get_spec_patches_expands_macros(tmp_path: Path, dist_git_module) -> None:
    """Resolve Version and nested spec macros without rewriting declarations."""
    spec = tmp_path / 'test.spec'
    content = """\
%global fipsver e1780dd5d390
%global fipspatch fips-17u-%{fipsver}.patch
Name: test
Version: 13.1.7
Release: 1%{?dist}
Summary: Test package
License: MIT
Patch0: grafana-yarn-cache-compression-%{version}.patch
Patch100: %{fipspatch}

%description
Test
%files
"""
    spec.write_text(content)

    assert dist_git_module.get_spec_patches(spec) == [
        (0, 'grafana-yarn-cache-compression-13.1.7.patch'),
        (100, 'fips-17u-e1780dd5d390.patch'),
    ]
    assert spec.read_text() == content


def test_get_spec_patches_uses_local_name_for_url(tmp_path: Path, dist_git_module) -> None:
    """A URL Patch tag resolves to the filename RPM saves on disk."""
    spec = tmp_path / 'test.spec'
    spec.write_text("""\
Name: test
Version: 3.0.29
Release: 1%{?dist}
Summary: Test package
License: MIT
Patch0: https://github.com/haproxy/haproxy/commit/abc.patch#/haproxy-3.0.17-lua-5.5.patch
Patch1: https://example.com/fix-%{version}.patch

%description
Test
%files
""")

    assert dist_git_module.get_spec_patches(spec) == [
        (0, 'haproxy-3.0.17-lua-5.5.patch'),
        (1, 'fix-3.0.29.patch'),
    ]


def test_drop_patches_from_spec_autosetup(tmp_path: Path, dist_git_module) -> None:
    """drop_patches_from_spec removes Patch declarations and preceding comments (%autosetup)."""
    spec = tmp_path / 'test.spec'
    spec.write_text("""\
Name: test
Version: 1.0
Release: 1%{?dist}
Patch0: keep-this.patch
# CVE-2026-12345: security fix
# Upstream commit abc123
Patch1: CVE-2026-12345.patch
Patch2: also-keep.patch

%description
Test

%prep
%autosetup -p1

%files
""")
    dist_git_module.drop_patches_from_spec(spec, {1})
    result = spec.read_text()
    assert 'Patch0: keep-this.patch' in result
    assert 'Patch2: also-keep.patch' in result
    assert 'CVE-2026-12345' not in result
    assert 'Upstream commit abc123' not in result
    assert 'Patch1' not in result


def test_drop_patches_from_spec_explicit_patch(tmp_path: Path, dist_git_module) -> None:
    """drop_patches_from_spec removes %patch -P<N> lines for explicit-apply specs."""
    spec = tmp_path / 'test.spec'
    spec.write_text("""\
Name: test
Version: 1.0
Release: 1%{?dist}
Patch0: keep-this.patch
Patch1: drop-this.patch
Patch2: also-keep.patch

%description
Test

%prep
%setup -q
%patch -P0 -p1 -b .keep
%patch -P1 -p1 -b .drop
%patch -P2 -p1 -b .alsokeep

%files
""")
    dist_git_module.drop_patches_from_spec(spec, {1})
    result = spec.read_text()
    assert '%patch -P0 -p1 -b .keep' in result
    assert '%patch -P2 -p1 -b .alsokeep' in result
    assert 'Patch1' not in result
    assert '%patch -P1' not in result


def test_drop_patches_from_spec_old_style_patch(tmp_path: Path, dist_git_module) -> None:
    """drop_patches_from_spec removes old-style %patch<N> apply lines."""
    spec = tmp_path / 'test.spec'
    spec.write_text("""\
Name: test
Version: 1.0
Release: 1%{?dist}
Patch0: keep-this.patch
Patch1: drop-this.patch
Patch2: also-keep.patch

%description
Test

%prep
%setup -q
%patch0 -p1 -b .keep
%patch1 -p1 -b .drop
%patch2 -p1 -b .alsokeep

%files
""")
    dist_git_module.drop_patches_from_spec(spec, {1})
    result = spec.read_text()
    assert 'Patch0: keep-this.patch' in result
    assert 'Patch2: also-keep.patch' in result
    assert '%patch0 -p1 -b .keep' in result
    assert '%patch2 -p1 -b .alsokeep' in result
    assert 'Patch1' not in result
    assert '%patch1' not in result


def test_drop_patches_partial_multi(tmp_path: Path, dist_git_module) -> None:
    """Dropping patch 1 and 3 keeps patch 0 and 2 intact."""
    spec = tmp_path / 'test.spec'
    spec.write_text("""\
Name: test
Version: 1.0
Release: 1%{?dist}
Patch0: keep0.patch
Patch1: drop1.patch
Patch2: keep2.patch
# Comment for patch3
Patch3: drop3.patch

%description
Test

%prep
%setup -q
%patch -P0 -p1
%patch -P1 -p1
%patch -P2 -p1
%patch -P3 -p1

%files
""")
    dist_git_module.drop_patches_from_spec(spec, {1, 3})
    result = spec.read_text()
    assert 'Patch0: keep0.patch' in result
    assert 'Patch2: keep2.patch' in result
    assert '%patch -P0 -p1' in result
    assert '%patch -P2 -p1' in result
    assert 'Patch1' not in result
    assert 'Patch3' not in result
    assert '%patch -P1' not in result
    assert '%patch -P3' not in result
    assert 'Comment for patch3' not in result


def _make_source_tarball(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    """Create a .tar.gz archive with the given files inside a top-level directory."""
    import tarfile
    source_dir = tmp_path / name
    source_dir.mkdir(parents=True)
    for fname, content in files.items():
        (source_dir / fname).write_text(content)
    tarball = tmp_path / f'{name}.tar.gz'
    with tarfile.open(tarball, 'w:gz') as tar:
        tar.add(source_dir, arcname=name)
    return tarball


@pytest.mark.parametrize(('patch_name', 'filename'), [
    ('fix.patch', 'fix.patch'),
    ('fix-%{version}.patch', 'fix-1.0.patch'),
])
def test_detect_stale_patches(
    tmp_path: Path, dist_git_module, patch_name: str, filename: str,
) -> None:
    """detect_stale_patches identifies patches already present in upstream sources."""
    # Create a "new upstream" source tree with the fix already applied
    tarball = _make_source_tarball(tmp_path / 'src', 'test-1.0', {
        'main.c': '#include <stdio.h>\nint main() { return 0; }\n',
    })

    # Set up a package dir with a spec and a patch that reverses cleanly
    pkg_dir = tmp_path / 'pkg'
    pkg_dir.mkdir()

    # Patch that adds the line "int main() { return 0; }" — already in upstream
    patch_content = """\
--- a/main.c
+++ b/main.c
@@ -1 +1,2 @@
 #include <stdio.h>
+int main() { return 0; }
"""
    (pkg_dir / filename).write_text(patch_content)
    (pkg_dir / 'unrelated.patch').write_text("""\
--- a/other.c
+++ b/other.c
@@ -0,0 +1 @@
+/* new file */
""")

    spec = pkg_dir / 'test.spec'
    spec.write_text("""\
Name: test
Version: 1.0
Release: 1%{?dist}
Summary: Test package
License: MIT
Patch0: fix.patch
Patch1: unrelated.patch

%description
Test
%files
""".replace('Patch0: fix.patch', f'Patch0: {patch_name}'))

    # Copy tarball into package dir (simulating gorget download)
    shutil.copy(tarball, pkg_dir / 'test-1.0.tar.gz')

    stale = dist_git_module.detect_stale_patches(pkg_dir, ['test-1.0.tar.gz'])

    # fix.patch should be stale (reverse-applies cleanly), unrelated.patch should not
    stale_nums = [num for num, _ in stale]
    assert 0 in stale_nums, "fix.patch should be detected as stale"
    assert 1 not in stale_nums, "unrelated.patch should NOT be detected as stale"
    assert (0, filename) in stale


def test_drop_stale_patches_deletes_files(tmp_path: Path, dist_git_module) -> None:
    """drop_stale_patches removes patch files and updates spec."""
    pkg_dir = tmp_path / 'pkg'
    pkg_dir.mkdir()
    (pkg_dir / 'keep.patch').write_text('keep')
    (pkg_dir / 'drop.patch').write_text('drop')
    spec = pkg_dir / 'test.spec'
    spec.write_text("""\
Name: test
Version: 1.0
Release: 1%{?dist}
Patch0: keep.patch
# CVE comment
Patch1: drop.patch

%description
Test

%prep
%setup -q
%patch -P0 -p1
%patch -P1 -p1

%files
""")

    dist_git_module.drop_stale_patches(pkg_dir, [(1, 'drop.patch')])

    assert (pkg_dir / 'keep.patch').exists()
    assert not (pkg_dir / 'drop.patch').exists()
    result = spec.read_text()
    assert 'Patch0: keep.patch' in result
    assert 'Patch1' not in result
    assert 'CVE comment' not in result
    assert '%patch -P0 -p1' in result
    assert '%patch -P1' not in result


def test_fetch_sources_for_stale_check_success(tmp_path: Path, dist_git_module) -> None:
    """Sources file is copied into tmpdir, dist-git-client runs there, filenames returned."""
    pkg_dir = tmp_path / 'pkg'
    pkg_dir.mkdir()
    (pkg_dir / 'sources').write_text(
        'SHA512 (test-1.0.tar.gz) = abc123\nSHA512 (extra.tar.xz) = def456\n')
    fetch_dir = tmp_path / 'fetch'
    fetch_dir.mkdir()

    with patch.object(dist_git_module.subprocess, 'run',
                      return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
        result = dist_git_module._fetch_sources_for_stale_check(
            pkg_dir, 'https://example.com/pkg', fetch_dir)

    assert result == ['test-1.0.tar.gz', 'extra.tar.xz']
    # sources must be copied into tmpdir, not left only in package_dir -- otherwise
    # dist-git-client has nothing to download from.
    assert (fetch_dir / 'sources').exists()
    # and it must run in tmpdir, so tarballs don't land in package_dir
    assert run.call_args.kwargs['cwd'] == str(fetch_dir)
    assert 'dist-git-client' in run.call_args.args[0]


def test_fetch_sources_for_stale_check_download_failure(tmp_path: Path, dist_git_module) -> None:
    """A dist-git-client failure returns [] so detection is skipped, not crashed."""
    pkg_dir = tmp_path / 'pkg'
    pkg_dir.mkdir()
    (pkg_dir / 'sources').write_text('SHA512 (test-1.0.tar.gz) = abc123\n')
    fetch_dir = tmp_path / 'fetch'
    fetch_dir.mkdir()

    with patch.object(dist_git_module.subprocess, 'run',
                      return_value=subprocess.CompletedProcess([], 1, '', 'network error')):
        result = dist_git_module._fetch_sources_for_stale_check(
            pkg_dir, 'https://example.com/pkg', fetch_dir)

    assert result == []


def test_fetch_sources_for_stale_check_no_sources_file(tmp_path: Path, dist_git_module) -> None:
    """A package with no sources file returns [] without invoking dist-git-client."""
    pkg_dir = tmp_path / 'pkg'
    pkg_dir.mkdir()
    fetch_dir = tmp_path / 'fetch'
    fetch_dir.mkdir()

    with patch.object(dist_git_module.subprocess, 'run') as run:
        result = dist_git_module._fetch_sources_for_stale_check(
            pkg_dir, 'https://example.com/pkg', fetch_dir)

    assert result == []
    run.assert_not_called()


@pytest.mark.parametrize(('upstream', 'portable'), [
    ('fix:one.patch', 'fix~3aone.patch'),
    ('a~3ab.patch', 'a~3ab.patch'),
    ('CON.txt', '~43ON.txt'),
    ('com¹.log', '~63om¹.log'),
    ('name. ', 'name~2e~20'),
    ('a?b*"<>|\\c', 'a~3fb~2a~22~3c~3e~7c~5cc'),
    ('a\x01b', 'a~01b'),
    ('ordinary.patch', 'ordinary.patch'),
])
def test_portable_path_components(dist_git_module, upstream, portable) -> None:
    from package_paths import portable_component
    assert portable_component(upstream) == portable
    assert not dist_git_module.path_problem(portable)


def test_sanitize_paths_is_idempotent(dist_git_module, tmp_path: Path) -> None:
    payload = b'--- a/source.c\n+++ b/source.c\n+const char *s = "fix:one.patch";\n'
    (tmp_path / 'fix:one.patch').write_bytes(payload)
    (tmp_path / 'ordinary.patch').write_bytes(b'other patch')
    (tmp_path / 'a:b.patch').write_bytes(b'colon patch')
    spec = 'Patch: fix:one.patch\r\nPatch1: a:b.patch\r\nSource: https://example.org/fix:one.patch\r\nSource1: ftp://example.org/fix:one.patch\r\n'
    (tmp_path / 'test.spec').write_bytes(spec.encode())
    (tmp_path / 'sources').write_text('SHA512 (archive:1.tar.xz) = abc\n')
    (tmp_path / 'archive:1.tar.xz').write_bytes(b'lookaside data')
    mapping = dist_git_module.sanitize_package_paths(tmp_path)
    assert (tmp_path / 'fix~3aone.patch').read_bytes() == payload
    assert (tmp_path / 'a~3ab.patch').read_bytes() == b'colon patch'
    assert (tmp_path / 'ordinary.patch').read_bytes() == b'other patch'
    assert b'Patch: fix~3aone.patch\r\n' in (tmp_path / 'test.spec').read_bytes()
    assert b'https://example.org/fix:one.patch#/fix~3aone.patch' in (tmp_path / 'test.spec').read_bytes()
    assert b'ftp://example.org/fix:one.patch#/fix~3aone.patch' in (tmp_path / 'test.spec').read_bytes()
    assert (tmp_path / 'archive:1.tar.xz').read_bytes() == b'lookaside data'
    assert mapping['fix:one.patch'] == 'fix~3aone.patch'
    assert dist_git_module.sanitize_package_paths(tmp_path) == {}


def test_sanitize_nested_paths_and_symlinks(dist_git_module, tmp_path: Path) -> None:
    directory = tmp_path / 'dir:one'
    directory.mkdir()
    (directory / 'file:one').write_bytes(b'payload')
    (tmp_path / 'link').symlink_to('dir:one/file:one')
    (tmp_path / 'outside').symlink_to('../dir:one/file:one')
    mapping = dist_git_module.sanitize_package_paths(tmp_path)
    assert (tmp_path / 'link').read_bytes() == b'payload'
    assert (tmp_path / 'outside').readlink().as_posix() == '../dir:one/file:one'
    assert mapping['dir:one/file:one'] == 'dir~3aone/file~3aone'
    assert dist_git_module.sanitize_package_paths(tmp_path) == {}
    (tmp_path / 'dir~3aone' / 'new').write_bytes(b'new')
    assert dist_git_module.sanitize_package_paths(tmp_path) == {}
    assert (tmp_path / 'dir~3aone/new').read_bytes() == b'new'
    assert (tmp_path / 'link').readlink().as_posix() == 'dir~3aone/file~3aone'


@pytest.mark.parametrize(('first', 'second'), [('Fix.patch', 'fix.patch'), ('a:b.patch', 'a~3ab.patch')])
def test_sanitize_paths_collision_is_atomic(dist_git_module, tmp_path: Path, first: str, second: str) -> None:
    (tmp_path / first).write_bytes(b'one')
    (tmp_path / second).write_bytes(b'two')
    (tmp_path / 'unsafe:name').write_bytes(b'three')
    with pytest.raises(ValueError, match='Portable path collision'):
        dist_git_module.sanitize_package_paths(tmp_path)
    assert (tmp_path / 'unsafe:name').read_bytes() == b'three'


def test_sanitize_paths_command_dry_run(dist_git_module, workdir: Path) -> None:
    package = workdir / 'rpms' / 'test'
    package.mkdir()
    (package / 'fix:one.patch').write_bytes(b'patch')
    metadata = {'version': '1', 'modification_status': 'independent', 'upstream_repo': 'https://src.fedoraproject.org/rpms/test'}
    metadata_path = workdir / 'metadata' / 'test.json'
    metadata_path.write_text(json.dumps(metadata))
    run_dist_git(dist_git_module, workdir, '--dry-run', 'sanitize-paths', '--all')
    assert (package / 'fix:one.patch').exists()
    assert json.loads(metadata_path.read_text()) == metadata
    run_dist_git(dist_git_module, workdir, 'sanitize-paths', 'test')
    assert (package / 'fix~3aone.patch').read_bytes() == b'patch'
    assert json.loads(metadata_path.read_text()) == metadata


def test_check_paths_rejects_windows_paths(dist_git_module, tmp_path: Path) -> None:
    with patch.object(dist_git_module, 'run_git') as git:
        git.return_value.stdout = 'good/file\0bad/name:one\0CON.txt\0Case/file\0case/other\0'
        with pytest.raises(SystemExit, match='Paths incompatible with Windows') as error:
            dist_git_module.check_paths()
        assert 'bad/name:one' in str(error.value)
        assert 'CON.txt' in str(error.value)
        assert 'collides with' in str(error.value)
        git.return_value.stdout = 'good/file\0good/other\0'
        dist_git_module.check_paths()


def test_import_update_and_merge_portable_paths(workdir: Path, upstream_repos: dict[str, Path], dist_git_module, monkeypatch) -> None:
    monkeypatch.chdir(workdir)
    upstream = upstream_repos['chocolate']
    spec = upstream / 'chocolate.spec'
    spec.write_text(spec.read_text().replace('Summary:', 'Patch: fix:one.patch\nSummary:'))
    (upstream / '.gitignore').write_text('*.patch\n')
    (upstream / 'fix:one.patch').write_bytes(b'upstream patch\n')
    subprocess.run(['git', 'add', '-f', '-A'], cwd=upstream, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add colon patch'], cwd=upstream, check=True)
    with patch.object(dist_git_module, 'lookup_anitya_project_id', return_value=None):
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream}')
    package = workdir / 'rpms' / 'chocolate'
    assert (package / 'fix~3aone.patch').read_bytes() == b'upstream patch\n'
    assert dist_git_module.is_package_unmodified('chocolate', dist_git_module.imports['chocolate'], upstream)
    assert dist_git_module.diff_package('chocolate', capture=True) == ''
    assert dist_git_module.diff_package('chocolate', capture=True, raw=True)
    (package / 'local:fix.patch').write_bytes(b'local patch\n')
    local_spec = package / 'chocolate.spec'
    local_spec.write_text(local_spec.read_text().replace('Summary:', 'Patch1: local:fix.patch\nSummary:'))
    run_dist_git(dist_git_module, workdir, 'mark-modified', 'chocolate', '--modified', '--reason', 'Local patch')
    run_dist_git(dist_git_module, workdir, 'sanitize-paths', 'chocolate')
    subprocess.run(['git', 'add', '-f', '-A'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Local patch'], cwd=workdir, check=True)
    (upstream / 'fix:one.patch').write_bytes(b'new upstream patch\n')
    subprocess.run(['git', 'add', 'fix:one.patch'], cwd=upstream, check=True)
    add_upstream_commit(upstream, 'chocolate', '10', '11')
    run_dist_git(dist_git_module, workdir, 'update', 'chocolate', '--skip-build-check')
    assert (package / 'fix~3aone.patch').read_bytes() == b'new upstream patch\n'
    assert (package / 'local~3afix.patch').read_bytes() == b'local patch\n'
    assert 'Patch1: local~3afix.patch' in local_spec.read_text()
    assert not (package / 'fix:one.patch').exists()
    metadata = json.loads((workdir / 'metadata' / 'chocolate.json').read_text())
    assert metadata['modification_status'] == 'modified'
    run_dist_git(dist_git_module, workdir, 'sync', 'chocolate', '--ref', 'HEAD~1')
    assert (package / 'fix~3aone.patch').read_bytes() == b'upstream patch\n'
    assert not (package / 'local~3afix.patch').exists()
    assert dist_git_module.is_package_unmodified('chocolate', dist_git_module.imports['chocolate'], upstream)


def test_sanitize_macro_patch_declaration(dist_git_module, tmp_path: Path) -> None:
    (tmp_path / 'fix:1.patch').write_bytes(b'patch')
    original = ('Version: 1\nPatch: fix:%{version}.patch\n'
                'Patch1: https://example.org/fix:%{version}.patch\n')
    spec = tmp_path / 'test.spec'
    spec.write_text(original)
    mapping = dist_git_module.sanitize_package_paths(tmp_path)
    assert 'Patch: fix~3a%{version}.patch' in spec.read_text()
    assert 'https://example.org/fix:%{version}.patch#/fix~3a%{version}.patch' in spec.read_text()
    assert mapping['fix:1.patch'] == 'fix~3a1.patch'
    assert dist_git_module.sanitize_package_paths(tmp_path) == {}


@pytest.mark.parametrize(('filename', 'reference', 'portable', 'rewritten'), [
    ('CON.patch', 'CON%{empty}.patch', '~43ON.patch', '~43ON%{empty}.patch'),
    ('CON1.patch', 'CON%{version}.patch', 'CON1.patch', 'CON%{version}.patch'),
    ('CON1:fix.patch', 'CON%{version}:fix.patch', 'CON1~3afix.patch', 'CON%{version}~3afix.patch'),
    ('CON.1.patch', 'CON.%{version}.patch', '~43ON.1.patch', '~43ON.%{version}.patch'),
])
def test_sanitize_reserved_macro_patch_declaration(
        dist_git_module, tmp_path: Path, filename: str, reference: str,
        portable: str, rewritten: str) -> None:
    payload = b'unchanged patch payload'
    (tmp_path / filename).write_bytes(payload)
    spec = tmp_path / 'test.spec'
    spec.write_text(f'Patch: {reference}\nPatch1: https://example.org/{reference}\n')

    mapping = dist_git_module.sanitize_package_paths(tmp_path)

    assert mapping == ({filename: portable} if filename != portable else {})
    url = f'https://example.org/{reference}'
    if filename != portable:
        url += '#/' + rewritten
    assert spec.read_text() == f'Patch: {rewritten}\nPatch1: {url}\n'
    assert (tmp_path / portable).read_bytes() == payload
    assert dist_git_module.sanitize_package_paths(tmp_path) == {}


def test_sanitize_ambiguous_reserved_macro_is_atomic(dist_git_module, tmp_path: Path) -> None:
    (tmp_path / 'CON.patch').write_bytes(b'reserved patch')
    (tmp_path / 'CON1.patch').write_bytes(b'valid patch')
    spec = tmp_path / 'test.spec'
    original = 'Patch: CON%{version}.patch\n'
    spec.write_text(original)

    with pytest.raises(ValueError, match='Ambiguous reserved filename'):
        dist_git_module.sanitize_package_paths(tmp_path)

    assert spec.read_text() == original
    assert (tmp_path / 'CON.patch').read_bytes() == b'reserved patch'
    assert (tmp_path / 'CON1.patch').read_bytes() == b'valid patch'
