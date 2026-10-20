"""Shared installed-package contract, independent of application assertions."""

import json
import subprocess
from pathlib import Path

from tmt_rpmtest.environment.rpm import QUERY, Identity, identities, verify_installed
from tmt_rpmtest.execution.subject import subject


class ContractFailure(AssertionError):
    """One or more executed package checks failed."""


def check_contract(directory: Path) -> None:
    """Check a pristine prepared root and retain each observation before failing.

    Package-specific path, user, feature, and behavior expectations belong in tests.
    RPM handles omitted documentation and ghost files according to its recorded flags.
    """
    manifest = json.loads((directory / "manifest.json").read_text())
    results = []
    current_check = "U05"
    package = None

    def record(check, package, passed, detail):
        results.append(
            {
                "check": check,
                "package": package,
                "result": "pass" if passed else "fail",
                "detail": detail,
            }
        )

    try:
        with subject(directory) as target:
            inventory = target.rpm("-qa", "--qf", QUERY)
            inventory.check_returncode()
            expected = [Identity(**item) for item in manifest["installed"]]
            try:
                verify_installed(expected, identities(inventory.stdout))
            except ValueError as error:
                record("U05", None, False, str(error))
            else:
                record("U05", None, True, "Installed inventory matches resolved RPM identities.")
            for package in manifest["packages"]:
                current_check = "U06"
                verified = target.packages.verify(package)
                record("U06", package, verified.returncode == 0, verified.stdout + verified.stderr)
                current_check = "U07"
                links = target.packages.runtime_links(package)
                record(
                    "U07",
                    package,
                    all(link.resolved for link in links),
                    [
                        {
                            "path": link.path,
                            "loader": link.loader,
                            "exit_code": link.result.returncode,
                            "stdout": link.result.stdout,
                            "stderr": link.result.stderr,
                        }
                        for link in links
                    ],
                )
                if not links:
                    results[-1].update(result="not-applicable", detail="No dynamic ELF files.")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        results.append(
            {"check": current_check, "package": package, "result": "error", "detail": str(error)}
        )
        raise
    finally:
        (directory / "contract.json").write_text(json.dumps(results, indent=2) + "\n")
    failed = [item for item in results if item["result"] == "fail"]
    if failed:
        raise ContractFailure(f"Package contract failed: {failed}")
