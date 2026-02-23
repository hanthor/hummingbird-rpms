"""
Rust/Cargo-specific pipeline steps.

This module provides steps for working with Rust projects:
- cargo/vendor: Vendor Rust dependencies
- cargo/bump: Bump specific crate versions
"""

import logging
from pathlib import Path
from typing import Any

from source_gen.steps import register_step
from source_gen.steps.base import Step, StepResult, ValidationError

logger = logging.getLogger(__name__)


@register_step("cargo/vendor")
class CargoVendorStep(Step):
    """Vendor Rust dependencies.

    This step runs `cargo vendor` to download all dependencies
    into a vendor directory.

    Parameters:
        manifest: Path to Cargo.toml
        output: Optional output directory for vendor (default: <manifest_dir>/vendor)

    Example:
        - name: Vendor Rust dependencies
          uses: cargo/vendor
          with:
            manifest: project-1.2.3/Cargo.toml
            output: vendor  # Optional: put vendor outside source tree
    """

    def get_dependencies(self) -> list[str]:
        return ["rust", "cargo", "git"]

    def validate(self) -> list[ValidationError]:
        return self._validate_required("manifest")

    def execute(self) -> StepResult:
        manifest = self.require_param("manifest")
        output = self.get_param("output")

        # Get the directory containing Cargo.toml
        manifest_dir = str(Path(manifest).parent)
        if manifest_dir == ".":
            manifest_dir = ""

        logger.info(f"Vendoring Rust dependencies for {manifest}")

        # Build the vendor command
        if output:
            # Vendor to a custom location
            if manifest_dir:
                cmd = f"cd '{manifest_dir}' && cargo vendor '../{output}'"
            else:
                cmd = f"cargo vendor '{output}'"
        else:
            if manifest_dir:
                cmd = f"cd '{manifest_dir}' && cargo vendor"
            else:
                cmd = "cargo vendor"

        # Capture the vendor config output
        cmd += " > .cargo-vendor-config.toml 2>&1 || (cat .cargo-vendor-config.toml && exit 1)"

        result = self._run_container_command(cmd)

        if result.success:
            vendor_dir = output or (
                f"{manifest_dir}/vendor" if manifest_dir else "vendor"
            )
            result.artifacts.append(vendor_dir)
            result.message = f"Vendored Rust dependencies to {vendor_dir}"

        return result


@register_step("cargo/bump")
class CargoBumpStep(Step):
    """Bump Rust crate dependency versions.

    This step updates specific crate versions in Cargo.toml, typically
    used for applying security fixes or updating problematic dependencies.

    Parameters:
        manifest: Path to Cargo.toml
        deps: List of dependencies to bump, each with 'crate' and 'version'

    Example:
        - name: Bump Rust dependencies for CVE fixes
          uses: cargo/bump
          with:
            manifest: project-1.2.3/Cargo.toml
            deps:
              - crate: openssl
                version: 0.10.70
              - crate: tokio
                version: 1.36.0
    """

    def get_dependencies(self) -> list[str]:
        return ["rust", "cargo", "git"]

    def validate(self) -> list[ValidationError]:
        errors = self._validate_required("manifest", "deps")

        deps = self.get_param("deps", [])
        if not isinstance(deps, list):
            errors.append(ValidationError("deps", "Must be a list"))
        else:
            for i, dep in enumerate(deps):
                if not isinstance(dep, dict):
                    errors.append(ValidationError(f"deps[{i}]", "Must be a mapping"))
                    continue
                if "crate" not in dep:
                    errors.append(ValidationError(f"deps[{i}].crate", "Required"))
                if "version" not in dep:
                    errors.append(ValidationError(f"deps[{i}].version", "Required"))

        return errors

    def execute(self) -> StepResult:
        manifest = self.require_param("manifest")
        deps = self.require_param("deps")

        # Get the directory containing Cargo.toml
        manifest_dir = str(Path(manifest).parent)
        if manifest_dir == ".":
            manifest_dir = ""

        logger.info(f"Bumping {len(deps)} Rust dependencies in {manifest}")

        # Build cargo update commands for each dependency
        # Using 'cargo update -p <crate>@<version> --precise <version>'
        update_commands = []
        for dep in deps:
            crate = dep["crate"]
            version = dep["version"]
            # cargo update --precise requires the package name
            update_commands.append(f"cargo update -p {crate} --precise {version}")
            logger.debug(f"  {crate}@{version}")

        # Change to manifest directory and run updates
        if manifest_dir:
            cmd = f"cd '{manifest_dir}' && " + " && ".join(update_commands)
        else:
            cmd = " && ".join(update_commands)

        result = self._run_container_command(cmd)

        if result.success:
            result.message = f"Bumped {len(deps)} Rust dependencies"
        else:
            # Provide more helpful message for cargo update failures
            if "no matching package" in result.stderr.lower():
                result.message = "One or more crates not found in dependency tree"

        return result


@register_step("cargo/update")
class CargoUpdateStep(Step):
    """Run cargo update.

    This step updates Cargo.lock with the latest compatible versions
    of all dependencies.

    Parameters:
        manifest: Path to Cargo.toml

    Example:
        - name: Update Cargo.lock
          uses: cargo/update
          with:
            manifest: project-1.2.3/Cargo.toml
    """

    def get_dependencies(self) -> list[str]:
        return ["rust", "cargo", "git"]

    def validate(self) -> list[ValidationError]:
        return self._validate_required("manifest")

    def execute(self) -> StepResult:
        manifest = self.require_param("manifest")

        # Get the directory containing Cargo.toml
        manifest_dir = str(Path(manifest).parent)
        if manifest_dir == ".":
            manifest_dir = ""

        logger.info(f"Updating Cargo.lock for {manifest}")

        if manifest_dir:
            cmd = f"cd '{manifest_dir}' && cargo update"
        else:
            cmd = "cargo update"

        result = self._run_container_command(cmd)

        if result.success:
            result.message = "Cargo.lock updated"

        return result
