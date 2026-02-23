"""
Go-specific pipeline steps.

This module provides steps for working with Go projects:
- go/vendor: Vendor Go dependencies
- go/bump: Bump specific Go module versions
- go/tidy: Run go mod tidy
- go/bundled-provides: Generate bundled Provides from modules.txt
"""

import logging
import re
from pathlib import Path
from typing import Any

from source_gen.steps import register_step
from source_gen.steps.base import Step, StepResult, ValidationError

logger = logging.getLogger(__name__)


def parse_modules_txt(modules_txt_path: Path) -> list[tuple[str, str]]:
    """Parse a Go modules.txt file to extract module names and versions.

    Args:
        modules_txt_path: Path to vendor/modules.txt

    Returns:
        List of (module_name, version) tuples.
    """
    modules = []

    if not modules_txt_path.exists():
        return modules

    content = modules_txt_path.read_text()

    for line in content.splitlines():
        line = line.strip()
        # Match lines like: # module/path v1.2.3
        # Skip explicit and other directives
        if line.startswith("# ") and not line.startswith("# explicit"):
            # Handle "=> replacement" syntax
            if " => " in line:
                line = line.split(" => ")[-1]
            else:
                line = line[2:]  # Remove "# " prefix

            parts = line.split()
            if len(parts) >= 2:
                module_name = parts[0]
                version = parts[1]
                # Remove 'v' prefix if present
                if version.startswith("v"):
                    version = version[1:]
                # Replace hyphens with dots (RPM versioning)
                version = version.replace("-", ".")
                modules.append((module_name, version))

    return modules


def format_bundled_provides(modules: list[tuple[str, str]]) -> str:
    """Format modules as RPM bundled Provides lines.

    Args:
        modules: List of (module_name, version) tuples.

    Returns:
        Multi-line string of Provides statements.
    """
    lines = []
    for module_name, version in sorted(set(modules)):
        lines.append(f"Provides: bundled(golang({module_name})) = {version}")
    return "\n".join(lines)


@register_step("go/vendor")
class GoVendorStep(Step):
    """Vendor Go dependencies.

    This step runs `go mod vendor` to download all dependencies
    into a vendor directory.

    Parameters:
        modroot: Directory containing go.mod
        output: Optional output directory for vendor (default: <modroot>/vendor)

    Example:
        - name: Vendor Go dependencies
          uses: go/vendor
          with:
            modroot: project-1.2.3
            output: vendor  # Optional: put vendor outside source tree
    """

    def get_dependencies(self) -> list[str]:
        return ["golang", "git"]

    def validate(self) -> list[ValidationError]:
        return self._validate_required("modroot")

    def execute(self) -> StepResult:
        modroot = self.require_param("modroot")
        output = self.get_param("output")

        logger.info(f"Vendoring Go dependencies for {modroot}")

        # Build the vendor command
        if output:
            # Vendor to a custom location
            # We need to vendor normally first, then move
            cmd = f"cd '{modroot}' && go mod vendor"
            if output != f"{modroot}/vendor":
                cmd += f" && mv vendor '../{output}'"
        else:
            cmd = f"cd '{modroot}' && go mod vendor"

        result = self._run_container_command(cmd)

        if result.success:
            vendor_dir = output or f"{modroot}/vendor"
            result.artifacts.append(vendor_dir)
            result.message = f"Vendored Go dependencies to {vendor_dir}"

        return result


