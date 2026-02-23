"""
Patch step implementation.

This step applies patch files to a directory.
"""

import logging
import shutil
from pathlib import Path
from typing import Any

from source_gen.steps import register_step
from source_gen.steps.base import Step, StepResult, ValidationError

logger = logging.getLogger(__name__)


@register_step("patch")
class PatchStep(Step):
    """Apply patch files to a directory.

    Patch files are read from the package's rpms/<package>/ directory
    and applied to the specified target directory.

    Parameters:
        directory: Target directory to apply patches to
        patches: List of patch filenames (relative to rpms/<package>/)
        strip: Number of leading path components to strip (default: 1)

    Example:
        - name: Apply Hummingbird patches
          uses: patch
          with:
            directory: project-1.2.3
            patches:
              - fix-build.patch
              - hummingbird-specific.patch
            strip: 1
    """

    def get_dependencies(self) -> list[str]:
        return ["patch"]

    def validate(self) -> list[ValidationError]:
        errors = self._validate_required("directory", "patches")

        patches = self.get_param("patches", [])
        if not isinstance(patches, list):
            errors.append(
                ValidationError("patches", "Must be a list of patch filenames")
            )
        elif len(patches) == 0:
            errors.append(ValidationError("patches", "Must contain at least one patch"))

        # Check that patch files exist in the package directory
        for patch_name in patches:
            patch_path = self.context.package_dir / patch_name
            if not patch_path.exists():
                errors.append(
                    ValidationError("patches", f"Patch file not found: {patch_path}")
                )

        return errors

    def execute(self) -> StepResult:
        directory = self.require_param("directory")
        patches = self.require_param("patches")
        strip = self.get_param("strip", 1)

        if self.context.dry_run:
            patch_list = ", ".join(patches)
            return StepResult.ok(
                message=f"[DRY-RUN] Would apply patches to {directory}: {patch_list}"
            )

        # Copy patch files to the workdir so they're accessible in the container
        workdir_patches = []
        for patch_name in patches:
            src_path = self.context.package_dir / patch_name
            dst_path = self.context.workdir / f".patches/{patch_name}"

            # Create patches directory
            dst_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy patch file
            shutil.copy2(src_path, dst_path)
            workdir_patches.append(f".patches/{patch_name}")

        logger.info(f"Applying {len(patches)} patch(es) to {directory}")

        # Build the patch command
        patch_commands = []
        for patch_file in workdir_patches:
            patch_commands.append(f"patch -d '{directory}' -p{strip} < '{patch_file}'")

        full_cmd = " && ".join(patch_commands)

        result = self._run_container_command(full_cmd)

        if result.success:
            result.message = f"Applied {len(patches)} patch(es) to {directory}"

        return result
