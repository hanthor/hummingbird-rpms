"""
Run step implementation.

This step executes arbitrary shell commands.
"""

import logging
from typing import Any

from source_gen.steps import register_step
from source_gen.steps.base import Step, StepResult, ValidationError

logger = logging.getLogger(__name__)


@register_step("run")
class RunStep(Step):
    """Execute arbitrary shell commands.

    This step is for running custom commands that don't fit into
    other step types. Use sparingly - prefer specific steps when available.

    Parameters:
        command: Shell command(s) to execute
        dependencies: Optional list of DNF packages to install

    Example:
        - name: Generate version file
          uses: run
          with:
            command: |
              echo "{version}" > project/VERSION
              ./scripts/generate-manifest.sh
            dependencies:
              - make
              - gcc
    """

    def get_dependencies(self) -> list[str]:
        # User can specify custom dependencies
        return self.get_param("dependencies", [])

    def validate(self) -> list[ValidationError]:
        errors = self._validate_required("command")

        deps = self.get_param("dependencies", [])
        if deps and not isinstance(deps, list):
            errors.append(
                ValidationError("dependencies", "Must be a list of package names")
            )

        return errors

    def execute(self) -> StepResult:
        command = self.require_param("command")

        logger.info("Executing custom command")
        logger.debug(f"Command: {command}")

        # Run the command with any specified dependencies
        result = self._run_container_command(command)

        if result.success:
            result.message = "Custom command completed successfully"

        return result
