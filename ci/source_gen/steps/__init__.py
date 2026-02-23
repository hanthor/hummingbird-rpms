"""
Pipeline step registry and utilities.

This module provides the step registration mechanism and utilities
for discovering and instantiating step implementations.
"""

from typing import Type

from source_gen.steps.base import Step, StepContext, StepResult

# Registry mapping step names to their implementation classes
STEP_REGISTRY: dict[str, Type[Step]] = {}


def register_step(name: str):
    """Decorator to register a step class.

    Usage:
        @register_step("git-checkout")
        class GitCheckoutStep(Step):
            ...

    Args:
        name: The step name as it appears in YAML (e.g., "git-checkout", "go/vendor")
    """

    def decorator(cls: Type[Step]) -> Type[Step]:
        if name in STEP_REGISTRY:
            raise ValueError(f"Step '{name}' is already registered")
        STEP_REGISTRY[name] = cls
        return cls

    return decorator


def get_step(name: str) -> Type[Step]:
    """Get a step class by name.

    Args:
        name: The step name to look up.

    Returns:
        The step class.

    Raises:
        ValueError: If the step is not registered.
    """
    if name not in STEP_REGISTRY:
        available = ", ".join(sorted(STEP_REGISTRY.keys()))
        raise ValueError(
            f"Unknown step type: '{name}'. "
            f"Available steps: {available or 'none registered'}"
        )
    return STEP_REGISTRY[name]


def list_steps() -> list[str]:
    """List all registered step names.

    Returns:
        Sorted list of registered step names.
    """
    return sorted(STEP_REGISTRY.keys())


def create_step(name: str, params: dict, context: StepContext) -> Step:
    """Create a step instance.

    Args:
        name: The step type name.
        params: The step parameters from YAML.
        context: The execution context.

    Returns:
        Instantiated step ready for execution.
    """
    step_class = get_step(name)
    return step_class(params, context)


# Import step modules to trigger registration
# These imports must be at the end to avoid circular imports
def _register_all_steps() -> None:
    """Import all step modules to register them."""
    # Import each step module - the @register_step decorators will
    # automatically register the steps when the modules are imported
    from source_gen.steps import git  # noqa: F401
    from source_gen.steps import fetch  # noqa: F401
    from source_gen.steps import patch  # noqa: F401
    from source_gen.steps import run  # noqa: F401
    from source_gen.steps import go  # noqa: F401
    from source_gen.steps import cargo  # noqa: F401


# Defer registration until steps are actually needed
_steps_registered = False


def ensure_steps_registered() -> None:
    """Ensure all step modules have been imported and registered."""
    global _steps_registered
    if not _steps_registered:
        _register_all_steps()
        _steps_registered = True


__all__ = [
    "Step",
    "StepContext",
    "StepResult",
    "STEP_REGISTRY",
    "register_step",
    "get_step",
    "list_steps",
    "create_step",
    "ensure_steps_registered",
]
