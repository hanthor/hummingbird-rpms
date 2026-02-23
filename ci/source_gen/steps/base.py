"""
Base classes for pipeline steps.

This module defines the abstract base class that all pipeline steps
must implement, along with shared context and result types.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class StepContext:
    """Execution context shared between steps.

    This provides access to common resources and state that steps
    need during execution.
    """

    # Working directory where steps execute (container mount point on host)
    workdir: Path

    # Package directory in rpms/ (for accessing patches, etc.)
    package_dir: Path

    # Package name
    package_name: str

    # Whether this is a dry-run (no actual execution)
    dry_run: bool = False

    # Container image to use for execution
    container_image: str = "registry.fedoraproject.org/fedora:43"

    # Additional context data that steps can use
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    """Result of a step execution."""

    # Whether the step succeeded
    success: bool

    # Human-readable message about what happened
    message: str

    # Standard output from the step (if any)
    stdout: str = ""

    # Standard error from the step (if any)
    stderr: str = ""

    # Artifacts produced by the step (paths relative to workdir)
    artifacts: list[str] = field(default_factory=list)

    @classmethod
    def ok(cls, message: str = "Step completed successfully", **kwargs) -> "StepResult":
        """Create a successful result."""
        return cls(success=True, message=message, **kwargs)

    @classmethod
    def error(cls, message: str, **kwargs) -> "StepResult":
        """Create a failed result."""
        return cls(success=False, message=message, **kwargs)


@dataclass
class ValidationError:
    """A parameter validation error."""

    param: str
    message: str


class Step(ABC):
    """Abstract base class for all pipeline steps.

    Each step type must implement this interface. Steps are responsible
    for validating their parameters, declaring their dependencies, and
    executing their logic.

    Example implementation:

        @register_step("my-step")
        class MyStep(Step):
            def get_dependencies(self) -> list[str]:
                return ["some-package"]

            def validate(self) -> list[ValidationError]:
                errors = []
                if 'required_param' not in self.params:
                    errors.append(ValidationError('required_param', 'is required'))
                return errors

            def execute(self) -> StepResult:
                # Do the work
                return StepResult.ok()
    """

    def __init__(self, params: dict[str, Any], context: StepContext):
        """Initialize the step.

        Args:
            params: Parameters from the YAML 'with' section.
            context: Execution context.
        """
        self.params = params or {}
        self.context = context

    @abstractmethod
    def get_dependencies(self) -> list[str]:
        """Return the DNF packages required by this step.

        These will be installed in the container before execution.

        Returns:
            List of DNF package names.
        """
        pass

    @abstractmethod
    def validate(self) -> list[ValidationError]:
        """Validate the step parameters.

        This is called before execution to catch configuration errors early.

        Returns:
            List of validation errors (empty if valid).
        """
        pass

    @abstractmethod
    def execute(self) -> StepResult:
        """Execute the step.

        This performs the actual work of the step, typically by running
        commands in a container.

        Returns:
            StepResult indicating success or failure.
        """
        pass

    def get_param(self, name: str, default: Any = None) -> Any:
        """Get a parameter value with optional default.

        Args:
            name: Parameter name.
            default: Default value if parameter is not set.

        Returns:
            Parameter value or default.
        """
        return self.params.get(name, default)

    def require_param(self, name: str) -> Any:
        """Get a required parameter value.

        Args:
            name: Parameter name.

        Returns:
            Parameter value.

        Raises:
            ValueError: If parameter is not set.
        """
        if name not in self.params:
            raise ValueError(f"Required parameter '{name}' is not set")
        return self.params[name]

    def _validate_required(self, *param_names: str) -> list[ValidationError]:
        """Helper to validate that required parameters are present.

        Args:
            *param_names: Names of required parameters.

        Returns:
            List of validation errors for missing parameters.
        """
        errors = []
        for name in param_names:
            if name not in self.params:
                errors.append(ValidationError(name, "is required"))
        return errors

    def _run_container_command(
        self,
        command: str,
        dependencies: Optional[list[str]] = None,
    ) -> StepResult:
        """Helper to run a command in the container.

        Args:
            command: Shell command to execute.
            dependencies: Additional dependencies beyond get_dependencies().

        Returns:
            StepResult from the command execution.
        """
        from source_gen.container import ContainerRunner, ContainerConfig

        if self.context.dry_run:
            return StepResult.ok(
                message=f"[DRY-RUN] Would execute: {command}",
                stdout=f"Command: {command}\nDependencies: {dependencies or self.get_dependencies()}",
            )

        # Combine step dependencies with any extras
        all_deps = list(self.get_dependencies())
        if dependencies:
            all_deps.extend(dependencies)

        config = ContainerConfig(image=self.context.container_image)
        runner = ContainerRunner(config)

        result = runner.run(
            command=command,
            workdir=self.context.workdir,
            dependencies=all_deps if all_deps else None,
        )

        if result.success:
            return StepResult.ok(
                message="Command completed successfully",
                stdout=result.stdout,
                stderr=result.stderr,
            )
        else:
            return StepResult.error(
                message=f"Command failed with exit code {result.returncode}",
                stdout=result.stdout,
                stderr=result.stderr,
            )
