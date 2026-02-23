#!/usr/bin/env python3
"""
Hummingbird RPMs Source Generation Tool.

This tool generates RPM source tarballs from declarative YAML configuration
files. All pipeline steps execute in Podman containers for reproducibility.

Usage:
    source_gen.py generate <package>       Generate source tarballs
    source_gen.py generate --all           Generate for all packages
    source_gen.py update <package> --version X.Y.Z
    source_gen.py validate <package>       Validate YAML configuration
    source_gen.py upstream-diff <package>  Show Fedora changes since last sync
    source_gen.py local-diff <package>     Show local vs Fedora diff
    source_gen.py upstream-update <package> Interactive Fedora sync
"""

import argparse
import logging
import sys
from pathlib import Path

# Add the ci directory to the path so we can import source_gen package
sys.path.insert(0, str(Path(__file__).parent))

from source_gen.config import load_config, validate_config, VariableResolver

ROOT_DIR = Path(__file__).resolve().parent.parent
RPMS_DIR = ROOT_DIR / "rpms"
METADATA_DIR = ROOT_DIR / "metadata"

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def get_config_path(package: str) -> Path:
    """Get the path to a package's YAML configuration file."""
    return METADATA_DIR / f"{package}.yaml"


def get_package_dir(package: str) -> Path:
    """Get the path to a package's RPM directory."""
    return RPMS_DIR / package


def find_all_configs() -> list[Path]:
    """Find all YAML configuration files in the metadata directory."""
    return sorted(METADATA_DIR.glob("*.yaml"))


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate source tarballs for a package."""
    from source_gen.executor import PipelineExecutor
    from source_gen.steps import ensure_steps_registered

    ensure_steps_registered()

    if args.all:
        configs = find_all_configs()
        if not configs:
            logger.warning("No YAML configuration files found in metadata/")
            return 0

        success_count = 0
        fail_count = 0

        for config_path in configs:
            package = config_path.stem
            logger.info(f"Processing {package}...")

            try:
                result = _generate_package(package, args.dry_run)
                if result == 0:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"Failed to process {package}: {e}")
                fail_count += 1

        logger.info(f"Completed: {success_count} succeeded, {fail_count} failed")
        return 1 if fail_count > 0 else 0
    else:
        return _generate_package(args.package, args.dry_run)


def _generate_package(package: str, dry_run: bool = False) -> int:
    """Generate sources for a single package."""
    from source_gen.executor import PipelineExecutor

    config_path = get_config_path(package)

    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        return 1

    package_dir = get_package_dir(package)
    if not package_dir.exists():
        logger.error(f"Package directory not found: {package_dir}")
        return 1

    try:
        config = load_config(config_path)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        return 1

    # Create and run the executor
    executor = PipelineExecutor(
        config=config,
        package_dir=package_dir,
        dry_run=dry_run,
    )

    result = executor.execute()

    if result.success:
        logger.info(f"Successfully generated sources for {package}")
        return 0
    else:
        logger.error(f"Failed to generate sources: {result.message}")
        return 1


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate YAML configuration files."""
    if args.all:
        configs = find_all_configs()
        if not configs:
            logger.warning("No YAML configuration files found in metadata/")
            return 0

        error_count = 0

        for config_path in configs:
            package = config_path.stem
            result = _validate_package(package)
            if result != 0:
                error_count += 1

        if error_count > 0:
            logger.error(f"Validation failed for {error_count} package(s)")
            return 1

        logger.info(f"All {len(configs)} configurations are valid")
        return 0
    else:
        return _validate_package(args.package)


def _validate_package(package: str) -> int:
    """Validate configuration for a single package."""
    config_path = get_config_path(package)

    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        return 1

    try:
        config = load_config(config_path)
        result = validate_config(config)

        if not result.is_valid:
            logger.error(f"Validation failed for {package}:")
            for error in result.errors:
                logger.error(f"  - {error.path}: {error.message}")
            return 1

        if result.warnings:
            for warning in result.warnings:
                logger.warning(f"  - {warning.path}: {warning.message}")

        logger.info(f"Configuration valid: {package}")
        return 0

    except Exception as e:
        logger.error(f"Failed to validate {package}: {e}")
        return 1


