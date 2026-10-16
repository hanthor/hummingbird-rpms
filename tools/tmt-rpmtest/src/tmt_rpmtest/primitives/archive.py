"""Inspect paths within an archived root without following host symlinks."""

import re
import tarfile
from collections import deque
from pathlib import PurePosixPath


def glob_pattern(pattern: str) -> re.Pattern:
    if not pattern.startswith("/"):
        raise ValueError("Content patterns must be absolute.")
    pieces = re.split(r"(\*\*|\*|\?)", pattern)
    return re.compile(
        "".join(
            {"**": ".*", "*": "[^/]*", "?": "[^/]"}.get(piece, re.escape(piece)) for piece in pieces
        )
    )


class RootArchive:
    def __init__(self, archive: tarfile.TarFile):
        self.members = {str(PurePosixPath("/" + item.name.lstrip("/"))): item for item in archive}
        root = tarfile.TarInfo(".")
        root.type, root.mode = tarfile.DIRTYPE, 0o755
        self.members.setdefault("/", root)

    def resolve(self, path: str, *, follow=True) -> tuple[str, tarfile.TarInfo]:
        if not path.startswith("/"):
            raise ValueError("Subject paths must be absolute.")
        pending = deque(PurePosixPath(path).parts[1:])
        parts = []
        links = 0
        item = self.members["/"]
        while pending:
            part = pending.popleft()
            if part == "..":
                if parts:
                    parts.pop()
                continue
            candidate = "/" + "/".join([*parts, part])
            item = self.members.get(candidate)
            if item is None:
                raise FileNotFoundError(candidate)
            if (item.issym() and (follow or pending)) or item.islnk():
                links += 1
                if links > 40:
                    raise OSError("Too many links in " + path)
                target = PurePosixPath(item.linkname)
                if target.is_absolute() or item.islnk():
                    parts = []
                pending.extendleft(
                    reversed(target.parts[1:] if target.is_absolute() else target.parts)
                )
            else:
                parts.append(part)
                if pending and not item.isdir():
                    raise NotADirectoryError(candidate)
        resolved = "/" + "/".join(parts)
        item = self.members.get(resolved)
        if item is None:
            raise FileNotFoundError(resolved)
        return resolved, item
