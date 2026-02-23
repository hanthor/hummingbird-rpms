"""
Fedora upstream tracking utilities.

This module provides functionality for tracking and syncing with
Fedora dist-git repositories.
"""

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import yaml

from source_gen.config import SourceGenConfig

logger = logging.getLogger(__name__)


def fetch_upstream_spec(
    source_url: str,
    branch: str,
    package_name: str,
    target_sha: Optional[str] = None,
) -> Optional[str]:
    """Fetch a spec file from upstream Fedora dist-git.

    Args:
        source_url: URL of the upstream git repository.
        branch: Branch to fetch from.
        package_name: Name of the package (for spec file name).
        target_sha: Optional specific commit to fetch.

    Returns:
        The spec file contents, or None on failure.
    """
    with tempfile.TemporaryDirectory(prefix="source-gen-upstream-") as tmpdir:
        tmpdir = Path(tmpdir)

        # Clone the repository (shallow for speed)
        clone_cmd = [
            "git",
            "clone",
            "--branch",
            branch,
            "--single-branch",
            "--depth",
            "100" if target_sha else "1",
            source_url,
            str(tmpdir / "repo"),
        ]

        try:
            subprocess.run(clone_cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to clone upstream: {e.stderr}")
            return None

        repo_dir = tmpdir / "repo"

        # Checkout specific SHA if requested
        if target_sha:
            try:
                subprocess.run(
                    ["git", "checkout", target_sha],
                    cwd=repo_dir,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to checkout {target_sha}: {e.stderr}")
                return None

        # Read the spec file
        spec_path = repo_dir / f"{package_name}.spec"
        if not spec_path.exists():
            logger.error(f"Spec file not found: {spec_path}")
            return None

        return spec_path.read_text()


def get_upstream_head(source_url: str, branch: str) -> Optional[str]:
    """Get the current HEAD SHA of an upstream branch.

    Args:
        source_url: URL of the upstream git repository.
        branch: Branch to check.

    Returns:
        The HEAD commit SHA, or None on failure.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", source_url, f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )

        if result.stdout:
            return result.stdout.split()[0]

        return None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error(f"Failed to get upstream HEAD: {e}")
        return None


def compute_upstream_diff(config: SourceGenConfig) -> Optional[str]:
    """Compute the diff between last synced commit and upstream HEAD.

    Args:
        config: Source generation configuration with tracking info.

    Returns:
        Git log output showing changes, or None on failure.
    """
    tracking = config.get("tracking")
    if not tracking:
        logger.error("No tracking configuration")
        return None

    source_url = tracking["source"]
    branch = tracking["branch"]
    last_synced = tracking.get("last_synced_sha")

    if not last_synced:
        logger.warning("No last_synced_sha in tracking config")
        return "No sync history - run upstream-update to establish baseline"

    with tempfile.TemporaryDirectory(prefix="source-gen-diff-") as tmpdir:
        tmpdir = Path(tmpdir)

        # Clone the repository
        try:
            subprocess.run(
                ["git", "clone", "--branch", branch, source_url, str(tmpdir / "repo")],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to clone: {e.stderr}")
            return None

        repo_dir = tmpdir / "repo"

        # Get the diff - track all files, not just spec
        try:
            result = subprocess.run(
                [
                    "git",
                    "log",
                    "--oneline",
                    f"{last_synced}..HEAD",
                ],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=True,
            )

            if not result.stdout.strip():
                return None  # No changes

            # Also get the actual diff for all files
            diff_result = subprocess.run(
                ["git", "diff", last_synced, "HEAD"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=True,
            )

            output = f"Commits since last sync ({last_synced[:8]}):\n"
            output += result.stdout
            output += "\n\nDiff:\n"

            # Add visual spacing between file diffs
            diff_lines = diff_result.stdout.split("\n")
            padded_lines = []
            for i, line in enumerate(diff_lines):
                if line.startswith("diff ") and i > 0:
                    padded_lines.append("")
                    padded_lines.append("")
                padded_lines.append(line)

            output += "\n".join(padded_lines)

            return output

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to compute diff: {e.stderr}")
            return None


def fetch_upstream_repo(
    source_url: str,
    branch: str,
    target_sha: Optional[str] = None,
) -> Optional[Path]:
    """Clone upstream Fedora dist-git repo to a temp directory.

    Args:
        source_url: URL of the upstream git repository.
        branch: Branch to fetch from.
        target_sha: Optional specific commit to checkout.

    Returns:
        Path to the cloned repo, or None on failure.
        Caller is responsible for cleanup.
    """
    tmpdir = tempfile.mkdtemp(prefix="source-gen-upstream-")
    repo_dir = Path(tmpdir) / "repo"

    try:
        # Use deeper clone if we need a specific SHA
        depth = "100" if target_sha else "1"
        subprocess.run(
            [
                "git",
                "clone",
                "--branch",
                branch,
                "--single-branch",
                "--depth",
                depth,
                source_url,
                str(repo_dir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        # Checkout specific SHA if requested
        if target_sha:
            subprocess.run(
                ["git", "checkout", target_sha],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            )

        return repo_dir
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to clone upstream: {e.stderr}")
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
        return None


def compute_local_diff(config: SourceGenConfig, package_dir: Path) -> Optional[str]:
    """Compute the diff between local package and the tracked upstream commit.

    Compares all files (spec, patches, sources, etc.) against the
    last_synced_sha commit, showing what local modifications we've made
    since forking from Fedora.

    Args:
        config: Source generation configuration with tracking info.
        package_dir: Local package directory.

    Returns:
        Diff output, or None on failure or if identical.
    """
    tracking = config.get("tracking")
    if not tracking:
        logger.error("No tracking configuration")
        return None

    source_url = tracking["source"]
    branch = tracking["branch"]
    last_synced = tracking.get("last_synced_sha")

    if not last_synced:
        logger.warning("No last_synced_sha - comparing against current HEAD")

    if not package_dir.exists():
        logger.error(f"Local package directory not found: {package_dir}")
        return None

    # Clone upstream repo at the tracked SHA
    upstream_dir = fetch_upstream_repo(source_url, branch, last_synced)
    if upstream_dir is None:
        return None

    try:
        # Get list of files to compare (exclude .git and generated files)
        exclude_patterns = [".git", "sources", "*.pyc", "__pycache__"]

        # Build exclude args for diff
        exclude_args = []
        for pattern in exclude_patterns:
            exclude_args.extend(["--exclude", pattern])

        # Run recursive diff
        result = subprocess.run(
            [
                "diff",
                "-ruN",
                *exclude_args,
                str(upstream_dir),
                str(package_dir),
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return None  # Identical

        # Build header
        sha_display = last_synced[:12] if last_synced else "HEAD"
        header = f"Local changes from tracked commit ({sha_display}):\n\n"

        # Clean up paths in output for readability
        output = result.stdout
        output = output.replace(str(upstream_dir), "fedora")
        output = output.replace(str(package_dir), "local")

        # Add visual spacing between file diffs
        lines = output.split("\n")
        padded_lines = []
        for i, line in enumerate(lines):
            # Add blank lines before new file diff (but not at start)
            if line.startswith("diff ") and i > 0:
                padded_lines.append("")
                padded_lines.append("")
            padded_lines.append(line)

        return header + "\n".join(padded_lines)

    finally:
        # Cleanup temp directory
        import shutil

        shutil.rmtree(upstream_dir.parent)


def interactive_upstream_update(
    config: SourceGenConfig,
    config_path: Path,
    package_dir: Path,
) -> int:
    """Interactively update from upstream Fedora.

    This fetches the current upstream spec, shows the diff, and
    prompts the user to confirm updating the last_synced_sha.

    Args:
        config: Source generation configuration.
        config_path: Path to the YAML config file.
        package_dir: Local package directory.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    tracking = config.get("tracking")
    if not tracking:
        logger.error("No tracking configuration")
        return 1

    source_url = tracking["source"]
    branch = tracking["branch"]

    # Get current upstream HEAD
    upstream_head = get_upstream_head(source_url, branch)
    if not upstream_head:
        logger.error("Failed to get upstream HEAD")
        return 1

    logger.info(f"Upstream HEAD: {upstream_head[:12]}")

    # Show diff if we have history
    last_synced = tracking.get("last_synced_sha")
    if last_synced:
        diff = compute_upstream_diff(config)
        if diff:
            print("\n" + diff + "\n")
        else:
            print("No changes in upstream spec file since last sync.")

    # Show local diff
    local_diff = compute_local_diff(config, package_dir)
    if local_diff:
        print("\nLocal differences from upstream:")
        print(local_diff)
    else:
        print("\nNo local differences from upstream.")

    # Prompt for confirmation
    print(f"\nUpdate last_synced_sha to {upstream_head[:12]}?")
    print("This marks the current upstream commit as your sync point.")
    print("You should manually merge any desired changes into your local spec.")

    try:
        response = input("\nProceed? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return 1

    if response not in ("y", "yes"):
        print("Aborted.")
        return 1

    # Update the config file
    try:
        with open(config_path) as f:
            raw_config = yaml.safe_load(f)

        if "tracking" not in raw_config:
            raw_config["tracking"] = {}

        raw_config["tracking"]["last_synced_sha"] = upstream_head

        with open(config_path, "w") as f:
            yaml.safe_dump(raw_config, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Updated last_synced_sha to {upstream_head[:12]}")
        return 0

    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        return 1
