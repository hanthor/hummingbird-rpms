"""Build catalogs reject omissions, substitutions, and OCI descriptor mismatches."""

import json
import shutil
import subprocess

import pytest

from tmt_rpmtest.environment import artifacts


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    source = tmp_path / "source"
    binaries = tmp_path / "binaries"
    source.mkdir()
    binaries.mkdir()
    for name in ("app.rpm", "app.src.rpm", "foreign.rpm"):
        (source / name).write_text(name)
    shutil.copyfile(source / "app.rpm", binaries / "app.rpm")
    monkeypatch.setattr(
        artifacts.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, "foreign" if "foreign" in argv[-1] else "noarch", ""
        ),
    )
    manifest = tmp_path / "oci.json"
    manifest.write_text(
        json.dumps(
            {
                "layers": [
                    {
                        "annotations": {"org.opencontainers.image.title": path.name},
                        "digest": "sha256:" + artifacts.digest(path),
                    }
                    for path in source.iterdir()
                ]
            }
        )
    )
    output = tmp_path / "catalog.json"
    return source, binaries, output, manifest


def test_complete_catalog_retains_selected_source_and_foreign_outputs(catalog):
    source, binaries, output, manifest = catalog
    artifacts.capture(source, binaries, "native", output, manifest)
    records = json.loads(output.read_text())["artifacts"]
    assert {record["disposition"] for record in records} == {"selected", "source", "foreign"}
    artifacts.verify(output, binaries, source / "app.src.rpm")


@pytest.mark.parametrize("failure", ["missing", "extra", "changed", "source"])
def test_acquired_outputs_cannot_change_before_testing(catalog, failure):
    source, binaries, output, manifest = catalog
    artifacts.capture(source, binaries, "native", output, manifest)
    if failure == "missing":
        (binaries / "app.rpm").unlink()
    elif failure == "extra":
        (binaries / "extra.rpm").write_text("extra")
    elif failure == "source":
        (source / "app.src.rpm").write_text("changed")
    else:
        (binaries / "app.rpm").write_text("changed")
    with pytest.raises(ValueError):
        artifacts.verify(output, binaries, source / "app.src.rpm")


@pytest.mark.parametrize("failure", ["missing", "changed", "duplicate", "unsafe"])
def test_oci_manifest_is_authoritative_for_acquisition(catalog, failure):
    source, binaries, output, manifest = catalog
    data = json.loads(manifest.read_text())
    if failure == "missing":
        (source / "foreign.rpm").unlink()
    elif failure == "changed":
        (source / "foreign.rpm").write_text("changed bytes")
    elif failure == "duplicate":
        data["layers"].append(data["layers"][0])
    else:
        data["layers"][0]["annotations"]["org.opencontainers.image.title"] = "../escape.rpm"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        artifacts.capture(source, binaries, "native", output, manifest)
    assert not output.exists()
