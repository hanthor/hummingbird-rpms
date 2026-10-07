"""Exercise Prometheus 3.5's native Gorget bundled-Provides migration."""

# Gorget is installed in the CI image but does not ship a py.typed marker.
# mypy: disable-error-code=import-untyped

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from gorget.config.loader import build_pipeline_spec
from gorget.config.schema import PipelineSpec
from gorget.config.substitution import SubstitutionVars
from gorget.context import RunContext
from gorget.pipeline.result import PipelineReport
from gorget.pipeline.source import SourceWorkspace
from gorget.pipeline.stages.post import PostStage
from gorget.pipeline.state import StageState
from gorget.specfile import SpecFile

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT / "rpms" / "prometheus3.5"
PIPELINE_FILE = ROOT / "metadata" / "prometheus3.5.source-pipeline.yaml"
INCLUDE_NAME = "bundled-npm-provides.inc"


def _post_context(tmp_path: Path, *, dry_run: bool = False) -> tuple[
    RunContext, PipelineSpec, StageState
]:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    spec_path = package_dir / "prometheus3.5.spec"
    shutil.copyfile(PACKAGE_DIR / spec_path.name, spec_path)
    shutil.copyfile(PACKAGE_DIR / INCLUDE_NAME, package_dir / INCLUDE_NAME)
    source_dir = tmp_path / "checkout"
    source_dir.mkdir()
    variables = SubstitutionVars(
        version="3.5.5",
        old_version="3.5.4",
        package="prometheus3.5",
        spec_file=spec_path.name,
        package_dir=str(package_dir),
        upstream_repo="https://github.com/prometheus/prometheus",
    )
    context = RunContext(
        package_dir=package_dir,
        pipeline_file=PIPELINE_FILE,
        gpg_keys_dir=tmp_path / "keys",
        output_dir=tmp_path / "output",
        dry_run=dry_run,
        spec_path=spec_path,
        vars=variables,
    )
    pipeline = build_pipeline_spec(PIPELINE_FILE, substitution_vars=variables)
    report = PipelineReport(
        package=variables.package,
        version=variables.version,
        old_version=variables.old_version,
        dry_run=dry_run,
    )
    state = StageState(
        work_dir=tmp_path,
        spec=SpecFile(spec_path, sourcedir=package_dir),
        report=report,
        source=SourceWorkspace(path=source_dir),
    )
    return context, pipeline, state


def _write_lock(source_dir: Path, module: str, packages: dict) -> None:
    path = source_dir / module / "package-lock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lockfileVersion": 3, "packages": packages}))


def test_pipeline_replaces_hook_with_native_post_step() -> None:
    pipeline = yaml.safe_load(PIPELINE_FILE.read_text())
    assert pipeline["post"] == [{
        "type": "bundled-provides",
        "ecosystem": "npm",
        "modules": [{"path": "web/ui"}, {"path": "web/ui/react-app"}],
        "scope": "production",
        "output": INCLUDE_NAME,
    }]
    assert not (ROOT / "metadata" / "prometheus3.5.update-hooks.yaml").exists()
    assert not (PACKAGE_DIR / "generate-bundled-npm-provides.py").exists()


def test_pipeline_preserves_artifact_names_and_module_only_go_vendor() -> None:
    pipeline = yaml.safe_load(PIPELINE_FILE.read_text())
    assert [step["type"] for step in pipeline["fetch"]] == ["git"]
    assert pipeline["transform"][0] == {
        "type": "vendor",
        "ecosystem": "go",
        "archive_name": "prometheus-${VERSION}-vendor.tar.bz2",
        "modules": [{"path": ".", "use_workspace": False}],
    }
    assert pipeline["publish"]["files"] == [
        "prometheus-${VERSION}.tar.gz",
        "prometheus-${VERSION}-vendor.tar.bz2",
        "prometheus-npm-cache-${VERSION}.tar.bz2",
    ]
    assert INCLUDE_NAME not in pipeline["publish"]["files"]


def test_native_post_covers_both_frontends_and_excludes_dev_dependencies(
    tmp_path: Path,
) -> None:
    context, pipeline, state = _post_context(tmp_path)
    assert state.source.path is not None
    _write_lock(state.source.path, "web/ui", {
        "": {"name": "not-a-dependency", "version": "1.0.0"},
        "node_modules/shared": {"version": "1.2.3"},
        "node_modules/dev-only": {"version": "9.0.0", "dev": True},
        "node_modules/@scope/optional": {"version": "2.0.0", "optional": True},
    })
    _write_lock(state.source.path, "web/ui/react-app", {
        "node_modules/shared": {"version": "1.2.3"},
        "node_modules/parent/node_modules/shared": {"version": "1.2.4"},
        "node_modules/alias": {"name": "actual-name", "version": "3.0.0-rc.1"},
    })

    result = PostStage().run(context, pipeline, state)

    assert result.status == "success"
    assert (context.package_dir / INCLUDE_NAME).read_text() == (
        "Provides:       bundled(npm(@scope/optional)) = 2.0.0\n"
        "Provides:       bundled(npm(actual-name)) = 3.0.0~rc.1\n"
        "Provides:       bundled(npm(shared)) = 1.2.3\n"
        "Provides:       bundled(npm(shared)) = 1.2.4\n"
    )


def test_native_post_replaces_stale_provides_after_a_lockfile_update(
    tmp_path: Path,
) -> None:
    context, pipeline, state = _post_context(tmp_path)
    assert state.source.path is not None
    _write_lock(state.source.path, "web/ui", {
        "node_modules/react-router": {"version": "7.17.0"},
    })
    _write_lock(state.source.path, "web/ui/react-app", {})
    PostStage().run(context, pipeline, state)
    _write_lock(state.source.path, "web/ui", {
        "node_modules/react-router": {"version": "7.18.0"},
    })

    PostStage().run(context, pipeline, state)

    assert (context.package_dir / INCLUDE_NAME).read_text() == (
        "Provides:       bundled(npm(react-router)) = 7.18.0\n"
    )


def test_native_post_dry_run_keeps_the_committed_include(tmp_path: Path) -> None:
    context, pipeline, state = _post_context(tmp_path, dry_run=True)
    include = context.package_dir / INCLUDE_NAME
    original = include.read_bytes()

    result = PostStage().run(context, pipeline, state)

    assert result.status == "skipped"
    assert include.read_bytes() == original


def test_rpm_spec_consumes_the_regenerated_include(tmp_path: Path) -> None:
    if shutil.which("rpmspec") is None:
        pytest.skip("rpmspec is required to verify the include")
    context, pipeline, state = _post_context(tmp_path)
    assert state.source.path is not None
    _write_lock(state.source.path, "web/ui", {
        "node_modules/example": {"version": "1.2.3"},
    })
    _write_lock(state.source.path, "web/ui/react-app", {})
    PostStage().run(context, pipeline, state)

    result = subprocess.run(
        [
            "rpmspec", "--query", "--rpms", "--define",
            f"_sourcedir {context.package_dir}",
            "--queryformat", "[%{PROVIDENAME} = %{PROVIDEVERSION}\\n]",
            str(context.spec_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "bundled(npm(example)) = 1.2.3\n" in result.stdout
    assert "bundled(npm(react-router))" not in result.stdout
    spec = context.spec_path.read_text()
    assert f"Source4:        {INCLUDE_NAME}" in spec
    assert "%include %{SOURCE4}" in spec
    assert "# BEGIN generated bundled npm Provides" not in spec
