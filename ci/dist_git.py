#!/usr/bin/env python3
"""
dist-git importer for Hummingbird rpms repository.

This tool imports and syncs Fedora/CentOS dist-git packages
into the local rpms/ directory while tracking metadata in import.json.
"""

import argparse
import filecmp
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import xmlrpc.client
from pathlib import Path
from typing import TypedDict, cast

ROOT_DIR = Path(__file__).resolve().parent.parent
IMPORT_JSON = ROOT_DIR / 'import.json'

# Global imports dict, loaded at startup
imports: dict[str, 'PackageMetadata'] = {}


class PackageMetadata(TypedDict):
    """Metadata stored in import.json for each package."""
    source: str
    branch: str
    sha: str
    version: str
    release: str


class KojiBuild(TypedDict, total=False):
    """Koji build result from getBuild() API."""
    build_id: int
    nvr: str
    state: int
    source: str


def parse_spec_version(package_dir: Path) -> tuple[str, str]:
    """Extract version and release from package directory's spec file."""
    # Find the spec file
    spec_files = list(package_dir.glob('*.spec'))
    if len(spec_files) != 1:
        sys.exit(f"ERROR: Expected exactly one .spec file in {package_dir}, found {spec_files}")

    spec_file = spec_files[0]

    # Use rpmspec to query the resolved version and release
    # Set dist to %{nil} to get release without dist suffix
    rpmspec = subprocess.run(
        ['rpmspec', '-q', '--qf', '%{VERSION}\n%{RELEASE}\n', '--define=dist %{nil}', '--srpm', str(spec_file)],
        stdout=subprocess.PIPE, text=True, check=True)
    lines = rpmspec.stdout.strip().splitlines()
    try:
        return lines[0], lines[1]
    except IndexError as e:
        raise ValueError(f"Unexpected rpmspec output for {spec_file}: {rpmspec.stdout}") from e


def save_import_json() -> None:
    """Save import.json with sorted keys for consistent diffs."""
    with open(IMPORT_JSON, 'w') as f:
        json.dump(imports, f, indent=2, sort_keys=True)
        f.write('\n')  # Ensure trailing newline


