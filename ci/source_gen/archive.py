"""
Archive creation utilities.

This module handles creating archives in various formats (.tar.gz, .tar.bz2,
.tar.xz, .zip) from source directories.
"""

import logging
import os
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Supported archive formats and their file extensions
ARCHIVE_FORMATS = {
    ".tar.gz": "gzip",
    ".tgz": "gzip",
    ".tar.bz2": "bzip2",
    ".tbz2": "bzip2",
    ".tar.xz": "xz",
    ".txz": "xz",
    ".zip": "zip",
}


@dataclass
class ArchiveSpec:
    """Specification for creating an archive."""

    source: Path  # Source directory to archive
    output: Path  # Output archive path
    prefix: str  # Directory name inside the archive
    exclude: list[str]  # Patterns to exclude


def get_archive_format(filename: str) -> Optional[str]:
    """Determine the archive format from a filename.

    Args:
        filename: The filename to check.

    Returns:
        The compression format, or None if not recognized.
    """
    for ext, fmt in ARCHIVE_FORMATS.items():
        if filename.endswith(ext):
            return fmt
    return None


def get_archive_extension(filename: str) -> Optional[str]:
    """Get the full archive extension from a filename.

    Args:
        filename: The filename to check.

    Returns:
        The extension (e.g., '.tar.gz'), or None if not recognized.
    """
    for ext in ARCHIVE_FORMATS:
        if filename.endswith(ext):
            return ext
    return None


def derive_prefix_from_filename(filename: str) -> str:
    """Derive the archive prefix (root directory) from the filename.

    This removes the archive extension to get the expected directory name.
    For example: 'project-1.2.3.tar.gz' -> 'project-1.2.3'

    Args:
        filename: The archive filename.

    Returns:
        The derived prefix.
    """
    ext = get_archive_extension(filename)
    if ext:
        return filename[: -len(ext)]
    return filename


def should_exclude(path: str, exclude_patterns: list[str]) -> bool:
    """Check if a path should be excluded based on patterns.

    Args:
        path: The relative path to check.
        exclude_patterns: List of patterns to exclude.

    Returns:
        True if the path should be excluded.
    """
    for pattern in exclude_patterns:
        # Remove trailing slash from pattern for comparison
        pattern_clean = pattern.rstrip("/")

        # Check if path starts with pattern (directory match)
        if path == pattern_clean or path.startswith(pattern_clean + "/"):
            return True

        # Check if any path component matches the pattern
        path_parts = path.split("/")
        if pattern_clean in path_parts:
            return True

    return False


def create_archive(
    source_dir: Path,
    output_path: Path,
    prefix: Optional[str] = None,
    exclude: Optional[list[str]] = None,
    dry_run: bool = False,
) -> bool:
    """Create an archive from a source directory.

    Args:
        source_dir: Directory to archive.
        output_path: Path for the output archive.
        prefix: Directory name inside the archive. If None, derived from output filename.
        exclude: List of patterns to exclude.
        dry_run: If True, don't actually create the archive.

    Returns:
        True if successful, False otherwise.
    """
    # Determine the format first (needed for dry-run output)
    fmt = get_archive_format(output_path.name)
    if not fmt:
        logger.error(f"Unknown archive format: {output_path.name}")
        return False

    # Determine the prefix
    if prefix is None:
        prefix = derive_prefix_from_filename(output_path.name)

    exclude = exclude or []

    # Handle dry-run before checking source existence
    if dry_run:
        logger.info(f"[DRY-RUN] Would create {fmt} archive: {output_path.name}")
        logger.info(f"[DRY-RUN]   Source: {source_dir}, Prefix: {prefix}")
        if exclude:
            logger.info(f"[DRY-RUN]   Exclude: {exclude}")
        return True

    if not source_dir.exists():
        logger.error(f"Source directory does not exist: {source_dir}")
        return False

    if not source_dir.is_dir():
        logger.error(f"Source is not a directory: {source_dir}")
        return False

    logger.info(f"Creating {fmt} archive: {output_path.name}")
    logger.debug(f"Source: {source_dir}, Prefix: {prefix}, Exclude: {exclude}")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if fmt == "zip":
            return _create_zip_archive(source_dir, output_path, prefix, exclude)
        else:
            return _create_tar_archive(source_dir, output_path, prefix, exclude, fmt)
    except Exception as e:
        logger.error(f"Failed to create archive: {e}")
        return False


