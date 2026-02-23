"""
Pipeline execution engine.

This module orchestrates the execution of source generation pipelines,
running each step in sequence and handling outputs.
"""

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from source_gen.archive import create_archive, derive_prefix_from_filename
from source_gen.checksum import compute_and_update_sources
from source_gen.config import (
    SourceGenConfig,
    VariableResolver,
    resolve_config_variables,
)
from source_gen.spec_updater import update_spec_from_config
from source_gen.steps import create_step, ensure_steps_registered
from source_gen.steps.base import StepContext, StepResult

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of pipeline execution."""

    success: bool
    message: str
    step_results: list[tuple[str, StepResult]] = field(default_factory=list)
    artifacts: list[Path] = field(default_factory=list)

    @classmethod
    def ok(
        cls, message: str = "Pipeline completed successfully", **kwargs
    ) -> "ExecutionResult":
        return cls(success=True, message=message, **kwargs)

    @classmethod
    def error(cls, message: str, **kwargs) -> "ExecutionResult":
        return cls(success=False, message=message, **kwargs)


class PipelineExecutor:
    """Executes source generation pipelines.

    This class coordinates the execution of all pipeline steps, manages
    the working directory, and handles output generation.
    """

    def __init__(
        self,
        config: SourceGenConfig,
        package_dir: Path,
        dry_run: bool = False,
        container_image: Optional[str] = None,
    ):
        """Initialize the executor.

        Args:
            config: The source generation configuration.
            package_dir: Path to rpms/<package>/ directory.
            dry_run: If True, don't execute steps or create outputs.
            container_image: Override default container image.
        """
        self.config = config
        self.package_dir = package_dir
        self.dry_run = dry_run
        self.container_image = container_image or "registry.fedoraproject.org/fedora:43"

        # Ensure steps are registered
        ensure_steps_registered()

        # Create variable resolver
        self.resolver = VariableResolver.from_package_info(config["package"])

        # Resolve variables in config
        self.resolved_config = resolve_config_variables(config, self.resolver)

    def execute(self) -> ExecutionResult:
        """Execute the full pipeline.

        This runs all pipeline steps, generates output archives,
        updates checksums, and optionally updates the spec file.

        Returns:
            ExecutionResult with status and details.
        """
        package_name = self.config["package"]["name"]
        logger.info(f"Starting pipeline for {package_name}")

        # Create temporary working directory
        workdir = Path(tempfile.mkdtemp(prefix=f"source-gen-{package_name}-"))
        logger.debug(f"Working directory: {workdir}")

        try:
            # Create step context
            context = StepContext(
                workdir=workdir,
                package_dir=self.package_dir,
                package_name=package_name,
                dry_run=self.dry_run,
                container_image=self.container_image,
            )

            # Execute pipeline steps
            step_results = self._execute_steps(context)

            # Check if any step failed
            failed_steps = [
                (name, result) for name, result in step_results if not result.success
            ]
            if failed_steps:
                first_failure = failed_steps[0]
                return ExecutionResult.error(
                    message=f"Step '{first_failure[0]}' failed: {first_failure[1].message}",
                    step_results=step_results,
                )

            # Generate output archives
            archives = self._generate_outputs(workdir)
            if archives is None:
                return ExecutionResult.error(
                    message="Failed to generate output archives",
                    step_results=step_results,
                )

            # Copy archives to package directory and update sources
            if not self._finalize_outputs(archives):
                return ExecutionResult.error(
                    message="Failed to finalize outputs",
                    step_results=step_results,
                )

            # Update spec file if configured
            if (
                "spec_updates" in self.resolved_config
                or "changelog" in self.resolved_config
            ):
                if not self._update_spec(workdir=workdir):
                    return ExecutionResult.error(
                        message="Failed to update spec file",
                        step_results=step_results,
                    )

            return ExecutionResult.ok(
                message=f"Generated {len(archives)} archive(s) for {package_name}",
                step_results=step_results,
                artifacts=[self.package_dir / a.name for a in archives],
            )

        finally:
            # Cleanup working directory
            if not self.dry_run and workdir.exists():
                shutil.rmtree(workdir)
                logger.debug(f"Cleaned up working directory: {workdir}")

    def _execute_steps(self, context: StepContext) -> list[tuple[str, StepResult]]:
        """Execute all pipeline steps.

        Args:
            context: The step execution context.

        Returns:
            List of (step_name, result) tuples.
        """
        results = []

        for step_config in self.resolved_config["pipeline"]:
            step_name = step_config["name"]
            step_type = step_config["uses"]
            step_params = step_config.get("with_", {})

            logger.info(f"Executing step: {step_name}")

            try:
                # Create and validate the step
                step = create_step(step_type, step_params, context)

                validation_errors = step.validate()
                if validation_errors:
                    error_msgs = [f"{e.param}: {e.message}" for e in validation_errors]
                    result = StepResult.error(
                        message=f"Validation failed: {'; '.join(error_msgs)}"
                    )
                    results.append((step_name, result))
                    break

                # Execute the step
                result = step.execute()
                results.append((step_name, result))

                if not result.success:
                    logger.error(f"Step '{step_name}' failed: {result.message}")
                    if result.stderr:
                        logger.error(f"stderr: {result.stderr}")
                    break
                else:
                    logger.info(f"Step '{step_name}' completed: {result.message}")

            except Exception as e:
                logger.exception(f"Step '{step_name}' raised an exception")
                result = StepResult.error(message=str(e))
                results.append((step_name, result))
                break

        return results

    def _generate_outputs(self, workdir: Path) -> Optional[list[Path]]:
        """Generate output archives.

        Args:
            workdir: The working directory containing source files.

        Returns:
            List of generated archive paths, or None on failure.
        """
        archives = []

        for output_config in self.resolved_config["outputs"]:
            if output_config["type"] != "archive":
                logger.error(f"Unknown output type: {output_config['type']}")
                return None

            source = output_config["source"]
            filename = output_config["filename"]
            exclude = output_config.get("exclude", [])
            prefix = output_config.get("prefix")

            # Derive prefix from filename if not specified
            if prefix is None:
                prefix = derive_prefix_from_filename(filename)

            source_path = workdir / source
            output_path = workdir / filename

            logger.info(f"Creating archive: {filename}")

            success = create_archive(
                source_dir=source_path,
                output_path=output_path,
                prefix=prefix,
                exclude=exclude,
                dry_run=self.dry_run,
            )

            if not success:
                logger.error(f"Failed to create archive: {filename}")
                return None

            archives.append(output_path)

        return archives

    def _finalize_outputs(self, archives: list[Path]) -> bool:
        """Copy archives to package directory and update sources.

        Args:
            archives: List of archive paths in workdir.

        Returns:
            True if successful.
        """
        if self.dry_run:
            logger.info("[DRY-RUN] Would copy archives to package directory:")
            for archive in archives:
                logger.info(f"  {archive.name} -> {self.package_dir}/")
            logger.info(
                f"[DRY-RUN] Would update sources file: {self.package_dir / 'sources'}"
            )
            for archive in archives:
                logger.info(f"  SHA512 ({archive.name}) = <computed>")
            return True

        final_paths = []

        for archive in archives:
            dest = self.package_dir / archive.name

            # Copy archive to package directory
            shutil.copy2(archive, dest)
            logger.info(f"Copied {archive.name} to {self.package_dir}")

            final_paths.append(dest)

        # Update sources file
        return compute_and_update_sources(
            package_dir=self.package_dir,
            archive_files=final_paths,
            dry_run=self.dry_run,
        )

    def _update_spec(self, workdir: Optional[Path] = None) -> bool:
        """Update the spec file with configured changes.

        Args:
            workdir: Working directory for resolving content_file paths.

        Returns:
            True if successful.
        """
        # Find spec file
        spec_files = list(self.package_dir.glob("*.spec"))
        if not spec_files:
            logger.warning("No spec file found, skipping spec updates")
            return True

        spec_path = spec_files[0]

        spec_updates = self.resolved_config.get("spec_updates", [])
        changelog = self.resolved_config.get("changelog", [])

        # Process any content_file references in spec_updates
        processed_updates = []
        for update in spec_updates:
            if "block" in update and "content_file" in update:
                # Read content from the generated file
                content_file = update["content_file"]
                if workdir:
                    content_path = workdir / content_file
                    if content_path.exists():
                        content = content_path.read_text().strip()
                        processed_update = dict(update)
                        processed_update["content"] = content
                        del processed_update["content_file"]
                        processed_updates.append(processed_update)
                        continue
                    else:
                        logger.warning(f"Content file not found: {content_path}")
            processed_updates.append(update)

        return update_spec_from_config(
            spec_path=spec_path,
            spec_updates=processed_updates,
            changelog_entries=changelog,
            dry_run=self.dry_run,
        )
