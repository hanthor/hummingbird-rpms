"""Check real RPM contents, including a deliberately damaged installed filesystem."""

import json
import os
import tarfile
from pathlib import Path

import pytest

from tmt_rpmtest.environment.lifecycle import finish, prepare
from tmt_rpmtest.environment.rpm import digest
from tmt_rpmtest.execution.contract import ContractFailure, check_contract

CANDIDATE = os.environ.get("RPMTEST_CURL_RPM")


@pytest.mark.skipif(not CANDIDATE, reason="Set RPMTEST_CURL_RPM for real contract checks.")
def test_contract_rejects_missing_installed_executable(tmp_path):
    directory = tmp_path / "environment"
    try:
        prepare(
            {
                "packages": ["curl"],
                "artifacts": [str(Path(CANDIDATE).resolve())],
                "repositories": [
                    "https://packages.redhat.com/api/pulp-content/public-hummingbird/$basearch/"
                ],
            },
            directory,
            tmp_path,
            publish=False,
        )
        check_contract(directory)
        archive = directory / "rootfs.tar"
        damaged = directory / "damaged.tar"
        removed = False
        with tarfile.open(archive) as source, tarfile.open(damaged, "w") as target:
            for entry in source:
                if entry.name.removeprefix("./") == "usr/bin/curl":
                    removed = True
                    continue
                with_source = source.extractfile(entry) if entry.isfile() else None
                try:
                    target.addfile(entry, with_source)
                finally:
                    if with_source is not None:
                        with_source.close()
        assert removed
        damaged.replace(archive)
        manifest = json.loads((directory / "manifest.json").read_text())
        # Exercise installed-file verification after accepting this root's artifact hash.
        manifest["rootfs_sha256"] = digest(archive)
        (directory / "manifest.json").write_text(json.dumps(manifest))
        with pytest.raises(ContractFailure, match="U06"):
            check_contract(directory)
        results = json.loads((directory / "contract.json").read_text())
        assert any(item["check"] == "U06" and item["result"] == "fail" for item in results)
    finally:
        finish(directory)