@register_step("go/bump")
class GoBumpStep(Step):
    """Bump Go module dependency versions.

    This step updates specific module versions in go.mod, typically
    used for applying security fixes or updating problematic dependencies.

    Parameters:
        modroot: Directory containing go.mod
        deps: List of dependencies to bump, each with 'module' and 'version'

    Example:
        - name: Bump Go dependencies for CVE fixes
          uses: go/bump
          with:
            modroot: project-1.2.3
            deps:
              - module: golang.org/x/crypto
                version: v0.32.0
              - module: github.com/opencontainers/selinux
                version: v1.13.0
    """

    def get_dependencies(self) -> list[str]:
        return ["golang", "git"]

    def validate(self) -> list[ValidationError]:
        errors = self._validate_required("modroot", "deps")

        deps = self.get_param("deps", [])
        if not isinstance(deps, list):
            errors.append(ValidationError("deps", "Must be a list"))
        else:
            for i, dep in enumerate(deps):
                if not isinstance(dep, dict):
                    errors.append(ValidationError(f"deps[{i}]", "Must be a mapping"))
                    continue
                if "module" not in dep:
                    errors.append(ValidationError(f"deps[{i}].module", "Required"))
                if "version" not in dep:
                    errors.append(ValidationError(f"deps[{i}].version", "Required"))

        return errors

    def execute(self) -> StepResult:
        modroot = self.require_param("modroot")
        deps = self.require_param("deps")

        logger.info(f"Bumping {len(deps)} Go dependencies in {modroot}")

        # Build go get commands for each dependency
        get_commands = []
        for dep in deps:
            module = dep["module"]
            version = dep["version"]
            get_commands.append(f"go get {module}@{version}")
            logger.debug(f"  {module}@{version}")

        # Run all go get commands, then go mod tidy
        cmd = f"cd '{modroot}' && " + " && ".join(get_commands) + " && go mod tidy"

        result = self._run_container_command(cmd)

        if result.success:
            result.message = f"Bumped {len(deps)} Go dependencies"

        return result


@register_step("go/tidy")
class GoTidyStep(Step):
    """Run go mod tidy.

    This step cleans up the go.mod and go.sum files by removing
    unused dependencies and adding missing ones.

    Parameters:
        modroot: Directory containing go.mod

    Example:
        - name: Tidy Go modules
          uses: go/tidy
          with:
            modroot: project-1.2.3
    """

    def get_dependencies(self) -> list[str]:
        return ["golang", "git"]

    def validate(self) -> list[ValidationError]:
        return self._validate_required("modroot")

    def execute(self) -> StepResult:
        modroot = self.require_param("modroot")

        logger.info(f"Running go mod tidy in {modroot}")

        cmd = f"cd '{modroot}' && go mod tidy"

        result = self._run_container_command(cmd)

        if result.success:
            result.message = "Go modules tidied"

        return result


@register_step("go/bundled-provides")
class GoBundledProvidesStep(Step):
    """Generate bundled Provides from Go vendor/modules.txt.

    This step scans the source tree for vendor/modules.txt files and
    generates the bundled golang Provides lines. The output is saved
    to a file that can be referenced in spec_updates.

    Parameters:
        source: Source directory to scan for modules.txt files
        output: Output file path (relative to workdir) for the Provides

    Example:
        - name: Generate bundled Provides
          uses: go/bundled-provides
          with:
            source: go1.26
            output: bundled-provides.txt
    """

    def get_dependencies(self) -> list[str]:
        return []  # Pure Python, no container dependencies

    def validate(self) -> list[ValidationError]:
        return self._validate_required("source", "output")

    def execute(self) -> StepResult:
        source = self.require_param("source")
        output = self.require_param("output")

        source_path = self.context.workdir / source
        output_path = self.context.workdir / output

        if self.context.dry_run:
            return StepResult.ok(
                message=f"[DRY-RUN] Would generate bundled Provides from {source} to {output}"
            )

        logger.info(f"Scanning {source} for vendor/modules.txt files")

        # Find all modules.txt files
        all_modules = []
        for modules_txt in source_path.rglob("modules.txt"):
            if "vendor" in str(modules_txt):
                logger.debug(f"Found {modules_txt}")
                modules = parse_modules_txt(modules_txt)
                all_modules.extend(modules)

        if not all_modules:
            return StepResult.error(message=f"No modules.txt files found in {source}")

        # Generate Provides content
        provides_content = format_bundled_provides(all_modules)

        # Write to output file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(provides_content + "\n")

        logger.info(f"Generated {len(all_modules)} bundled Provides to {output}")

        return StepResult.ok(
            message=f"Generated {len(all_modules)} bundled Provides",
            artifacts=[str(output)],
        )
