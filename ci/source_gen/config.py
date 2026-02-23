"""
Configuration parsing and validation for source generation.

This module handles loading YAML configuration files, validating their
structure, and resolving variable substitutions like {version}, {major}, etc.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import yaml


# Schema version this code supports
SUPPORTED_SCHEMA_VERSION = 1


class PackageInfo(TypedDict):
    """Package metadata from YAML configuration."""

    name: str
    version: str
    release: str
    license: NotRequired[str]


class TrackingInfo(TypedDict):
    """Fedora upstream tracking configuration."""

    source: str
    branch: str
    last_synced_sha: NotRequired[str]


class PipelineStep(TypedDict):
    """A single step in the source generation pipeline."""

    name: str
    uses: str
    with_: NotRequired[dict[str, Any]]


class OutputSpec(TypedDict):
    """Output archive specification."""

    type: str  # Currently only "archive" is supported
    source: str
    filename: str
    exclude: NotRequired[list[str]]
    prefix: NotRequired[str]


class ChangelogEntry(TypedDict):
    """Changelog entry to add to spec file."""

    entry: str
    cves: NotRequired[list[str]]


class SpecUpdate(TypedDict):
    """Spec file update specification."""

    tag: NotRequired[str]
    macro: NotRequired[str]
    value: str


class SourceGenConfig(TypedDict):
    """Complete source generation configuration."""

    schema_version: int
    package: PackageInfo
    tracking: NotRequired[TrackingInfo]
    pipeline: list[PipelineStep]
    outputs: list[OutputSpec]
    changelog: NotRequired[list[ChangelogEntry]]
    spec_updates: NotRequired[list[SpecUpdate]]


@dataclass
class VersionInfo:
    """Parsed version information for variable substitution."""

    full: str
    major: str
    minor: str
    patch: str

    @classmethod
    def from_version_string(cls, version: str) -> "VersionInfo":
        """Parse a version string into components.

        Handles various version formats:
        - Semver: 1.2.3 -> major=1, minor=2, patch=3
        - Two-part: 1.2 -> major=1, minor=2, patch=""
        - Single: 1 -> major=1, minor="", patch=""
        - Complex: 1.2.3~rc1 -> major=1, minor=2, patch=3~rc1
        """
        # Split on dots, handling at most 3 parts
        parts = version.split(".", 2)

        major = parts[0] if len(parts) > 0 else ""
        minor = parts[1] if len(parts) > 1 else ""
        patch = parts[2] if len(parts) > 2 else ""

        return cls(full=version, major=major, minor=minor, patch=patch)


@dataclass
class VariableResolver:
    """Resolves variable substitutions in strings.

    Supports the following variables:
    - {name}: Package name
    - {version}: Full version string
    - {release}: Release number
    - {major}: Major version component
    - {minor}: Minor version component
    - {patch}: Patch version component
    """

    name: str
    version: VersionInfo
    release: str

    # Pattern to match {variable} syntax
    VARIABLE_PATTERN = re.compile(r"\{(\w+)\}")

    @classmethod
    def from_package_info(cls, package: PackageInfo) -> "VariableResolver":
        """Create a resolver from package info."""
        return cls(
            name=package["name"],
            version=VersionInfo.from_version_string(package["version"]),
            release=package["release"],
        )

    def resolve(self, text: str) -> str:
        """Resolve all variables in the given text."""

        def replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return self._get_variable(var_name)

        return self.VARIABLE_PATTERN.sub(replacer, text)

    def _get_variable(self, name: str) -> str:
        """Get the value for a variable name."""
        variables = {
            "name": self.name,
            "version": self.version.full,
            "release": self.release,
            "major": self.version.major,
            "minor": self.version.minor,
            "patch": self.version.patch,
        }

        if name not in variables:
            raise ValueError(f"Unknown variable: {{{name}}}")

        return variables[name]


@dataclass
class ConfigValidationError:
    """A validation error in the configuration."""

    path: str  # JSON path to the error location
    message: str


@dataclass
class ConfigValidationResult:
    """Result of configuration validation."""

    errors: list[ConfigValidationError] = field(default_factory=list)
    warnings: list[ConfigValidationError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Check if configuration is valid (no errors)."""
        return len(self.errors) == 0

    def add_error(self, path: str, message: str) -> None:
        """Add a validation error."""
        self.errors.append(ConfigValidationError(path, message))

    def add_warning(self, path: str, message: str) -> None:
        """Add a validation warning."""
        self.warnings.append(ConfigValidationError(path, message))


