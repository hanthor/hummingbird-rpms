#!/usr/bin/python3
"""Refresh bundled npm Provides from Thanos frontend lockfiles."""

import json
import re
import sys
import tarfile
from pathlib import Path

BEGIN = "# BEGIN generated bundled npm Provides"
END = "# END generated bundled npm Provides"
PACKAGE_NAME_RE = re.compile(r"^(?:@[^/@]+/)?[^/@]+$")


def rpm_version(version: str) -> str:
    base, separator, prerelease = version.partition("-")
    if separator:
        prerelease = prerelease.lstrip("-._+~").replace("-", ".")
        version = base + "~" + prerelease
    return re.sub(r"([._+~])\1+", r"\1", version)


def npm_packages(lock: str) -> set[tuple[str, str]]:
    provides = set()
    for path, package in json.loads(lock).get("packages", {}).items():
        version = package.get("version")
        if not path or not version or package.get("dev") or "node_modules/" not in path:
            continue
        name = package.get("name") or path.rsplit("node_modules/", 1)[1]
        if PACKAGE_NAME_RE.fullmatch(name):
            provides.add((name, version))
    return provides


def read_member(source: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    extracted = source.extractfile(member)
    if extracted is None:
        raise ValueError(f"could not read {member.name}")
    return extracted.read().decode()


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", sys.argv[1]):
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} VERSION")
    version = sys.argv[1]
    package_dir = Path(__file__).resolve().parent
    archive = package_dir / f"thanos-{version}.tar.gz"
    specs = list(package_dir.glob("thanos*.spec"))
    if len(specs) != 1:
        raise SystemExit("expected exactly one thanos*.spec")

    provides = set()
    with tarfile.open(archive, "r:gz") as source:
        lockfiles = [
            member for member in source.getmembers()
            if "/pkg/ui/react-app/" in member.name
            and member.name.endswith("package-lock.json")
        ]
        for member in lockfiles:
            lock = read_member(source, member)
            provides.update(npm_packages(lock))
    if not provides:
        raise SystemExit(f"{archive}: no frontend dependencies found")

    generated = "\n".join(
        f"Provides:       bundled(npm({name})) = {rpm_version(package_version)}"
        for name, package_version in sorted(provides)
    )
    spec = specs[0]
    text = spec.read_text()
    pattern = re.compile(rf"(?<={re.escape(BEGIN)}\n).*?(?={re.escape(END)})", re.DOTALL)
    updated, count = pattern.subn(generated + "\n", text)
    if count != 1:
        raise SystemExit(f"{spec}: expected exactly one generated block")
    spec.write_text(updated)
    print(f"wrote {len(provides)} bundled npm Provides to {spec.name}")


if __name__ == "__main__":
    main()
