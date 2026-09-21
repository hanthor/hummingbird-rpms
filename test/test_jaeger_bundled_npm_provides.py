"""Tests for Jaeger's bundled npm Provides generator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR_PATH = ROOT / "rpms" / "jaeger" / "generate-bundled-npm-provides.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("jaeger_bundled_npm_provides", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


generator = _load_generator()


def test_single_document_lockfile() -> None:
    lock = """\
lockfileVersion: '9.0'
importers:
  .:
    dependencies:
      foo:
        version: 1.2.3
  packages/ui:
    dependencies: {}
snapshots:
  foo@1.2.3: {}
"""

    assert generator.lockfile_provides(lock) == {("foo", "1.2.3")}
    assert generator.lockfile_importers(lock) == {"packages/ui"}


def test_multi_document_lockfile() -> None:
    lock = """\
---
lockfileVersion: '9.0'
importers:
  .:
    packageManagerDependencies:
      pnpm:
        version: 12.1.0
snapshots:
  pnpm@12.1.0: {}
---
lockfileVersion: '9.0'
importers:
  .:
    dependencies:
      foo:
        version: 1.2.3
  packages/ui:
    optionalDependencies:
      optional:
        version: 4.5.6
snapshots:
  foo@1.2.3:
    dependencies:
      transitive: 2.0.0
  optional@4.5.6: {}
  transitive@2.0.0: {}
"""

    assert generator.lockfile_provides(lock) == {
        ("foo", "1.2.3"),
        ("optional", "4.5.6"),
        ("transitive", "2.0.0"),
    }
    assert generator.lockfile_importers(lock) == {"packages/ui"}
