"""
Git checkout step implementation.

This step clones a git repository at a specific ref (tag, branch, or commit).
"""

import logging
from typing import Any

from source_gen.steps import register_step
from source_gen.steps.base import Step, StepResult, ValidationError

logger = logging.getLogger(__name__)


@register_step("git-checkout")
class GitCheckoutStep(Step):
    """Clone a git repository at a specific ref.

    Parameters:
        repository: URL of the git repository to clone
        ref: Git ref to checkout (tag, branch, or commit SHA)
        dest: Destination directory name
        expected-sha: Optional commit SHA to verify after checkout

    Example:
        - name: Clone repository
          uses: git-checkout
          with:
            repository: https://github.com/example/project
            ref: v1.2.3
            dest: project-1.2.3
            expected-sha: abc123def456
    """

    def get_dependencies(self) -> list[str]:
        return ["git"]

    def validate(self) -> list[ValidationError]:
        return self._validate_required("repository", "ref", "dest")

    def execute(self) -> StepResult:
        repository = self.require_param("repository")
        ref = self.require_param("ref")
        dest = self.require_param("dest")
        expected_sha = self.get_param("expected-sha")

        logger.info(f"Cloning {repository} at {ref} to {dest}")

        # Build the clone command
        # Use --depth=1 for efficiency, but full clone if we need to verify SHA
        clone_cmd = f"git clone --branch '{ref}' --single-branch"
        if not expected_sha:
            clone_cmd += " --depth=1"
        clone_cmd += f" '{repository}' '{dest}'"

        # If expected SHA is provided, verify after clone
        if expected_sha:
            clone_cmd += (
                f" && cd '{dest}' && git rev-parse HEAD | grep -q '^{expected_sha}'"
            )
            clone_cmd += f" || (echo 'ERROR: Expected SHA {expected_sha} but got:' && git rev-parse HEAD && exit 1)"

        result = self._run_container_command(clone_cmd)

        if result.success:
            result.artifacts.append(dest)
            result.message = f"Cloned {repository} at {ref} to {dest}"

        return result


@register_step("git-clone")
class GitCloneStep(GitCheckoutStep):
    """Alias for git-checkout step.

    This provides compatibility with configs that use 'git-clone' instead of 'git-checkout'.
    """

    pass
