"""Regression tests for the nodejs22 source pipeline."""

# Gorget is installed in the CI image but does not ship a py.typed marker.
# mypy: disable-error-code=import-untyped

import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml
from gorget.config.loader import build_pipeline_spec
from gorget.config.schema import PipelineSpec
from gorget.config.substitution import SubstitutionVars
from gorget.context import RunContext
from gorget.exceptions import GorgetTransientError
from gorget.fetch.base import FetchContext
from gorget.fetch.spec_update import SpecUpdateHandler
from gorget.pipeline.artifact import Artifact
from gorget.pipeline.result import PipelineReport
from gorget.pipeline.stages.post import PostStage
from gorget.pipeline.state import StageState
from gorget.specfile import SpecFile
from specfile import Specfile

ROOT_DIR = Path(__file__).resolve().parent.parent
PIPELINE_FILE = ROOT_DIR / "metadata" / "nodejs22.source-pipeline.yaml"
PACKAGE_DIR = ROOT_DIR / "rpms" / "nodejs22"
VERSION = "22.23.4"


def _packaging_scripts_command() -> list[str]:
    pipeline = yaml.safe_load(PIPELINE_FILE.read_text())
    assert isinstance(pipeline, dict)
    transforms = pipeline.get("transform")
    assert isinstance(transforms, list)

    for transform in transforms:
        if not isinstance(transform, dict):
            continue
        if "packaging-scripts.tar.gz" not in transform.get("outputs", []):
            continue
        command = transform.get("command")
        assert isinstance(command, list)
        assert all(isinstance(argument, str) for argument in command)
        return command

    raise AssertionError("nodejs22 packaging-scripts transform not found")


def test_packaging_scripts_archive_normalizes_gitlab_checkout_modes(
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "nodejs22"
    packaging_dir = package_dir / "packaging"
    packaging_dir.mkdir(parents=True)

    source_modes = {
        "Makefile": 0o666,
        "packaging/make-nodejs-tarball.sh": 0o777,
        "packaging/fill-versions.sh": 0o777,
    }
    for relative_path, mode in source_modes.items():
        destination = package_dir / relative_path
        shutil.copyfile(PACKAGE_DIR / relative_path, destination)
        destination.chmod(mode)

    command = [
        argument.replace("${PACKAGE_DIR}", str(package_dir))
        for argument in _packaging_scripts_command()
    ]
    subprocess.run(command, cwd=tmp_path, check=True)

    with tarfile.open(tmp_path / "packaging-scripts.tar.gz") as archive:
        member_modes = {member.name: member.mode for member in archive.getmembers()}

    assert member_modes == {
        "Makefile": 0o644,
        "packaging/make-nodejs-tarball.sh": 0o755,
        "packaging/fill-versions.sh": 0o755,
    }


def _source_archive(path: Path, version: str = VERSION) -> None:
    """Supply the version headers consumed by the real fill-versions.sh."""
    files = {
        "src/node_version.h": "#define NODE_MODULE_VERSION 127\n",
        "deps/ada/ada.h": '#define ADA_VERSION "2.9.2"\n',
        "deps/brotli/c/common/version.h": (
            "#define BROTLI_VERSION_MAJOR 1\n#define BROTLI_VERSION_MINOR 1\n"
            "#define BROTLI_VERSION_PATCH 0\n"
        ),
        "deps/cares/include/ares_version.h": '#define ARES_VERSION_STR "1.34.8"\n',
        "deps/histogram/include/hdr/hdr_histogram_version.h": '#define HDR_HISTOGRAM_VERSION "0.11.9"\n',
        "tools/icu/current_ver.dep": '[{"url":"https://example.com/release-78.3/icu.zip"}]\n',
        "deps/uv/include/uv/version.h": (
            "#define UV_VERSION_MAJOR 1\n#define UV_VERSION_MINOR 51\n#define UV_VERSION_PATCH 0\n"
        ),
        "deps/llhttp/include/llhttp.h": (
            "#define LLHTTP_VERSION_MAJOR 9\n#define LLHTTP_VERSION_MINOR 4\n#define LLHTTP_VERSION_PATCH 3\n"
        ),
        "deps/nghttp2/lib/includes/nghttp2/nghttp2ver.h": '#define NGHTTP2_VERSION "1.69.0"\n',
        "deps/ngtcp2/nghttp3/lib/includes/nghttp3/version.h": '#define NGHTTP3_VERSION "1.6.0"\n',
        "deps/ngtcp2/ngtcp2/lib/includes/ngtcp2/version.h": '#define NGTCP2_VERSION "1.11.0"\n',
        "deps/cjs-module-lexer/src/package.json": '{"version":"2.2.0"}\n',
        "lib/punycode.js": "  'version': '2.1.0',\n",
        "deps/undici/src/package.json": '{"version":"6.28.1"}\n',
        "deps/npm/package.json": '{"version":"10.9.9"}\n',
        "deps/sqlite/sqlite3.h": '#define SQLITE_VERSION "3.51.3"\n',
        "deps/uvwasi/include/uvwasi.h": (
            "#define UVWASI_VERSION_MAJOR 0\n#define UVWASI_VERSION_MINOR 0\n"
            "#define UVWASI_VERSION_PATCH 23\n"
        ),
        "deps/v8/include/v8-version.h": (
            "#define V8_MAJOR_VERSION 12\n#define V8_MINOR_VERSION 4\n"
            "#define V8_BUILD_NUMBER 254\n#define V8_PATCH_LEVEL 21\n"
        ),
        "deps/zlib/zlib.h": '#define ZLIB_VERSION "1.3.1"\n',
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            data = content.encode()
            entry = tarfile.TarInfo(f"node-v{version}/{name}")
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))


