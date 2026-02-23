"""
Spec file update utilities.

This module provides functionality for updating RPM spec files using
the packit/specfile library.
"""

import datetime
import logging
import os
import pwd
import re
import socket
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_author_info() -> tuple[str, str]:
    """Get author name and email for changelog entries.

    Tries multiple sources in order:
    1. Git config (user.name, user.email)
    2. Environment variables (GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL)
    3. System user info

    Returns:
        Tuple of (name, email)
    """
    name = None
    email = None

    # Try git config
    try:
        result = subprocess.run(
            ["git", "config", "--get", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            name = result.stdout.strip()

        result = subprocess.run(
            ["git", "config", "--get", "user.email"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            email = result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass

    # Try environment variables
    if not name:
        name = os.environ.get("GIT_AUTHOR_NAME")
    if not email:
        email = os.environ.get("GIT_AUTHOR_EMAIL")

    # Fall back to system info
    if not name:
        try:
            name = (
                pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
                or pwd.getpwuid(os.getuid()).pw_name
            )
        except (KeyError, IndexError):
            name = "Unknown"

    if not email:
        try:
            email = f"{pwd.getpwuid(os.getuid()).pw_name}@{socket.gethostname()}"
        except (KeyError, OSError):
            email = "unknown@localhost"

    return name, email


class SpecUpdater:
    """Updates RPM spec files using the specfile library.

    This class wraps the packit/specfile library to provide convenient
    methods for common spec file modifications.
    """

    def __init__(self, spec_path: Path):
        """Initialize the spec updater.

        Args:
            spec_path: Path to the spec file.
        """
        self.spec_path = spec_path
        self._spec = None

    def _get_spec(self):
        """Lazy-load the spec file."""
        if self._spec is None:
            try:
                from specfile import Specfile
            except ImportError:
                # Try alternative import path
                from specfile.specfile import Specfile

            self._spec = Specfile(
                str(self.spec_path), sourcedir=str(self.spec_path.parent)
            )
        return self._spec

    def update_tag(self, tag_name: str, value: str) -> bool:
        """Update a spec file tag.

        Args:
            tag_name: Name of the tag (e.g., 'Version', 'Release', 'License')
            value: New value for the tag

        Returns:
            True if the tag was updated, False if not found.
        """
        spec = self._get_spec()

        try:
            with spec.tags() as tags:
                tag = getattr(tags, tag_name.lower(), None)
                if tag is not None:
                    tag.value = value
                    logger.info(f"Updated {tag_name} to {value}")
                    return True
                else:
                    logger.warning(f"Tag not found: {tag_name}")
                    return False
        except Exception as e:
            logger.error(f"Failed to update tag {tag_name}: {e}")
            return False

    def update_macro(self, macro_name: str, value: str) -> bool:
        """Update a global macro definition.

        Args:
            macro_name: Name of the macro (without %)
            value: New value for the macro

        Returns:
            True if the macro was updated, False if not found.
        """
        spec = self._get_spec()

        # Read the spec file content
        content = self.spec_path.read_text()

        # Pattern to match %global or %define macro
        pattern = rf"^(%(?:global|define)\s+{re.escape(macro_name)}\s+)(.*)$"

        new_content, count = re.subn(
            pattern, rf"\g<1>{value}", content, flags=re.MULTILINE
        )

        if count > 0:
            self.spec_path.write_text(new_content)
            # Reload spec to pick up changes
            self._spec = None
            logger.info(f"Updated macro {macro_name} to {value}")
            return True
        else:
            logger.warning(f"Macro not found: {macro_name}")
            return False

    def replace_pattern(
        self,
        pattern: str,
        replacement: str,
        count: int = 0,
    ) -> bool:
        """Replace text matching a regex pattern.

        Args:
            pattern: Regex pattern to match.
            replacement: Replacement string (can use backreferences like \\1).
            count: Max replacements (0 = all).

        Returns:
            True if at least one replacement was made.
        """
        content = self.spec_path.read_text()

        new_content, num_replacements = re.subn(
            pattern, replacement, content, count=count, flags=re.MULTILINE
        )

        if num_replacements > 0:
            self.spec_path.write_text(new_content)
            self._spec = None
            logger.info(f"Replaced {num_replacements} occurrence(s) of '{pattern}'")
            return True
        else:
            logger.warning(f"Pattern not found: {pattern}")
            return False

    def replace_block(
        self,
        start_marker: str,
        end_marker: str,
        new_content: str,
        include_markers: bool = True,
    ) -> bool:
        """Replace a block of text between markers.

        This is useful for updating sections like bundled Provides that
        span multiple lines.

        Args:
            start_marker: Regex pattern matching the start of the block.
            end_marker: Regex pattern matching the end of the block.
            new_content: New content to replace the block with.
            include_markers: If True, include the markers in the replacement.

        Returns:
            True if the block was found and replaced.
        """
        content = self.spec_path.read_text()

        # Build the pattern to match the block
        # We always capture the markers, then decide whether to keep them
        pattern = rf"({start_marker})(.*?)({end_marker})"
        if include_markers:
            # Keep markers, replace content between them
            replacement = rf"\1\n{new_content}\n\3"
        else:
            # Replace everything including markers
            replacement = new_content

        new_content_text, count = re.subn(
            pattern, replacement, content, count=1, flags=re.DOTALL | re.MULTILINE
        )

        if count > 0:
            self.spec_path.write_text(new_content_text)
            # Reload spec to pick up changes
            self._spec = None
            logger.info(f"Replaced block between '{start_marker}' and '{end_marker}'")
            return True
        else:
            logger.warning(f"Block not found: {start_marker} ... {end_marker}")
            return False

    def add_changelog_entry(
        self,
        entries: list[str],
        author: Optional[str] = None,
        email: Optional[str] = None,
        timestamp: Optional[datetime.date] = None,
    ) -> bool:
        """Add a changelog entry to the spec file.

        Args:
            entries: List of changelog entry lines (each should start with '- ')
            author: Author name (auto-detected if not provided)
            email: Author email (auto-detected if not provided)
            timestamp: Entry date (defaults to today)

        Returns:
            True if successful.
        """
        spec = self._get_spec()

        # Get author info if not provided
        if not author or not email:
            detected_name, detected_email = get_author_info()
            author = author or detected_name
            email = email or detected_email

        # Use today's date if not provided
        if timestamp is None:
            timestamp = datetime.date.today()

        # Get version and release from spec
        version = spec.version
        release = spec.release

        # Format the changelog entry
        # Format: * Day Mon DD YYYY Author <email> - version-release
        date_str = timestamp.strftime("%a %b %d %Y")
        header = f"* {date_str} {author} <{email}> - {version}-{release}"

        try:
            with spec.changelog() as changelog:
                # Prepend new entry
                from specfile.changelog import ChangelogEntry

                new_entry = ChangelogEntry.assemble(
                    timestamp=timestamp,
                    author=author,
                    email=email,
                    content=entries,
                    evr=f"{version}-{release}",
                )
                changelog.insert(0, new_entry)

            logger.info(f"Added changelog entry with {len(entries)} item(s)")
            return True

        except Exception as e:
            logger.error(f"Failed to add changelog entry: {e}")
            return False

    def save(self) -> None:
        """Save changes to the spec file."""
        if self._spec is not None:
            self._spec.save()
            logger.info(f"Saved changes to {self.spec_path}")


def update_spec_from_config(
    spec_path: Path,
    spec_updates: list[dict],
    changelog_entries: Optional[list[dict]] = None,
    dry_run: bool = False,
) -> bool:
    """Update a spec file based on configuration.

    Args:
        spec_path: Path to the spec file.
        spec_updates: List of updates from YAML config.
        changelog_entries: Optional changelog entries from YAML config.
        dry_run: If True, don't actually make changes.

    Returns:
        True if all updates succeeded.
    """
    if not spec_path.exists():
        logger.error(f"Spec file not found: {spec_path}")
        return False

    if dry_run:
        logger.info(f"[DRY-RUN] Would update spec file: {spec_path}")
        for update in spec_updates:
            if "tag" in update:
                logger.info(
                    f"  [DRY-RUN] Would set {update['tag']} = {update['value']}"
                )
            elif "macro" in update:
                logger.info(
                    f"  [DRY-RUN] Would set %{update['macro']} = {update['value']}"
                )
            elif "block" in update:
                logger.info(f"  [DRY-RUN] Would replace block '{update['block']}'")
            elif "replace" in update:
                logger.info(
                    f"  [DRY-RUN] Would replace '{update['replace']}' with '{update['with']}'"
                )
        if changelog_entries:
            logger.info(
                f"  [DRY-RUN] Would add {len(changelog_entries)} changelog entries"
            )
        return True

    updater = SpecUpdater(spec_path)
    success = True

    # Separate updates by type - block/replace updates must happen after specfile saves
    tag_macro_updates = []
    post_save_updates = []

    for update in spec_updates:
        if "block" in update or "replace" in update:
            post_save_updates.append(update)
        else:
            tag_macro_updates.append(update)

    # Apply tag/macro updates first (via specfile library)
    for update in tag_macro_updates:
        if "tag" in update:
            if not updater.update_tag(update["tag"], update["value"]):
                success = False
        elif "macro" in update:
            if not updater.update_macro(update["macro"], update["value"]):
                success = False

    # Add changelog entries
    if changelog_entries:
        entry_lines = []
        for entry in changelog_entries:
            text = entry.get("entry", "")
            if not text.startswith("- "):
                text = f"- {text}"
            entry_lines.append(text)

        if entry_lines:
            if not updater.add_changelog_entry(entry_lines):
                success = False

    # Save specfile changes before block/replace updates
    updater.save()

    # Now apply block/replace updates (direct file manipulation)
    for update in post_save_updates:
        if "block" in update:
            if not updater.replace_block(
                start_marker=update["start"],
                end_marker=update["end"],
                new_content=update["content"],
                include_markers=update.get("include_markers", True),
            ):
                success = False
        elif "replace" in update:
            if not updater.replace_pattern(
                pattern=update["replace"],
                replacement=update["with"],
                count=update.get("count", 0),
            ):
                success = False

    return success