def run_git(*args: str, cwd: Path | str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git command and return its stdout/exit code."""
    return subprocess.run(['git', *args], cwd=cwd, check=check, stdout=subprocess.PIPE, text=True)


def expand_url_shortcut(url: str) -> str:
    """Expand URL shortcuts to full dist-git URLs."""
    if url.startswith('fedora/'):
        package = url.removeprefix('fedora/')
        return f'https://src.fedoraproject.org/rpms/{package}.git'
    return url


def dirs_equal(dir1: Path, dir2: Path) -> bool:
    """Recursively compare two directories."""
    comparison = filecmp.dircmp(dir1, dir2)

    if comparison.left_only or comparison.right_only or comparison.diff_files:
        return False

    for subdir in comparison.common_dirs:
        if not dirs_equal(dir1 / subdir, dir2 / subdir):
            return False

    return True


def branch_to_dist_tag(branch: str, rawhide_version: int) -> str:
    """Convert a branch name to its dist tag."""
    if branch == 'rawhide':
        return f'fc{rawhide_version}'
    elif branch.startswith('f'):
        # f40, f41, etc. -> fc40, fc41
        return f'fc{branch[1:]}'
    elif match := re.match(r'^c(\d+)s$', branch):
        # c9s, c10s -> el9, el10
        return f'el{match.group(1)}'
    raise ValueError(f"Unknown branch format: {branch}")


def check_koji_build(package_name: str, version: str, release: str, expected_commit: str, dist_tag: str) -> bool:
    """Check if a build exists in Koji and matches the expected commit."""
    # Construct NVR
    nvr = f'{package_name}-{version}-{release}.{dist_tag}'

    server = xmlrpc.client.ServerProxy('https://koji.fedoraproject.org/kojihub')
    build_result = server.getBuild(nvr)

    if not build_result:
        logging.info("Build %s not found in Koji", nvr)
        return False

    build = cast(KojiBuild, build_result)

    # Extract commit from source field
    source = build.get('source', '')
    if '#' not in source:
        logging.warning("Build %s has no commit in source: %s", nvr, source)
        return False

    commit = source.split('#')[1]

    # Check if commit matches
    if commit != expected_commit:
        logging.warning("Build %s has different commit: %s (expected %s)",
                      nvr, commit[:8], expected_commit[:8])
        return False

    # Check build state (1 = COMPLETE)
    if build.get('state') != 1:
        logging.info("Build %s is not complete (state=%s)", nvr, build.get('state'))
        return False

    logging.info("Build %s found in Koji (commit %s)", nvr, commit[:8])
    return True


def is_package_unmodified(package_name: str, metadata: PackageMetadata, upstream_dir: Path) -> bool:
    """Check if package directory matches the imported version."""
    package_dir = ROOT_DIR / 'rpms' / package_name

    # Clone upstream_dir as we are going to checkout specific sha and remove .git/ for comparison
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir) / package_name
        run_git('clone', '--branch', metadata['branch'], '--single-branch',
                str(upstream_dir), str(temp_dir))
        run_git('checkout', metadata['sha'], cwd=temp_dir)
        shutil.rmtree(temp_dir / '.git')

        return dirs_equal(package_dir, temp_dir)


def import_(url: str, branch: str, dry_run: bool = False) -> None:
    """Import a new dist-git package."""
    url = expand_url_shortcut(url)
    package_name = Path(url).stem
    package_dir = ROOT_DIR / 'rpms' / package_name

    if package_dir.exists():
        sys.exit(f"ERROR: Package {package_name} already exists at rpms/{package_name}/\n"
                 f"       Use 'sync' or 'update' command to update existing packages.")

    logging.info("Importing %s from %s (branch: %s)", package_name, url, branch)

    logging.info("Cloning from %s...", url)
    run_git('clone', '--branch', branch, '--depth=1', '--single-branch', url, str(package_dir))

    sha = run_git('rev-parse', 'HEAD', cwd=package_dir).stdout.strip()
    logging.info("Latest commit: %s", sha)

    # Parse spec file for version/release
    version, release = parse_spec_version(package_dir)
    logging.info("Version: %s-%s", version, release)

    # We can't have sub .git directories in our repo
    shutil.rmtree(package_dir / '.git')

    # Update import.json
    imports[package_name] = {
        'source': url,
        'branch': branch,
        'sha': sha,
        'version': version,
        'release': release,
    }
    save_import_json()
    logging.info("Successfully imported %s", package_name)

    # Commit the changes
    if not dry_run:
        run_git('add', f'rpms/{package_name}', 'import.json', cwd=ROOT_DIR)
        commit_msg = f"Import {package_name}-{version}-{release}\n\nBranch: {branch}\nUpstream: {sha}"
        run_git('commit', '-m', commit_msg, cwd=ROOT_DIR)


def update(package_name: str, rawhide_version: int = 0, skip_build_check: bool = False, sync: bool = False,
           dry_run: bool = False) -> None:
    """Update a single package from upstream."""
    if package_name not in imports:
        sys.exit(f"ERROR: Package {package_name} not in import.json")

    package_dir = ROOT_DIR / 'rpms' / package_name
    assert package_dir.exists()

    metadata = imports[package_name]

    # Check latest commit from upstream with ls-remote (fast, no clone needed)
    result = run_git('ls-remote', metadata['source'], metadata['branch'])
    if not result.stdout.strip():
        sys.exit(f"ERROR: Unable to query remote for {package_name}")
    latest_sha = result.stdout.split()[0]

    # Check if there's an update
    if latest_sha == metadata['sha']:
        if sync:
            sys.exit(f"ERROR: Package {package_name} is already at upstream {latest_sha[:8]}")
        logging.info("Skipping %s: already up-to-date", package_name)
        return

    # There's an update available - clone upstream to check if it was built in Koji
    with tempfile.TemporaryDirectory() as tmpdir:
        upstream_dir = Path(tmpdir) / package_name
        run_git('clone', '--branch', metadata['branch'], '--single-branch',
                metadata['source'], str(upstream_dir))

        # Parse spec file to get version-release
        version, release = parse_spec_version(upstream_dir)

        # Check if this version-release was built in Koji; syncing is a human thing,
        # assume they know what they are doing
        if not skip_build_check and not sync:
            dist_tag = branch_to_dist_tag(metadata['branch'], rawhide_version)
            if not check_koji_build(package_name, version, release, latest_sha, dist_tag):
                logging.info("Skipping %s: %s-%s not built in Koji", package_name, version, release)
                return

        # Check if local package has modifications (only in update mode)
        if not sync and not is_package_unmodified(package_name, metadata, upstream_dir):
            logging.info("Skipping %s: package has local modifications", package_name)
            return

        # Update is available and conditions met - apply it
        action = "Syncing" if sync else "Updating"
        logging.info("%s %s: %s -> %s", action, package_name, metadata['sha'][:8], latest_sha[:8])
        shutil.rmtree(package_dir)
        shutil.rmtree(upstream_dir / '.git')
        shutil.copytree(upstream_dir, package_dir)

        logging.info("  Updated to %s-%s", version, release)

        # Update import.json
        imports[package_name]['sha'] = latest_sha
        imports[package_name]['version'] = version
        imports[package_name]['release'] = release
        save_import_json()

        # Commit the changes
        if not dry_run:
            run_git('add', f'rpms/{package_name}', 'import.json', cwd=ROOT_DIR)
            verb = "Sync" if sync else "Update"
            commit_msg = f"{verb} {package_name} to {version}-{release}\n\nUpstream: {latest_sha}"
            run_git('commit', '-m', commit_msg, cwd=ROOT_DIR)


def check_git_config() -> None:
    """Check that git user.name and user.email are configured."""

    name_result = run_git('config', 'user.name', cwd=ROOT_DIR, check=False)
    email_result = run_git('config', 'user.email', cwd=ROOT_DIR, check=False)

    if (name_result.returncode != 0 or not name_result.stdout.strip() or
        email_result.returncode != 0 or not email_result.stdout.strip()):
        sys.exit(
            "ERROR: Please configure git:\n"
            "   git config user.name 'Your Name'\n"
            "   git config user.email 'you@example.com'"
        )


def main() -> None:
    global imports

    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    # Load import.json
    imports = cast(dict[str, PackageMetadata], json.loads(IMPORT_JSON.read_text()))

    parser = argparse.ArgumentParser(
        description='Import and sync dist-git packages',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Import bash from rawhide (using shortcut)
  %(prog)s import fedora/bash

  # Import glibc from Fedora 42 (using full URL), don't commit the update
  %(prog)s --dry-run import --branch f42 https://src.fedoraproject.org/rpms/glibc.git

  # Import from CentOS Stream
  %(prog)s import --branch c10s https://gitlab.com/redhat/centos-stream/rpms/kernel.git
"""
    )

    parser.add_argument('--dry-run', action='store_true',
                        help='Do not commit changes to git')

    subparsers = parser.add_subparsers(dest='command', required=True)

    # import command
    import_parser = subparsers.add_parser('import', help='Import a new dist-git package')
    import_parser.add_argument('url', help='dist-git repository URL or shortcut (e.g., fedora/bash)')
    import_parser.add_argument('--branch', default='rawhide',
                               help='Branch to import from (default: rawhide)')

    # update command
    update_parser = subparsers.add_parser('update', help='Update packages from upstream if unmodified')
    update_parser.add_argument('package', nargs='?', default=None,
                              help='Package name to update (default: all packages)')
    # FIXME: This is awkward, but expensive to detect -- we'd need to cache this somewhere
    update_parser.add_argument('--rawhide-version', type=int, required=True,
                              help='Fedora version number for rawhide (e.g., 43)')
    update_parser.add_argument('--skip-build-check', action='store_true',
                              help='Skip Koji build verification (for testing)')

    # sync command
    sync_parser = subparsers.add_parser('sync', help='Force-sync package to upstream (discards local changes)')
    sync_parser.add_argument('package', help='Package name to sync')

    args = parser.parse_args()

    if not args.dry_run:
        check_git_config()

    if args.command == 'import':
        import_(args.url, args.branch, args.dry_run)
    elif args.command == 'update':
        if args.package:
            packages = [args.package]
        else:
            packages = list(imports.keys())

        for pkg in packages:
            update(pkg, args.rawhide_version, args.skip_build_check, dry_run=args.dry_run)
    elif args.command == 'sync':
        update(args.package, sync=True, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