def _post_context(tmp_path: Path, *, dry_run: bool = False, version: str = VERSION) -> tuple[
    RunContext, PipelineSpec, StageState
]:
    # Spaces exercise quoting of SPEC_FILE and package-relative artifact paths.
    package_dir = tmp_path / "package with spaces"
    shutil.copytree(PACKAGE_DIR, package_dir)
    spec_path = package_dir / "nodejs22.spec"
    variables = SubstitutionVars(
        version=version, old_version="22.23.3", package="nodejs22",
        spec_file=spec_path.name, package_dir=str(package_dir),
    )
    context = RunContext(
        package_dir=package_dir, pipeline_file=PIPELINE_FILE,
        gpg_keys_dir=tmp_path / "keys", output_dir=tmp_path / "output",
        dry_run=dry_run, spec_path=spec_path, vars=variables,
    )
    pipeline = build_pipeline_spec(PIPELINE_FILE, substitution_vars=variables)
    scratch = tmp_path / "scratch.spec"
    shutil.copyfile(spec_path, scratch)
    archive_path = tmp_path / f"node-v{version}.tar.gz"
    _source_archive(archive_path, version)
    state = StageState(
        work_dir=tmp_path, spec=SpecFile(scratch, sourcedir=package_dir),
        report=PipelineReport(package="nodejs22", version=version,
                              old_version=variables.old_version, dry_run=dry_run),
        artifacts=[Artifact(path=archive_path, output_name=archive_path.name,
                            source_description="test Node source", checksum=None)],
    )
    return context, pipeline, state


def test_nodejs22_pipeline_removes_legacy_hook() -> None:
    assert not (ROOT_DIR / "metadata" / "nodejs22.update-hooks.yaml").exists()
    pipeline = yaml.safe_load(PIPELINE_FILE.read_text())
    assert pipeline["fetch"][0]["set-version"] is False
    assert pipeline["fetch"][0]["reset-release"] is None
    assert pipeline["post"][0]["artifacts"] == ["node-v${VERSION}.tar.gz"]


def test_nodejs22_scratch_substitution_does_not_persist_spec(tmp_path: Path) -> None:
    context, pipeline, state = _post_context(tmp_path)
    original = context.spec_path.read_bytes()
    fetch_context = FetchContext(
        work_dir=tmp_path, package_dir=context.package_dir,
        spec=state.spec, vars=context.vars, dry_run=False,
    )
    SpecUpdateHandler().run(pipeline.fetch[0], fetch_context)

    assert f"1:{VERSION}-" in state.spec.path.read_text()
    assert context.spec_path.read_bytes() == original


