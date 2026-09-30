#!/usr/bin/env python3
"""Verify all bigfiles/xattr history entries carry SOURCE_DATE_EPOCH, not wall-clock time.

Usage: check_sde_clamp.py <oci-archive> <sde-rfc3339>

Exits 0 on success, 1 on failure.
"""

import json
import pathlib
import sys
import tarfile
import tempfile

if len(sys.argv) != 3:
    print(f"Usage: {sys.argv[0]} <oci-archive> <sde-rfc3339>", file=sys.stderr)
    sys.exit(1)

archive_path, sde = pathlib.Path(sys.argv[1]), sys.argv[2]

with tempfile.TemporaryDirectory() as extract_dir:
    extract = pathlib.Path(extract_dir)

    with tarfile.open(archive_path) as t:
        t.extractall(extract, filter="data")

    idx = json.loads((extract / "index.json").read_text())
    if not idx.get("manifests"):
        print("ERROR: OCI index has no manifests", file=sys.stderr)
        sys.exit(1)
    manifest_digest = idx["manifests"][0]["digest"].replace("sha256:", "blobs/sha256/")
    manifest = json.loads((extract / manifest_digest).read_text())
    config_digest = manifest["config"]["digest"].replace("sha256:", "blobs/sha256/")
    config = json.loads((extract / config_digest).read_text())

failed = False
for h in config.get("history", []):
    comment = h.get("comment", "")
    created = h.get("created", "")
    if "bigfiles/" in comment or "xattr/" in comment:
        ok = created == sde
        status = "OK" if ok else "FAIL"
        if not ok:
            failed = True
        print(f"  [{status}] {comment}: created={created} (expected {sde})")
    else:
        print(f"  [--]   {comment}: created={created}")

if failed:
    print()
    print("FAILED: bigfiles/xattr layer timestamps are wall-clock time, not SOURCE_DATE_EPOCH")
    print("This is the chunkah 0.7.0 regression (HUM-8922 / coreos/chunkah#176).")
    print("Ensure the backport patch restoring default_mtime_clamp is applied.")
    sys.exit(1)

print()
print("PASSED: all bigfiles/xattr layer timestamps match SOURCE_DATE_EPOCH")
