"""
Checksum utilities for sources file handling.

This module handles computing SHA512 checksums and updating the
'sources' file in RPM package directories.
"""

import hashlib
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# BSD-style checksum format used by Fedora/CentOS
# Example: SHA512 (filename.tar.gz) = abc123...
BSD_CHECKSUM_PATTERN = re.compile(
    r"^(?P<algo>\w+)\s+\((?P<filename>[^)]+)\)\s+=\s+(?P<hash>[a-fA-F0-9]+)$"
)


def compute_sha512(file_path: Path) -> str:
    """Compute the SHA512 checksum of a file.

    Args:
        file_path: Path to the file.

    Returns:
        Lowercase hex-encoded SHA512 hash.

    Raises:
        FileNotFoundError: If the file doesn't exist.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    sha512 = hashlib.sha512()

    with open(file_path, "rb") as f:
        # Read in chunks to handle large files
        for chunk in iter(lambda: f.read(8192), b""):
            sha512.update(chunk)

    return sha512.hexdigest()


def format_bsd_checksum(filename: str, checksum: str, algorithm: str = "SHA512") -> str:
    """Format a checksum in BSD style.

    Args:
        filename: The filename (basename only).
        checksum: The hex-encoded checksum.
        algorithm: The algorithm name (default: SHA512).

    Returns:
        BSD-style checksum string.
    """
    return f"{algorithm} ({filename}) = {checksum}"


def parse_sources_file(sources_path: Path) -> dict[str, str]:
    """Parse a sources file into a dictionary.

    Args:
        sources_path: Path to the sources file.

    Returns:
        Dictionary mapping filenames to their checksums.
    """
    checksums: dict[str, str] = {}

    if not sources_path.exists():
        return checksums

    with open(sources_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            match = BSD_CHECKSUM_PATTERN.match(line)
            if match:
                filename = match.group("filename")
                checksum = match.group("hash")
                checksums[filename] = checksum
            else:
                logger.warning(f"Could not parse sources line: {line}")

    return checksums


def update_sources_file(
    sources_path: Path,
    files: dict[str, str],
    dry_run: bool = False,
) -> bool:
    """Update the sources file with new checksums.

    This replaces existing checksums for the given files and adds
    new entries as needed. Other entries are preserved.

    Args:
        sources_path: Path to the sources file.
        files: Dictionary mapping filenames to checksums.
        dry_run: If True, don't actually write the file.

    Returns:
        True if successful.
    """
    # Read existing checksums
    existing = parse_sources_file(sources_path)

    # Merge with new checksums
    existing.update(files)

    # Sort by filename for consistent output
    sorted_files = sorted(existing.items())

    # Generate output lines
    lines = [
        format_bsd_checksum(filename, checksum) for filename, checksum in sorted_files
    ]
    content = "\n".join(lines) + "\n"

    if dry_run:
        logger.info(f"[DRY-RUN] Would update {sources_path}")
        for line in lines:
            logger.info(f"  {line}")
        return True

    try:
        sources_path.parent.mkdir(parents=True, exist_ok=True)
        sources_path.write_text(content)
        logger.info(f"Updated sources file: {sources_path}")
        return True
    except OSError as e:
        logger.error(f"Failed to write sources file: {e}")
        return False


def compute_and_update_sources(
    package_dir: Path,
    archive_files: list[Path],
    dry_run: bool = False,
    replace: bool = True,
) -> bool:
    """Compute checksums for archives and update the sources file.

    Args:
        package_dir: The package directory (rpms/<package>/).
        archive_files: List of archive file paths.
        dry_run: If True, don't actually write anything.
        replace: If True, replace the sources file entirely.
                 If False, merge with existing entries.

    Returns:
        True if successful.
    """
    sources_path = package_dir / "sources"

    checksums: dict[str, str] = {}

    for archive_path in archive_files:
        if not archive_path.exists():
            logger.error(f"Archive file not found: {archive_path}")
            return False

        logger.info(f"Computing checksum for {archive_path.name}")
        checksum = compute_sha512(archive_path)
        checksums[archive_path.name] = checksum

        if not dry_run:
            logger.debug(f"SHA512 ({archive_path.name}) = {checksum}")

    if replace:
        # Replace sources file entirely with new checksums
        lines = [
            format_bsd_checksum(filename, checksum)
            for filename, checksum in sorted(checksums.items())
        ]
        content = "\n".join(lines) + "\n"

        if dry_run:
            logger.info(f"[DRY-RUN] Would write {sources_path}")
            for line in lines:
                logger.info(f"  {line}")
            return True

        try:
            sources_path.parent.mkdir(parents=True, exist_ok=True)
            sources_path.write_text(content)
            logger.info(f"Updated sources file: {sources_path}")
            return True
        except OSError as e:
            logger.error(f"Failed to write sources file: {e}")
            return False
    else:
        return update_sources_file(sources_path, checksums, dry_run)


def verify_checksum(file_path: Path, expected_checksum: str) -> bool:
    """Verify that a file matches an expected checksum.

    Args:
        file_path: Path to the file.
        expected_checksum: Expected SHA512 checksum.

    Returns:
        True if the checksum matches.
    """
    try:
        actual = compute_sha512(file_path)
        return actual.lower() == expected_checksum.lower()
    except FileNotFoundError:
        return False
