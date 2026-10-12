#!/usr/bin/env python3
"""
One-time migration of metadata/<package>.json files from the old flat
Fedora-origin schema (top-level source/branch/sha/release) to ADR-0003's
nested fedora object, via hum.meta.

See HUM-6232. Not meant to be a permanent part of this repo -- it's meant to
run once, as part of the same PR that ports the real consumers onto
hum.meta, so the data migration and the schema-aware consumer code land
together.

Reshapes each raw dict's old flat keys into the new nested shape, then
delegates all classification/validation to PackageMetadata._from_raw_dict --
the same classifier MetadataRepository.load() already uses for files already
in the new schema -- rather than reimplementing that logic here.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from hum.meta import InvalidMetadataError, MalformedMetadataError, MetadataRepository  # noqa: E402
from hum.meta.model import PackageMetadata  # noqa: E402


def reshape_to_new_schema(data: dict[str, object]) -> dict[str, object]:
    """Returns a new dict with the old flat source/branch/sha/release keys
    (if present) hoisted into a nested fedora object. Every other field
    passes through unchanged. A dict with no 'source' key (an independent
    package) is returned as-is -- it has no Fedora origin to reshape.
    Raises ValueError if 'source' is present without 'branch'/'sha'."""
    data = dict(data)
    if "source" not in data:
        return data

    missing = [key for key in ("branch", "sha") if key not in data]
    if missing:
        raise ValueError(f"has 'source' but missing {missing}")

    fedora: dict[str, object] = {
        "git_url": data.pop("source"),
        "branch": data.pop("branch"),
        "sha": data.pop("sha"),
    }
    if "release" in data:
        fedora["release"] = data.pop("release")
    data["fedora"] = fedora
    return data


def migrate(root: Path, *, dry_run: bool = False) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """Migrates every metadata/<package>.json file under root to the new
    schema. Returns (migrated, skipped, failed) -- skipped is files already
    on the new schema, failed is (package_name, reason) pairs. Never raises
    on a single bad file; collects every failure instead."""
    repo = MetadataRepository(root)
    migrated: list[str] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []

    for path in sorted((root / "metadata").glob("*.json")):
        package_name = path.stem
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)

            if "fedora" in data:
                skipped.append(package_name)
                continue

            reshaped = reshape_to_new_schema(data)
            metadata = PackageMetadata._from_raw_dict(package_name, reshaped)
            result = metadata.validate()
            if result.violations:
                raise InvalidMetadataError(result)
            if not dry_run:
                repo.save(metadata)
            migrated.append(package_name)
        except (json.JSONDecodeError, OSError, ValueError,
                MalformedMetadataError, InvalidMetadataError) as exc:
            failed.append((package_name, str(exc)))

    return migrated, skipped, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would change without writing anything"
    )
    args = parser.parse_args()

    migrated, skipped, failed = migrate(ROOT_DIR, dry_run=args.dry_run)

    print(f"Migrated: {len(migrated)}")
    print(f"Already on the new schema (skipped): {len(skipped)}")
    print(f"Failed: {len(failed)}")
    for name, reason in failed:
        print(f"  {name}: {reason}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
