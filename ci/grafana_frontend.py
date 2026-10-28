#!/usr/bin/env python3
"""Prepare Grafana's patched Yarn graph and its release-specific RPM patch."""

import argparse
import difflib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def generate(package_dir: Path, source_dir: Path, version: str) -> None:
    config = json.loads((package_dir / "frontend-overrides.json").read_text())
    paths = [*config["manifests"], ".yarnrc.yml", "yarn.lock", *config["assets"]]
    before = {name: (source_dir / name).read_text() if (source_dir / name).exists() else None
              for name in paths}

    for name, sections in config["manifests"].items():
        path = source_dir / name
        manifest = json.loads(path.read_text())
        for section, changes in sections.items():
            manifest.setdefault(section, {}).update(changes)
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    yarnrc = source_dir / ".yarnrc.yml"
    updated, count = re.subn(r"(?m)^compressionLevel:.*$", "compressionLevel: 0", yarnrc.read_text())
    if count != 1:
        raise ValueError("expected one compressionLevel in .yarnrc.yml")
    yarnrc.write_text(updated)

    for name, asset in config["assets"].items():
        destination = source_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package_dir / asset, destination)

    yarn_releases = list((source_dir / ".yarn/releases").glob("yarn-*.cjs"))
    if len(yarn_releases) != 1:
        raise ValueError(f"expected one Yarn release, found {len(yarn_releases)}")
    env = os.environ.copy()
    env.update(YARN_ENABLE_GLOBAL_CACHE="false", YARN_CACHE_FOLDER=".yarn/cache",
               YARN_ENABLE_IMMUTABLE_INSTALLS="false", YARN_NPM_MINIMAL_AGE_GATE="0")
    if package_dir.name == "grafana12.4":
        env["CYPRESS_INSTALL_BINARY"] = "0"
    subprocess.run(["node", str(yarn_releases[0]), "install"], cwd=source_dir, env=env, check=True)

    patch: list[str] = []
    for name in paths:
        previous = before[name]
        current = (source_dir / name).read_text()
        if previous == current:
            continue
        patch.extend(difflib.unified_diff(
            [] if previous is None else previous.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile="/dev/null" if previous is None else f"a/{name}",
            tofile=f"b/{name}",
        ))
    if not patch:
        raise ValueError("no frontend changes to generate")
    (source_dir / f"grafana-frontend-{version}.patch").write_text("".join(patch))


def activate_spec(package_dir: Path, version: str) -> None:
    """Switch from the old backport stack after the new source is published."""
    config = json.loads((package_dir / "frontend-overrides.json").read_text())
    spec = package_dir / f"{package_dir.name}.spec"
    original = spec.read_text()
    if not re.search(rf"(?m)^Version:\s+{re.escape(version)}\s*$", original):
        raise ValueError(f"{spec.name} is not at version {version}")
    legacy = set(config["legacy_patches"])
    lines = original.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = re.match(r"Patch(\d+):", line)
        if match and int(match.group(1)) in legacy:
            lines[index] = "#" + line
    number = config["frontend_patch_number"]
    if not any(re.match(rf"Patch{number}:", line) for line in lines):
        last_patch = max(index for index, line in enumerate(lines)
                         if re.match(r"#?Patch\d+:", line))
        lines.insert(last_patch + 1,
                     f"Patch{number}:        grafana-frontend-%{{version}}.patch\n")
    spec.write_text("".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_dir", type=Path)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("version")
    parser.add_argument("mode", choices=("generate", "apply", "activate-spec"))
    args = parser.parse_args()
    package_dir = args.package_dir.resolve()
    source_dir = args.source_dir.resolve()
    if args.mode == "generate":
        generate(package_dir, source_dir, args.version)
    elif args.mode == "activate-spec":
        activate_spec(package_dir, args.version)
    else:
        patch = package_dir / f"grafana-frontend-{args.version}.patch"
        subprocess.run(["patch", "--batch", "--forward", "-p1", "-i", str(patch)],
                       cwd=source_dir, check=True)


if __name__ == "__main__":
    main()
