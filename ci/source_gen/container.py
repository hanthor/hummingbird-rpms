"""
Container execution abstraction for source generation.

This module provides functionality for running commands inside Podman
containers, ensuring reproducible builds across different environments.
"""

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default container image for all steps
DEFAULT_IMAGE = "registry.fedoraproject.org/fedora:43"


@dataclass
class ContainerConfig:
    """Configuration for container execution."""

    image: str = DEFAULT_IMAGE
    workdir: str = "/work"
    mounts: dict[Path, str] = field(default_factory=dict)  # host_path -> container_path
    env: dict[str, str] = field(default_factory=dict)
    user: Optional[str] = None  # Run as specific user (e.g., "0:0" for root)


@dataclass
class ContainerResult:
    """Result of a container command execution."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        """Check if the command succeeded."""
        return self.returncode == 0


class ContainerRunner:
    """Runs commands inside Podman containers.

    This class manages the lifecycle of container execution, including
    mounting directories, installing dependencies, and running commands.
    """

    def __init__(self, config: Optional[ContainerConfig] = None):
        """Initialize the container runner.

        Args:
            config: Container configuration. If None, uses defaults.
        """
        self.config = config or ContainerConfig()
        self._verify_podman()

    def _verify_podman(self) -> None:
        """Verify that Podman is available."""
        if not shutil.which("podman"):
            raise RuntimeError(
                "Podman is not installed or not in PATH. "
                "Please install Podman to use containerized execution."
            )

    def run(
        self,
        command: str,
        workdir: Path,
        dependencies: Optional[list[str]] = None,
        extra_mounts: Optional[dict[Path, str]] = None,
        capture_output: bool = True,
    ) -> ContainerResult:
        """Run a command inside a container.

        Args:
            command: The shell command to execute.
            workdir: Host directory to mount as the working directory.
            dependencies: DNF packages to install before running the command.
            extra_mounts: Additional host paths to mount (host_path -> container_path).
            capture_output: Whether to capture stdout/stderr.

        Returns:
            ContainerResult with the command output.
        """
        # Build the full command including dependency installation
        full_command = self._build_command(command, dependencies)

        # Build podman arguments
        podman_args = self._build_podman_args(workdir, extra_mounts)

        logger.debug(f"Running in container: {full_command}")
        logger.debug(f"Podman args: {podman_args}")

        try:
            proc = subprocess.run(
                podman_args + [full_command],
                capture_output=capture_output,
                text=True,
            )

            return ContainerResult(
                returncode=proc.returncode,
                stdout=proc.stdout if capture_output else "",
                stderr=proc.stderr if capture_output else "",
            )
        except subprocess.SubprocessError as e:
            logger.error(f"Container execution failed: {e}")
            return ContainerResult(
                returncode=1,
                stdout="",
                stderr=str(e),
            )

    def _build_command(
        self, command: str, dependencies: Optional[list[str]] = None
    ) -> str:
        """Build the full command including dependency installation."""
        parts = []

        # Install dependencies if specified
        if dependencies:
            deps_str = " ".join(dependencies)
            parts.append(f"dnf install -y {deps_str} >/dev/null 2>&1")

        # Add the actual command
        parts.append(command)

        # Join with && to fail fast
        return " && ".join(parts)

    def _build_podman_args(
        self, workdir: Path, extra_mounts: Optional[dict[Path, str]] = None
    ) -> list[str]:
        """Build the podman command arguments."""
        args = [
            "podman",
            "run",
            "--rm",  # Remove container after execution
            "--net=host",  # Use host networking for downloads
        ]

        # Mount the working directory
        args.extend(
            [
                "-v",
                f"{workdir.absolute()}:{self.config.workdir}:Z",
                "-w",
                self.config.workdir,
            ]
        )

        # Add configured mounts
        for host_path, container_path in self.config.mounts.items():
            args.extend(["-v", f"{host_path.absolute()}:{container_path}:Z"])

        # Add extra mounts
        if extra_mounts:
            for host_path, container_path in extra_mounts.items():
                args.extend(["-v", f"{host_path.absolute()}:{container_path}:Z"])

        # Add environment variables
        for key, value in self.config.env.items():
            args.extend(["-e", f"{key}={value}"])

        # Add user specification if set
        if self.config.user:
            args.extend(["--user", self.config.user])

        # Add the image
        args.append(self.config.image)

        # Use sh -c to run the command
        args.extend(["sh", "-c"])

        return args

    def pull_image(self) -> bool:
        """Pull the container image.

        Returns:
            True if successful, False otherwise.
        """
        logger.info(f"Pulling container image: {self.config.image}")

        try:
            proc = subprocess.run(
                ["podman", "pull", self.config.image],
                capture_output=True,
                text=True,
            )

            if proc.returncode != 0:
                logger.error(f"Failed to pull image: {proc.stderr}")
                return False

            return True
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to pull image: {e}")
            return False

    def image_exists(self) -> bool:
        """Check if the container image exists locally.

        Returns:
            True if the image exists, False otherwise.
        """
        try:
            proc = subprocess.run(
                ["podman", "image", "exists", self.config.image],
                capture_output=True,
            )
            return proc.returncode == 0
        except subprocess.SubprocessError:
            return False


def run_in_container(
    command: str,
    workdir: Path,
    dependencies: Optional[list[str]] = None,
    image: str = DEFAULT_IMAGE,
) -> ContainerResult:
    """Convenience function to run a command in a container.

    Args:
        command: The shell command to execute.
        workdir: Host directory to mount as the working directory.
        dependencies: DNF packages to install before running the command.
        image: Container image to use.

    Returns:
        ContainerResult with the command output.
    """
    config = ContainerConfig(image=image)
    runner = ContainerRunner(config)
    return runner.run(command, workdir, dependencies)