def _create_tar_archive(
    source_dir: Path,
    output_path: Path,
    prefix: str,
    exclude: list[str],
    compression: str,
) -> bool:
    """Create a tar archive with the specified compression.

    Args:
        source_dir: Directory to archive.
        output_path: Path for the output archive.
        prefix: Directory name inside the archive.
        exclude: List of patterns to exclude.
        compression: Compression type ('gzip', 'bzip2', 'xz').

    Returns:
        True if successful.
    """
    # Map compression types to tarfile modes
    mode_map = {
        "gzip": "w:gz",
        "bzip2": "w:bz2",
        "xz": "w:xz",
    }

    mode = mode_map.get(compression)
    if not mode:
        logger.error(f"Unknown compression type: {compression}")
        return False

    def filter_func(tarinfo: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        """Filter function to exclude files and set prefix."""
        # Get the relative path within the source
        rel_path = tarinfo.name

        # Check exclusions
        if should_exclude(rel_path, exclude):
            return None

        # Set the archive name with prefix
        if prefix:
            if rel_path == ".":
                tarinfo.name = prefix
            else:
                tarinfo.name = f"{prefix}/{rel_path}"

        return tarinfo

    with tarfile.open(output_path, mode) as tar:
        # Walk the source directory and add files
        for root, dirs, files in os.walk(source_dir):
            # Filter out excluded directories to avoid descending into them
            dirs[:] = [d for d in dirs if not should_exclude(d, exclude)]

            for name in dirs + files:
                full_path = Path(root) / name
                rel_path = full_path.relative_to(source_dir)

                if should_exclude(str(rel_path), exclude):
                    continue

                # Create archive name with prefix
                if prefix:
                    arcname = f"{prefix}/{rel_path}"
                else:
                    arcname = str(rel_path)

                tar.add(full_path, arcname=arcname, recursive=False)

    logger.info(f"Created archive: {output_path}")
    return True


def _create_zip_archive(
    source_dir: Path,
    output_path: Path,
    prefix: str,
    exclude: list[str],
) -> bool:
    """Create a ZIP archive.

    Args:
        source_dir: Directory to archive.
        output_path: Path for the output archive.
        prefix: Directory name inside the archive.
        exclude: List of patterns to exclude.

    Returns:
        True if successful.
    """
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            # Filter out excluded directories
            dirs[:] = [d for d in dirs if not should_exclude(d, exclude)]

            for name in files:
                full_path = Path(root) / name
                rel_path = full_path.relative_to(source_dir)

                if should_exclude(str(rel_path), exclude):
                    continue

                # Create archive name with prefix
                if prefix:
                    arcname = f"{prefix}/{rel_path}"
                else:
                    arcname = str(rel_path)

                zf.write(full_path, arcname)

    logger.info(f"Created archive: {output_path}")
    return True


def create_archive_in_container(
    source_dir: str,
    output_file: str,
    prefix: str,
    exclude: list[str],
    workdir: Path,
    dry_run: bool = False,
) -> bool:
    """Create an archive using container commands.

    This is an alternative to the Python-based archive creation that runs
    inside the container for better reproducibility.

    Args:
        source_dir: Source directory name (relative to workdir).
        output_file: Output filename.
        prefix: Directory name inside the archive.
        exclude: List of patterns to exclude.
        workdir: Working directory.
        dry_run: If True, don't actually create the archive.

    Returns:
        True if successful.
    """
    from source_gen.container import run_in_container

    fmt = get_archive_format(output_file)
    if not fmt:
        logger.error(f"Unknown archive format: {output_file}")
        return False

    if dry_run:
        logger.info(f"[DRY-RUN] Would create archive {output_file} in container")
        return True

    # Build exclude arguments
    exclude_args = " ".join(f"--exclude='{p}'" for p in exclude)

    if fmt == "zip":
        # For zip, use the zip command
        cmd = f"cd '{source_dir}' && zip -r '../{output_file}' . {exclude_args}"
    else:
        # For tar, determine compression flag
        comp_flags = {
            "gzip": "-czf",
            "bzip2": "-cjf",
            "xz": "-cJf",
        }
        comp_flag = comp_flags.get(fmt, "-czf")

        # Use transform to set the prefix
        if prefix:
            transform = f"--transform='s,^\\.,{prefix},'"
        else:
            transform = ""

        cmd = f"tar {comp_flag} '{output_file}' {exclude_args} {transform} -C '{source_dir}' ."

    result = run_in_container(
        command=cmd,
        workdir=workdir,
        dependencies=["tar", "gzip", "bzip2", "xz", "zip"],
    )

    if result.success:
        logger.info(f"Created archive: {output_file}")
        return True
    else:
        logger.error(f"Failed to create archive: {result.stderr}")
        return False
