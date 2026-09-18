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


def test_source_pipeline_caches_pnpm_12_for_all_build_architectures() -> None:
    script = _transform_script()

    assert 'pnpm_version="${pnpm_spec#pnpm@}"' in script
    assert 'if ((pnpm_major >= 12)); then' in script
    assert (
        'npm install --prefix "${root}/.pnpm" --ignore-scripts --force' in script
    )
    assert '"@pnpm/exe.linux-x64@${pnpm_version}"' in script
    assert '"@pnpm/exe.linux-arm64@${pnpm_version}"' in script


def test_source_pipeline_uses_lifecycle_free_pnpm_entrypoint() -> None:
    script = _transform_script()
    fetch_command = next(
        (line for line in script.splitlines() if "run_pnpm fetch" in line),
        None,
    )

    assert fetch_command is not None, "Expected pnpm fetch command"
    assert "node_modules/pnpm/bin/pnpm.mjs" in script
    assert 'node "${pnpm_mjs}" "$@"' in script
    assert "--frozen-lockfile" not in fetch_command


def test_source_pipeline_supports_existing_pnpm_10_cache_layout() -> None:
    script = _transform_script()

    assert 'pnpm_wrapper="${root}/.pnpm/node_modules/.bin/pnpm"' in script
    assert 'elif [[ -x "${pnpm_wrapper}" ]]' in script
    assert 'run_pnpm() { "${pnpm_wrapper}" "$@"; }' in script


def test_spec_uses_lifecycle_free_pnpm_entrypoint() -> None:
    spec = SPEC_FILE.read_text()

    assert 'pnpm_mjs="$PWD/.pnpm/node_modules/pnpm/bin/pnpm.mjs"' in spec
    assert 'node "$pnpm_mjs" "$@"' in spec
    assert 'run_pnpm install --offline --frozen-lockfile' in spec
    assert "run_pnpm run build" in spec


def test_spec_supports_existing_pnpm_10_cache_layout() -> None:
    spec = SPEC_FILE.read_text()

    assert 'pnpm_wrapper="$PWD/.pnpm/node_modules/.bin/pnpm"' in spec
    assert 'elif [[ -x "$pnpm_wrapper" ]]' in spec
    assert 'run_pnpm() { "$pnpm_wrapper" "$@"; }' in spec