@pytest.mark.parametrize("generic_update", [False, True])
def test_nodejs22_post_persists_version_and_preserves_evr(
    tmp_path: Path, generic_update: bool,
) -> None:
    context, pipeline, state = _post_context(tmp_path)
    before = context.spec_path.read_text()
    node_line = next(line for line in before.splitlines()
                     if line.startswith("%nodejs_define_version node "))
    old_version = Specfile(str(context.spec_path), sourcedir=str(context.package_dir)).expanded_version
    if generic_update:
        # Exercise the actual specfile operations used by the upstream checker
        # when there is no update_spec hook, not a simulated string replacement.
        spec = Specfile(str(context.spec_path), sourcedir=str(context.package_dir))
        spec.update_version(VERSION)
        spec.update_tag("Release", "0.1%{?dist}", protected_entities=".*")
        spec.save()

    result = PostStage().run(context, pipeline, state)

    assert result.status == "success"
    after = context.spec_path.read_text()
    assert node_line.replace(old_version, VERSION) in after
    spec = Specfile(str(context.spec_path), sourcedir=str(context.package_dir))
    with spec.tags() as tags:
        assert tags.version.value == "%{node_version}"
        assert tags.release.value == "%{node_release}"
    assert spec.expanded_version == VERSION
    assert spec.expanded_release == Specfile(
        str(PACKAGE_DIR / "nodejs22.spec"), sourcedir=str(PACKAGE_DIR),
    ).expanded_release
    assert "%nodejs_define_version npm 1:10.9.9-%{nodejs_subpackage_release}" in after
    assert "%nodejs_define_version v8 3:12.4.254.21-%{nodejs_subpackage_release} -p" in after
    assert f"# Version from node-v{VERSION}/src/node_version.h" in after
    assert not (context.package_dir / f"node-v{VERSION}").exists()
    # Dependency refresh leaves unrelated packaging, source declarations and
    # security patches unchanged; only the node macro changes outside that block.
    before_start, before_rest = before.split("# BEGIN automatic-version-macros", 1)
    after_start, after_rest = after.split("# BEGIN automatic-version-macros", 1)
    assert after_start == before_start.replace(old_version, VERSION)
    assert after_rest.split("# END automatic-version-macros", 1)[1] == (
        before_rest.split("# END automatic-version-macros", 1)[1]
    )


def test_nodejs22_post_dry_run_preserves_tracked_files(tmp_path: Path) -> None:
    context, pipeline, state = _post_context(tmp_path, dry_run=True)
    original = context.spec_path.read_bytes()
    source_archive = state.artifacts[0].path
    original_archive = source_archive.read_bytes()

    result = PostStage().run(context, pipeline, state)

    assert result.status == "skipped"
    assert context.spec_path.read_bytes() == original
    assert source_archive.read_bytes() == original_archive
    # A non-dry-run post stage copies declared artifacts into package_dir.
    assert not (context.package_dir / f"node-v{VERSION}.tar.gz").exists()


def test_nodejs22_post_rejects_invalid_version(tmp_path: Path) -> None:
    context, pipeline, state = _post_context(tmp_path, version="22.23.4|bad")
    original = context.spec_path.read_bytes()

    with pytest.raises(GorgetTransientError, match="Unexpected Node version"):
        PostStage().run(context, pipeline, state)

    assert context.spec_path.read_bytes() == original


def test_nodejs22_post_is_idempotent(tmp_path: Path) -> None:
    context, pipeline, state = _post_context(tmp_path)
    PostStage().run(context, pipeline, state)
    first = context.spec_path.read_bytes()

    PostStage().run(context, pipeline, state)

    assert context.spec_path.read_bytes() == first


def test_nodejs22_post_keeps_subpackage_version_release_macros(tmp_path: Path) -> None:
    context, pipeline, state = _post_context(tmp_path)
    PostStage().run(context, pipeline, state)
    result = subprocess.run(
        ["rpmspec", "--parse", "--define", f"_sourcedir {context.package_dir}",
         str(context.spec_path)], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    parsed = result.stdout
    assert f"Version:        {VERSION}" in parsed
    assert f"1.{VERSION}." in parsed  # npm/v8 releases include the new node EVR.
