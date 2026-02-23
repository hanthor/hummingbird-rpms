"""
Fetch step implementation.

This step downloads a file from a URL and verifies its checksum.
"""

import logging
from typing import Any

from source_gen.steps import register_step
from source_gen.steps.base import Step, StepResult, ValidationError

logger = logging.getLogger(__name__)


@register_step("fetch")
class FetchStep(Step):
    """Download a file from a URL with checksum verification.

    Parameters:
        url: URL to download from
        dest: Destination filename
        expected-sha256: Expected SHA256 checksum of the file

    Example:
        - name: Download source tarball
          uses: fetch
          with:
            url: https://example.com/source-1.0.tar.gz
            dest: source-1.0.tar.gz
            expected-sha256: abc123...
    """

    def get_dependencies(self) -> list[str]:
        return ["curl", "coreutils"]  # coreutils for sha256sum

    def validate(self) -> list[ValidationError]:
        errors = self._validate_required("url", "dest", "expected-sha256")

        # Validate SHA256 format
        sha256 = self.get_param("expected-sha256")
        if sha256 and len(sha256) != 64:
            errors.append(
                ValidationError(
                    "expected-sha256",
                    f"Invalid SHA256 length: expected 64 characters, got {len(sha256)}",
                )
            )

        return errors

    def execute(self) -> StepResult:
        url = self.require_param("url")
        dest = self.require_param("dest")
        expected_sha256 = self.require_param("expected-sha256")

        logger.info(f"Downloading {url} to {dest}")

        # Download the file
        download_cmd = f"curl -fsSL -o '{dest}' '{url}'"

        # Verify checksum
        verify_cmd = f"echo '{expected_sha256}  {dest}' | sha256sum -c -"

        full_cmd = f"{download_cmd} && {verify_cmd}"

        result = self._run_container_command(full_cmd)

        if result.success:
            result.artifacts.append(dest)
            result.message = f"Downloaded and verified {dest}"
        else:
            # Provide more helpful error message
            if (
                "sha256sum" in result.stderr.lower()
                or "checksum" in result.stderr.lower()
            ):
                result.message = f"Checksum verification failed for {dest}"
            elif "curl" in result.stderr.lower() or "404" in result.stderr:
                result.message = f"Failed to download {url}"

        return result
