"""Run the installed-package contract for every candidate, without a functional suite."""

import argparse
import configparser
import json
import os
import platform
import signal
import subprocess
import uuid
from pathlib import Path

from tmt_rpmtest.environment.artifacts import verify as verify_artifacts
from tmt_rpmtest.environment.lifecycle import finish, prepare
from tmt_rpmtest.environment.rpm import digest
from tmt_rpmtest.execution.contract import ContractFailure, check_contract
from tmt_rpmtest.execution.coverage import functional_coverage as functional_coverage
from tmt_rpmtest.execution.rollout import decision, report_skip


def repositories(directory: Path) -> list[str]:
    """Read enabled dependency repositories used by the existing RPM checks."""
    configuration = configparser.ConfigParser(interpolation=None)
    configuration.read(sorted(directory.glob("*.repo")))
    urls = [
        section["baseurl"].replace("$arch", "$basearch")
        for section in sorted(
            configuration.values(), key=lambda section: section.getint("priority", fallback=99)
        )
        if "baseurl" in section and section.getboolean("enabled", fallback=True)
    ]
    if not urls:
        raise ValueError("No enabled dependency repositories were configured.")
    return urls


def candidates(directory: Path) -> list[tuple[Path, str]]:
    """Reject empty, foreign-architecture, and ambiguous native candidate sets."""
    selected = []
    names = set()
    for path in sorted(directory.glob("*.rpm")):
        result = subprocess.run(
            ["rpm", "-qp", "--qf", "%{NAME}\\t%{ARCH}", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        name, architecture = result.stdout.split("\t")
        if architecture not in {"noarch", platform.machine()}:
            raise ValueError(f"Unexpected candidate architecture: {path}: {architecture}")
        if name in names:
            raise ValueError(f"Ambiguous candidate package: {name}")
        names.add(name)
        selected.append((path.resolve(), name))
    if not selected:
        raise ValueError("No native/noarch candidates were supplied for universal checks.")
    return selected


def check_source(source: Path, selected: list[tuple[Path, str]], package: str | None) -> None:
    """Validate the source artifact and each binary's source reference."""
    subprocess.run(
        ["rpm", "-K", "--nosignature", str(source)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    query = subprocess.run(
        ["rpm", "-qp", "--qf", "%{NAME}\\t%{NAME}-%{VERSION}-%{RELEASE}.src.rpm", str(source)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    source_name, expected = query.stdout.split("\t")
    if package is not None and source_name != package:
        raise ContractFailure(f"Source package {source_name!r} does not match {package!r}.")
    for path, _ in selected:
        query = subprocess.run(
            ["rpm", "-qp", "--qf", "%{SOURCERPM}", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if query.stdout != expected:
            raise ContractFailure(
                f"{path.name} references {query.stdout!r}; expected {expected!r}."
            )


def execute(
    artifacts: Path,
    directory: Path,
    repository_urls: list[str],
    *,
    source: Path | None = None,
    package: str | None = None,
    cache: Path | None = None,
    catalog: Path | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    results = []
    try:
        try:
            if catalog is not None:
                verify_artifacts(catalog, artifacts, source)
            selected = candidates(artifacts)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            results.append({"check": "U03", "result": "error", "detail": str(error)})
            raise
        paths = [str(path) for path, _ in selected]
        hashes = {path: digest(Path(path)) for path in paths}
        if source is not None:
            source_result = {"check": "U02", "artifact": str(source), "result": "error"}
            results.append(source_result)
            try:
                check_source(source, selected, package)
            except ContractFailure as error:
                source_result.update(result="fail", detail=str(error))
                raise
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                source_result.update(detail=str(error))
                raise
            source_result.update(result="pass", sha256=digest(source))
        for path, package in selected:
            environment = directory / f"profile-{uuid.uuid4().hex}"
            result = {
                "package": package,
                "artifact": str(path),
                "sha256": hashes[str(path)],
                "environment": str(environment),
                "result": "error",
            }
            results.append(result)
            try:
                prepare(
                    {
                        "packages": [package],
                        "repositories": repository_urls,
                        "artifacts": paths,
                        "expected_artifacts": hashes,
                    },
                    environment,
                    Path.cwd(),
                    publish=False,
                    cache=cache or directory.parent / "rpmtest-cache",
                )
                manifest = json.loads((environment / "manifest.json").read_text())
                if not any(
                    candidate["name"] == package
                    and candidate["selected"]
                    and candidate["sha256"] == hashes[str(path)]
                    for candidate in manifest["candidates"]
                ):
                    raise ContractFailure(f"Candidate {package} was not installed.")
                check_contract(environment)
                result["result"] = "pass"
            except ContractFailure as error:
                result.update(result="fail", detail=str(error))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                result.update(result="error", detail=str(error))
            finally:
                try:
                    finish(environment)
                except (OSError, subprocess.SubprocessError, ExceptionGroup) as error:
                    result.update(result="error", cleanup=str(error))
    finally:
        (directory / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    if any(result["result"] != "pass" for result in results):
        raise ContractFailure(f"Universal package checks failed; see {directory / 'results.json'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--repos", type=Path, default=Path("ci/repos"))
    parser.add_argument("--tests", type=Path, default=Path("test/rpmtest"))
    parser.add_argument("--component", default=os.environ.get("IMAGE_NAME"))
    parser.add_argument("--source", type=Path, help="SRPM produced by this build.")
    parser.add_argument("--package", help="Expected source package directory name.")
    parser.add_argument("--cache", type=Path, help="Shared run cache for roots and RPM downloads.")
    parser.add_argument(
        "--catalog", type=Path, help="Build catalog captured at artifact acquisition."
    )
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt("Universal checks interrupted.")

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGHUP, interrupt)
    policy = None
    if args.component:
        policy = decision(args.component, args.tests)
        if not policy["enabled"]:
            args.directory.mkdir(parents=True, exist_ok=False)
            (args.directory / "rollout.json").write_text(json.dumps(policy, indent=2) + "\n")
            report_skip(f"rpmtest rollout {policy['mode']}: {args.component} is not enabled.")
            return
    coverage = functional_coverage(args.tests, args.component) if args.component else None
    if policy:
        if not coverage or coverage["result"] == "missing":
            args.directory.mkdir(parents=True, exist_ok=False)
            (args.directory / "coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
            (args.directory / "rollout.json").write_text(json.dumps(policy, indent=2) + "\n")
            raise ContractFailure(f"No functional scenarios declared for {args.component}.")
    try:
        execute(
            args.artifacts,
            args.directory,
            repositories(args.repos),
            source=args.source,
            package=args.package,
            cache=args.cache,
            catalog=args.catalog,
        )
    finally:
        if policy is not None and args.directory.exists():
            (args.directory / "rollout.json").write_text(json.dumps(policy, indent=2) + "\n")
        if coverage is not None and args.directory.exists():
            (args.directory / "coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
            count = len(coverage["scenarios"])
            print(f"Functional coverage: {coverage['result']} ({count} scenarios)")
    print(f"Universal checks passed for {os.environ.get('RPMTEST_PACKAGE', 'build candidates')}.")


if __name__ == "__main__":
    main()