def load_config(config_path: Path) -> SourceGenConfig:
    """Load and parse a YAML configuration file.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        yaml.YAMLError: If the YAML is malformed.
        ValueError: If the configuration is invalid.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path) as f:
        raw_config = yaml.safe_load(f)

    if raw_config is None:
        raise ValueError(f"Empty configuration file: {config_path}")

    if not isinstance(raw_config, dict):
        raise ValueError(
            f"Configuration must be a YAML mapping, got {type(raw_config).__name__}"
        )

    # Handle 'with' key rename to 'with_' for Python compatibility
    config = _normalize_config(raw_config)

    # Validate the configuration
    result = validate_config(config)
    if not result.is_valid:
        error_messages = [f"  - {e.path}: {e.message}" for e in result.errors]
        raise ValueError(
            f"Invalid configuration in {config_path}:\n" + "\n".join(error_messages)
        )

    return config


def _normalize_config(raw_config: dict[str, Any]) -> SourceGenConfig:
    """Normalize raw YAML config to handle Python keyword conflicts.

    The YAML uses 'with' for step parameters, but 'with' is a Python keyword,
    so we rename it to 'with_' internally.
    """
    config = dict(raw_config)

    if "pipeline" in config:
        normalized_pipeline = []
        for step in config["pipeline"]:
            normalized_step = dict(step)
            if "with" in normalized_step:
                normalized_step["with_"] = normalized_step.pop("with")
            normalized_pipeline.append(normalized_step)
        config["pipeline"] = normalized_pipeline

    return config  # type: ignore


def validate_config(config: SourceGenConfig) -> ConfigValidationResult:
    """Validate a source generation configuration.

    Args:
        config: The configuration to validate.

    Returns:
        Validation result with any errors or warnings.
    """
    result = ConfigValidationResult()

    # Check schema version
    if "schema_version" not in config:
        result.add_error("schema_version", "Missing required field")
    elif config["schema_version"] != SUPPORTED_SCHEMA_VERSION:
        result.add_error(
            "schema_version",
            f"Unsupported schema version {config['schema_version']}, "
            f"expected {SUPPORTED_SCHEMA_VERSION}",
        )

    # Validate package section
    if "package" not in config:
        result.add_error("package", "Missing required field")
    else:
        package = config["package"]
        for field in ["name", "version", "release"]:
            if field not in package:
                result.add_error(f"package.{field}", "Missing required field")

    # Validate pipeline section
    if "pipeline" not in config:
        result.add_error("pipeline", "Missing required field")
    elif not isinstance(config["pipeline"], list):
        result.add_error("pipeline", "Must be a list")
    else:
        for i, step in enumerate(config["pipeline"]):
            _validate_pipeline_step(step, f"pipeline[{i}]", result)

    # Validate outputs section
    if "outputs" not in config:
        result.add_error("outputs", "Missing required field")
    elif not isinstance(config["outputs"], list):
        result.add_error("outputs", "Must be a list")
    else:
        for i, output in enumerate(config["outputs"]):
            _validate_output(output, f"outputs[{i}]", result)

    # Validate optional sections
    if "tracking" in config:
        _validate_tracking(config["tracking"], "tracking", result)

    if "changelog" in config:
        for i, entry in enumerate(config["changelog"]):
            if "entry" not in entry:
                result.add_error(f"changelog[{i}].entry", "Missing required field")

    if "spec_updates" in config:
        for i, update in enumerate(config["spec_updates"]):
            _validate_spec_update(update, f"spec_updates[{i}]", result)

    return result


def _validate_pipeline_step(
    step: dict[str, Any], path: str, result: ConfigValidationResult
) -> None:
    """Validate a single pipeline step."""
    if "name" not in step:
        result.add_error(f"{path}.name", "Missing required field")

    if "uses" not in step:
        result.add_error(f"{path}.uses", "Missing required field")
    else:
        # Validate known step types
        known_steps = [
            "git-checkout",
            "git-clone",
            "fetch",
            "patch",
            "run",
            "go/vendor",
            "go/bump",
            "go/tidy",
            "go/bundled-provides",
            "cargo/vendor",
            "cargo/bump",
            "cargo/update",
        ]
        if step["uses"] not in known_steps:
            result.add_warning(
                f"{path}.uses",
                f"Unknown step type '{step['uses']}', "
                f"known types: {', '.join(known_steps)}",
            )


def _validate_output(
    output: dict[str, Any], path: str, result: ConfigValidationResult
) -> None:
    """Validate a single output specification."""
    if "type" not in output:
        result.add_error(f"{path}.type", "Missing required field")
    elif output["type"] != "archive":
        result.add_error(
            f"{path}.type",
            f"Unknown output type '{output['type']}', expected 'archive'",
        )

    if "source" not in output:
        result.add_error(f"{path}.source", "Missing required field")

    if "filename" not in output:
        result.add_error(f"{path}.filename", "Missing required field")
    else:
        # Check for valid archive extension
        filename = output["filename"]
        valid_extensions = [
            ".tar.gz",
            ".tgz",
            ".tar.bz2",
            ".tbz2",
            ".tar.xz",
            ".txz",
            ".zip",
        ]
        has_valid_ext = any(filename.endswith(ext) for ext in valid_extensions)
        if not has_valid_ext:
            result.add_error(
                f"{path}.filename",
                f"Invalid archive extension, must be one of: {', '.join(valid_extensions)}",
            )


def _validate_tracking(
    tracking: dict[str, Any], path: str, result: ConfigValidationResult
) -> None:
    """Validate tracking configuration."""
    if "source" not in tracking:
        result.add_error(f"{path}.source", "Missing required field")

    if "branch" not in tracking:
        result.add_error(f"{path}.branch", "Missing required field")


def _validate_spec_update(
    update: dict[str, Any], path: str, result: ConfigValidationResult
) -> None:
    """Validate a spec update entry."""
    has_tag = "tag" in update
    has_macro = "macro" in update
    has_block = "block" in update
    has_replace = "replace" in update

    if not has_tag and not has_macro and not has_block and not has_replace:
        result.add_error(path, "Must specify 'tag', 'macro', 'block', or 'replace'")
    elif sum([has_tag, has_macro, has_block, has_replace]) > 1:
        result.add_error(
            path, "Cannot specify more than one of 'tag', 'macro', 'block', 'replace'"
        )

    if has_tag or has_macro:
        if "value" not in update:
            result.add_error(f"{path}.value", "Missing required field")

    if has_block:
        if "start" not in update:
            result.add_error(f"{path}.start", "Missing required field for block update")
        if "end" not in update:
            result.add_error(f"{path}.end", "Missing required field for block update")
        if "content" not in update and "content_file" not in update:
            result.add_error(
                f"{path}.content",
                "Must specify either 'content' or 'content_file' for block update",
            )

    if has_replace:
        if "with" not in update:
            result.add_error(f"{path}.with", "Missing required field for replace")

    if has_tag or has_macro:
        if "value" not in update:
            result.add_error(f"{path}.value", "Missing required field")

    if has_block:
        if "start" not in update:
            result.add_error(f"{path}.start", "Missing required field for block update")
        if "end" not in update:
            result.add_error(f"{path}.end", "Missing required field for block update")
        if "content" not in update and "content_file" not in update:
            result.add_error(
                f"{path}.content",
                "Must specify either 'content' or 'content_file' for block update",
            )


def resolve_config_variables(
    config: SourceGenConfig, resolver: VariableResolver
) -> SourceGenConfig:
    """Resolve all variable references in a configuration.

    This creates a new configuration with all {variable} references
    replaced with their actual values.

    Args:
        config: The configuration with variable references.
        resolver: The variable resolver to use.

    Returns:
        New configuration with variables resolved.
    """
    return _resolve_in_value(config, resolver)


def _resolve_in_value(value: Any, resolver: VariableResolver) -> Any:
    """Recursively resolve variables in a value."""
    if isinstance(value, str):
        return resolver.resolve(value)
    elif isinstance(value, dict):
        return {k: _resolve_in_value(v, resolver) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_in_value(item, resolver) for item in value]
    else:
        return value
