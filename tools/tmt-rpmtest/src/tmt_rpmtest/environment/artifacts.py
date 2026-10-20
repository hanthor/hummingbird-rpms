"""Record complete build outputs and verify their disposition before each test."""

import argparse
import json
import subprocess
from pathlib import Path

from tmt_rpmtest.environment.integrity import digest


def capture(
    source: Path, binaries: Path, architecture: str, output: Path, oci_manifest: Path | None = None
) -> None:
    paths = {path.name: path for path in source.glob("*.rpm")}
    if not paths:
        raise ValueError("The build contains no RPM artifacts.")
    if oci_manifest:
        manifest = json.loads(oci_manifest.read_text())
        expected = {}
        for layer in manifest["layers"]:
            name = layer.get("annotations", {}).get("org.opencontainers.image.title", "")
            if not name.endswith(".rpm"):
                continue
            if Path(name).name != name or name in expected:
                raise ValueError(f"Invalid or duplicate RPM artifact title: {name!r}")
            expected[name] = layer["digest"]
        if set(expected) != set(paths):
            raise ValueError("Downloaded RPM outputs differ from the OCI artifact manifest.")
        for name, sha in expected.items():
            if sha != "sha256:" + digest(paths[name]):
                raise ValueError(f"Artifact differs from OCI manifest: {name}")
    records = []
    for name, path in sorted(paths.items()):
        subprocess.run(
            ["rpm", "-K", "--nosignature", str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        if name.endswith(".src.rpm"):
            query = subprocess.run(
                ["rpm", "-qp", "--qf", "%{SOURCEPACKAGE}", str(path)],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            if query.stdout.strip() != "1":
                raise ValueError(f"Source artifact is not an SRPM: {name}")
            disposition = "source"
            current = path
            arch = "src"
        else:
            query = subprocess.run(
                ["rpm", "-qp", "--qf", "%{ARCH}", str(path)],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            arch = query.stdout.strip()
            disposition = "selected" if arch in {architecture, "noarch"} else "foreign"
            current = binaries / name if disposition == "selected" else path
        sha = digest(path)
        if digest(current) != sha:
            raise ValueError(f"Artifact changed during staging: {name}")
        records.append(
            {
                "name": name,
                "sha256": sha,
                "rpm_digests": "verified",
                "architecture": arch,
                "disposition": disposition,
                "path": str(current.resolve()),
            }
        )
    if sum(record["disposition"] == "source" for record in records) != 1:
        raise ValueError("Exactly one source RPM is required for a build catalog.")
    output.write_text(
        json.dumps(
            {
                "version": 1,
                "architecture": architecture,
                "oci_manifest": str(oci_manifest) if oci_manifest else None,
                "artifacts": records,
            },
            indent=2,
        )
        + "\n"
    )


def verify(catalog: Path, binaries: Path, source: Path | None = None) -> None:
    data = json.loads(catalog.read_text())
    if data["version"] != 1:
        raise ValueError("Unsupported artifact catalog version.")
    selected = {
        record["name"]: record
        for record in data["artifacts"]
        if record["disposition"] == "selected"
    }
    actual = {path.name: path for path in binaries.glob("*.rpm")}
    if set(selected) != set(actual):
        raise ValueError("Candidate outputs differ from the captured build catalog.")
    for name, path in actual.items():
        if digest(path) != selected[name]["sha256"]:
            raise ValueError(f"Candidate changed after artifact acquisition: {name}")
    if source is not None:
        records = [record for record in data["artifacts"] if record["disposition"] == "source"]
        if len(records) != 1 or digest(source) != records[0]["sha256"]:
            raise ValueError("Source RPM changed after artifact acquisition.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--binaries", type=Path, required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--oci-manifest", type=Path)
    args = parser.parse_args()
    capture(args.source, args.binaries, args.architecture, args.output, args.oci_manifest)


if __name__ == "__main__":
    main()
