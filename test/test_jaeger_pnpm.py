"""Regression tests for Jaeger's pnpm source and build integration."""

from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
PIPELINE_FILE = ROOT_DIR / "metadata" / "jaeger.source-pipeline.yaml"
SPEC_FILE = ROOT_DIR / "rpms" / "jaeger" / "jaeger.spec"


def _pipeline() -> dict[str, object]:
    pipeline = yaml.safe_load(PIPELINE_FILE.read_text())
    assert isinstance(pipeline, dict), "Expected pipeline to be a mapping"
    return pipeline


def test_source_pipeline_uses_gorget_pnpm_offline_cache() -> None:
    fetch_steps = _pipeline().get("fetch")
    assert isinstance(fetch_steps, list), "Expected fetch steps"
    pnpm_step = next(
        (
            step
            for step in fetch_steps
            if isinstance(step, dict)
            and step.get("type") == "vendor"
            and step.get("ecosystem") == "pnpm"
        ),
        None,
    )

    assert pnpm_step is not None, "Expected a pnpm vendor step"
    assert pnpm_step.get("offline-cache") is True
    assert pnpm_step.get("modules") == [{"path": "jaeger-ui"}]
    assert pnpm_step.get("metadata-packages") == ["@adobe/css-tools"]


def test_spec_uses_lifecycle_free_pnpm_entrypoint() -> None:
    spec = SPEC_FILE.read_text()

    assert 'pnpm_mjs="$PWD/jaeger-ui/.pnpm/node_modules/pnpm/bin/pnpm.mjs"' in spec
    assert 'node "$pnpm_mjs" "$@"' in spec
    assert 'run_pnpm install --offline --frozen-lockfile' in spec
    assert "run_pnpm run build" in spec


def test_spec_supports_existing_pnpm_10_cache_layout() -> None:
    spec = SPEC_FILE.read_text()

    assert 'pnpm_wrapper="$PWD/jaeger-ui/.pnpm/node_modules/.bin/pnpm"' in spec
    assert 'elif [[ -x "$pnpm_wrapper" ]]' in spec
    assert 'run_pnpm() { "$pnpm_wrapper" "$@"; }' in spec
