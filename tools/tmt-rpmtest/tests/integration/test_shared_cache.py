"""A different package request succeeds from the shared cache with no network."""

import json
import os

import pytest

from tmt_rpmtest.drivers.podman import Podman
from tmt_rpmtest.environment.cache import prepare_root
from tmt_rpmtest.environment.rpm import RPMBuilder


@pytest.mark.skipif(os.environ.get("RPMTEST_LIVE") != "1", reason="Set RPMTEST_LIVE=1.")
def test_overlapping_roots_reuse_downloads_offline_without_leaking_packages(tmp_path):
    class Runtime(Podman):
        offline = False

        def build(self, *args, **kwargs):
            if self.offline:
                kwargs["internet"] = False
            return super().build(*args, **kwargs)

    runtime = Runtime()
    try:
        builder = RPMBuilder(runtime)
        settings = {
            "packages": ["curl", "python3"],
            "repositories": [
                "https://packages.redhat.com/api/pulp-content/public-hummingbird/$basearch/"
            ],
        }
        prepare_root(builder, settings, tmp_path, tmp_path / "large", tmp_path / "cache")
        runtime.offline = True
        settings["packages"] = ["curl"]
        info = prepare_root(builder, settings, tmp_path, tmp_path / "small", tmp_path / "cache")
        assert not info["hit"]  # Different package set must assemble a different root.
        manifest = json.loads((tmp_path / "small/manifest.json").read_text())
        assert "python3" not in {item["name"] for item in manifest["installed"]}
        assert manifest["resolution"]["downloaded_rpms"] == 0
        assert manifest["resolution"]["reused_rpms"] > 0
        assert prepare_root(builder, settings, tmp_path, tmp_path / "again", tmp_path / "cache")[
            "hit"
        ]
    finally:
        runtime.cleanup()
