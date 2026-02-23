"""
Hummingbird RPMs Source Generation Tool.

This package provides functionality for generating RPM source tarballs
from declarative YAML configuration files.
"""

from source_gen.config import (
    SourceGenConfig,
    PackageInfo,
    PipelineStep,
    OutputSpec,
    load_config,
    validate_config,
)

__all__ = [
    "SourceGenConfig",
    "PackageInfo",
    "PipelineStep",
    "OutputSpec",
    "load_config",
    "validate_config",
]
