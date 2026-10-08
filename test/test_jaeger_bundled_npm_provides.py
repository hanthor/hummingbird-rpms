"""Tests for Jaeger's bundled npm Provides generator."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

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


def test_generator_reads_ui_workspace_from_source0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = """\
lockfileVersion: '9.0'
importers:
  packages/ui:
    dependencies:
      foo:
        version: 1.2.3
snapshots:
  foo@1.2.3: {}
"""
    members = {
        "jaeger-2.22.0/jaeger-ui/pnpm-lock.yaml": lock,
        "jaeger-2.22.0/jaeger-ui/packages/ui/package.json": json.dumps(
            {"name": "@jaegertracing/jaeger-ui", "version": "2.22.0"}
        ),
        # Other submodules may contain package manifests at the same suffix.
        "jaeger-2.22.0/other/packages/ui/package.json": "{}",
    }
    with tarfile.open(tmp_path / "jaeger-2.22.0.tar.gz", "w:gz") as archive:
        for name, content in members.items():
            payload = content.encode()
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    spec = tmp_path / "jaeger.spec"
    spec.write_text(f"{generator.BEGIN}\nold provides\n{generator.END}\n")
    monkeypatch.setattr(generator, "__file__", str(tmp_path / GENERATOR_PATH.name))
    monkeypatch.setattr(sys, "argv", [GENERATOR_PATH.name, "2.22.0"])

    generator.main()

    assert spec.read_text() == (
        f"{generator.BEGIN}\n"
        "Provides:       bundled(npm(foo)) = 1.2.3\n"
        "Provides:       bundled(npm(@jaegertracing/jaeger-ui)) = 2.22.0\n"
        f"{generator.END}\n"
    )