def cmd_update(args: argparse.Namespace) -> int:
    """Update package version/release and regenerate sources."""
    import yaml

    config_path = get_config_path(args.package)

    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        return 1

    # Must specify at least one of --version or --release
    if not args.version and not args.release:
        logger.error("Must specify --version or --release (or both)")
        return 1

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Update version if provided
        if args.version:
            old_version = config["package"]["version"]
            config["package"]["version"] = args.version
            logger.info(f"Updating version: {old_version} -> {args.version}")

        # Update release if provided
        if args.release:
            old_release = config["package"].get("release", "1")
            config["package"]["release"] = args.release
            logger.info(f"Updating release: {old_release} -> {args.release}")

        # Update expected-sha in git-checkout step if provided
        if args.expected_sha:
            for step in config.get("pipeline", []):
                if step.get("uses") == "git-checkout":
                    step.setdefault("with", {})["expected-sha"] = args.expected_sha
                    logger.info(f"Updated expected-sha: {args.expected_sha}")
                    break

        # Write updated config
        if not args.dry_run:
            with open(config_path, "w") as f:
                yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
            logger.info(f"Updated configuration file: {config_path}")
        else:
            logger.info("[DRY-RUN] Would update configuration file")

        # Regenerate sources
        return _generate_package(args.package, args.dry_run)

    except Exception as e:
        logger.error(f"Failed to update {args.package}: {e}")
        return 1


def cmd_upstream_diff(args: argparse.Namespace) -> int:
    """Show changes in Fedora since last sync."""
    from source_gen.tracking import compute_upstream_diff

    config_path = get_config_path(args.package)

    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        return 1

    try:
        config = load_config(config_path)

        if "tracking" not in config:
            logger.error(f"Package {args.package} has no tracking configuration")
            return 1

        diff = compute_upstream_diff(config)
        if diff:
            print(diff)
        else:
            logger.info("No upstream changes since last sync")

        return 0

    except Exception as e:
        logger.error(f"Failed to compute upstream diff: {e}")
        return 1


def cmd_local_diff(args: argparse.Namespace) -> int:
    """Show difference between local spec and Fedora."""
    from source_gen.tracking import compute_local_diff

    config_path = get_config_path(args.package)
    package_dir = get_package_dir(args.package)

    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        return 1

    try:
        config = load_config(config_path)

        if "tracking" not in config:
            logger.error(f"Package {args.package} has no tracking configuration")
            return 1

        diff = compute_local_diff(config, package_dir)
        if diff:
            print(diff)
        else:
            logger.info("No differences from Fedora")

        return 0

    except Exception as e:
        logger.error(f"Failed to compute local diff: {e}")
        return 1


def cmd_upstream_update(args: argparse.Namespace) -> int:
    """Interactive update from Fedora upstream."""
    from source_gen.tracking import interactive_upstream_update

    config_path = get_config_path(args.package)
    package_dir = get_package_dir(args.package)

    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        return 1

    try:
        config = load_config(config_path)

        if "tracking" not in config:
            logger.error(f"Package {args.package} has no tracking configuration")
            return 1

        return interactive_upstream_update(config, config_path, package_dir)

    except Exception as e:
        logger.error(f"Failed to update from upstream: {e}")
        return 1


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Hummingbird RPMs Source Generation Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate command
    gen_parser = subparsers.add_parser(
        "generate",
        help="Generate source tarballs",
    )
    gen_parser.add_argument(
        "package",
        nargs="?",
        help="Package name (required unless --all)",
    )
    gen_parser.add_argument(
        "--all",
        action="store_true",
        help="Process all packages with YAML configs",
    )
    gen_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    # validate command
    val_parser = subparsers.add_parser(
        "validate",
        help="Validate YAML configuration",
    )
    val_parser.add_argument(
        "package",
        nargs="?",
        help="Package name (required unless --all)",
    )
    val_parser.add_argument(
        "--all",
        action="store_true",
        help="Validate all configurations",
    )

    # update command
    upd_parser = subparsers.add_parser(
        "update",
        help="Update package version/release and regenerate sources",
    )
    upd_parser.add_argument(
        "package",
        help="Package name",
    )
    upd_parser.add_argument(
        "--version",
        help="New version number",
    )
    upd_parser.add_argument(
        "--release",
        help="New release number",
    )
    upd_parser.add_argument(
        "--expected-sha",
        help="Expected git commit SHA for the new version",
    )
    upd_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    # upstream-diff command
    ud_parser = subparsers.add_parser(
        "upstream-diff",
        help="Show Fedora changes since last sync",
    )
    ud_parser.add_argument(
        "package",
        help="Package name",
    )

    # local-diff command
    ld_parser = subparsers.add_parser(
        "local-diff",
        help="Show difference between local and Fedora spec",
    )
    ld_parser.add_argument(
        "package",
        help="Package name",
    )

    # upstream-update command
    uu_parser = subparsers.add_parser(
        "upstream-update",
        help="Interactive update from Fedora upstream",
    )
    uu_parser.add_argument(
        "package",
        help="Package name",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    # Validate arguments
    if args.command in ("generate", "validate"):
        if not args.all and not args.package:
            parser.error(f"{args.command} requires either a package name or --all")

    # Dispatch to command handler
    commands = {
        "generate": cmd_generate,
        "validate": cmd_validate,
        "update": cmd_update,
        "upstream-diff": cmd_upstream_diff,
        "local-diff": cmd_local_diff,
        "upstream-update": cmd_upstream_update,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args)
    else:
        parser.error(f"Unknown command: {args.command}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
