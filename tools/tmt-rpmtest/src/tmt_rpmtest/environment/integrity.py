"""Content identities shared by roots, RPMs, and repository snapshots."""

import hashlib
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()
