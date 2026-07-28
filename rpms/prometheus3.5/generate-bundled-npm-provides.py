#!/usr/bin/python3
"""Refresh bundled npm Provides from Prometheus frontend lockfiles."""

import ast
import json
import re
import sys
import tarfile
from pathlib import Path

BEGIN = "# BEGIN generated bundled npm Provides"
END = "# END generated bundled npm Provides"
PNPM_KEY_RE = re.compile(r"^  (\S.*):$")
PACKAGE_NAME_RE = re.compile(r"^(?:@[^/@]+/)?[^/@]+$")


def rpm_version(version: str) -> str:
    base, separator, prerelease = version.partition("-")
    if separator:
        prerelease = prerelease.lstrip("-._+~").replace("-", ".")
        version = base + "~" + prerelease
    return re.sub(r"([._+~])\1+", r"\1", version)


def unquote(value: str) -> str:
    if value.startswith(("'", '"')):
        parsed = ast.literal_eval(value)
        if not isinstance(parsed, str):
            raise ValueError(f"invalid pnpm package key: {value}")
        return parsed
    return value


def pnpm_packages(lock: str) -> set[tuple[str, str]]:
    packages = False
    provides = set()
    for line in lock.splitlines():
        if line == "packages:":
            packages = True
            continue
        if packages and line and not line.startswith(" "):
            break
        if not packages or not (match := PNPM_KEY_RE.fullmatch(line)):
            continue
        key = unquote(match.group(1))
        name, separator, version = key.rpartition("@")
        if not separator or not PACKAGE_NAME_RE.fullmatch(name) or not version:
            raise ValueError(f"unsupported pnpm package key: {key}")
        provides.add((name, version))
    return provides


def npm_packages(lock: str) -> set[tuple[str, str]]:
    provides = set()
    for path, package in json.loads(lock).get("packages", {}).items():
        version = package.get("version")
        if not path or not version or "node_modules/" not in path:
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
    archive = package_dir / f"prometheus-{version}.tar.gz"
    specs = list(package_dir.glob("prometheus*.spec"))
    if len(specs) != 1:
        raise SystemExit("expected exactly one prometheus*.spec")

    provides = set()
    with tarfile.open(archive, "r:gz") as source:
        lockfiles = [
            member for member in source.getmembers()
            if "/web/ui/" in member.name
            and member.name.endswith(("package-lock.json", "pnpm-lock.yaml"))
        ]
        for member in lockfiles:
            lock = read_member(source, member)
            if member.name.endswith("package-lock.json"):
                provides.update(npm_packages(lock))
            else:
                provides.update(pnpm_packages(lock))
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
