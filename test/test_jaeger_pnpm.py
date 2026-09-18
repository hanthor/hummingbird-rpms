"""Regression tests for Jaeger's pnpm source and build integration."""

from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
PIPELINE_FILE = ROOT_DIR / "metadata" / "jaeger.source-pipeline.yaml"
SPEC_FILE = ROOT_DIR / "rpms" / "jaeger" / "jaeger.spec"


def _transform_script() -> str:
    pipeline = yaml.safe_load(PIPELINE_FILE.read_text())
    assert isinstance(pipeline, dict), "Expected pipeline to be a mapping"
    transforms = pipeline.get("transform")
    assert isinstance(transforms, list) and transforms, "Expected transform steps"
    transform = transforms[0]
    assert isinstance(transform, dict), "Expected first transform to be a mapping"
    commands = transform.get("command")
    assert isinstance(commands, list) and commands, "Expected transform commands"
    script = commands[-1]
    assert isinstance(script, str), "Expected transform script to be a string"
    return script


def test_source_pipeline_uses_jaeger_ui_declared_pnpm() -> None:
    script = _transform_script()

    assert ".packageManager" in script
    assert "pnpm@10.5.0" not in script
    assert (
        'npm install --prefix "${root}/.pnpm" --ignore-scripts "${pnpm_spec}"'
        in script
    )


def test_source_pipeline_uses_lifecycle_free_pnpm_entrypoint() -> None:
    script = _transform_script()
    fetch_command = next(
        (line for line in script.splitlines() if 'node "${pnpm}" fetch' in line),
        None,
    )

    assert fetch_command is not None, "Expected pnpm fetch command"
    assert "node_modules/pnpm/bin/pnpm.mjs" in script
    assert 'node "${pnpm}" fetch' in fetch_command
    assert "--frozen-lockfile" not in fetch_command


def test_spec_uses_lifecycle_free_pnpm_entrypoint() -> None:
    spec = SPEC_FILE.read_text()

    assert 'pnpm="$PWD/.pnpm/node_modules/pnpm/bin/pnpm.mjs"' in spec
    assert '[[ ! -f "$pnpm" ]]' in spec
    assert 'node "$pnpm" install --offline --frozen-lockfile' in spec
    assert 'node "$pnpm" run build' in spec
