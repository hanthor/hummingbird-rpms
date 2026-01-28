#!/usr/bin/env python3
"""
Split import.json into per-package import.json files.

This script reads import.json and creates rpms/<package_name>/import.json
for each package.
"""

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
IMPORT_JSON = ROOT_DIR / 'import.json'
RPMS_DIR = ROOT_DIR / 'rpms'


def main():
    # Load import.json
    if not IMPORT_JSON.exists():
        sys.exit(f"ERROR: {IMPORT_JSON} not found")

    with open(IMPORT_JSON) as f:
        imports = json.load(f)

    print(f"Found {len(imports)} packages in import.json")

    created = 0
    skipped = 0
    missing_dir = 0

    for package_name, metadata in sorted(imports.items()):
        package_dir = RPMS_DIR / package_name
        import_json = package_dir / 'import.json'

        if not package_dir.exists():
            print(f"WARNING: Package directory {package_dir} does not exist, skipping {package_name}")
            missing_dir += 1
            continue

        if import_json.exists():
            print(f"WARNING: {import_json} already exists, skipping {package_name}")
            skipped += 1
            continue

        # Write import.json
        with open(import_json, 'w') as f:
            json.dump(metadata, f, indent=2, sort_keys=True)
            f.write('\n')  # Ensure trailing newline

        created += 1

    print("\nSummary:")
    print(f"  Created: {created} import.json files")
    print(f"  Skipped (already exists): {skipped}")
    print(f"  Skipped (missing directory): {missing_dir}")


if __name__ == '__main__':
    main()
