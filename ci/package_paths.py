"""Normalize dist-git filenames and packaging references for Windows checkouts.

Apply the same transformation to every Fedora snapshot before comparing or
merging. Lookaside names and patch payloads stay unchanged. No mapping is stored.
"""

import os
import re
import tempfile
from pathlib import Path, PurePosixPath

_RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)', re.I)
_FORBIDDEN = '<>:"\\|?*'
_URL = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*://[^\s]+')
_REFERENCE_SUFFIXES = {'.spec', '.sh', '.py', '.yaml', '.yml', '.toml', '.cfg'}


def portable_component(name: str) -> str:
    """Escape Windows-invalid characters and the escape marker itself."""
    if not path_problem(name):
        return name
    trailing = len(name.rstrip(' .'))
    reserved = bool(_RESERVED.match(name))
    return ''.join(
        f'~{ord(char):02x}'
        if char in _FORBIDDEN or char == '~' or ord(char) < 32
        or (index >= trailing and char in ' .') or (reserved and index == 0)
        else char
        for index, char in enumerate(name)
    )


def path_problem(path: str) -> bool:
    """Whether a repository path violates Windows component naming rules."""
    return any(
        _RESERVED.match(part) or part.endswith((' ', '.'))
        or any(char in _FORBIDDEN or ord(char) < 32 for char in part)
        for part in PurePosixPath(path).parts
    )


def _paths(directory: Path) -> list[Path]:
    # Never descend through symlinks or into temporary Git metadata.
    result: list[Path] = []
    for root, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = [name for name in dirs if name != '.git']
        result.extend(Path(root) / name for name in dirs + files)
    return result


def _replace_references(content: str, mapping: dict[str, str], filenames: set[str]) -> str:
    # URLs retain their download location. RPM's #/ suffix supplies the local
    # filename for a tracked Source/Patch that is also declared by URL.
    aliases = {Path(old).name: Path(new).name for old, new in mapping.items()}
    # Match a declaration containing RPM macros against actual mapped files.
    # Escape its literal pieces while preserving %{version}, %{name}, etc.
    macro = re.compile(r'%\{[^{}]*\}')

    def macro_tag(match: re.Match[str]) -> str:
        prefix, location = match.groups()
        if '%{' not in location:
            return match.group()
        remote = bool(_URL.match(location))
        basename = location.rsplit('/', 1)[-1]
        pieces = macro.split(basename)
        macros = macro.findall(basename)
        pattern = '.*'.join(re.escape(piece) for piece in pieces)
        matched_names = [name for name in filenames if re.fullmatch(pattern, name)]
        if not any(name in aliases for name in matched_names):
            return match.group()
        rewritten = [portable_component(piece + ('x' if index < len(macros) else ''))
                     [:-1] if index < len(macros) else portable_component(piece)
                     for index, piece in enumerate(pieces)]
        # An empty macro can leave a reserved basename such as CON.patch.
        # Decide from actual filenames, since CON1.patch needs no prefix escape.
        if pieces[0] and rewritten[0][0] == pieces[0][0]:
            reserved = {bool(_RESERVED.match(name)) for name in matched_names}
            if len(reserved) > 1:
                raise ValueError(f'Ambiguous reserved filename in macro reference: {location!r}')
            if True in reserved:
                rewritten[0] = f'~{ord(pieces[0][0]):02x}' + rewritten[0][1:]
        local = ''.join(piece + (macros[index] if index < len(macros) else '')
                        for index, piece in enumerate(rewritten))
        if remote:
            location += '#/' + local
        else:
            location = location[:-len(basename)] + local
        return prefix + location

    content = re.sub(r'^(\s*(?:Source|Patch)\d*\s*:\s*)(\S+)', macro_tag, content, flags=re.M | re.I)
    names = dict(mapping)
    names.update(aliases)
    if not names:
        return content
    pattern = re.compile(
        _URL.pattern + r'|(?<![\w.~+-])(?:'
        + '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))
        + r')(?![\w.~+-])'
    )

    def replace(match: re.Match[str]) -> str:
        value = match.group()
        if _URL.match(value):
            # Only Source/Patch declarations use RPM's local-name URL suffix.
            start = content.rfind('\n', 0, match.start()) + 1
            if re.match(r'\s*(?:Source|Patch)\d*\s*:', content[start:match.start()], re.I):
                basename = value.rsplit('/', 1)[-1]
                if basename in aliases:
                    return value + '#/' + aliases[basename]
            return value
        return names[value]

    return pattern.sub(replace, content)


def _apply(directory: Path, mapping: dict[str, str],
           include: set[str] | None = None) -> None:
    paths = [path for path in _paths(directory)
             if include is None or path.relative_to(directory).as_posix() in include]
    # Compute every text change before moving files. Never edit patch payloads,
    # archives, or binary files.
    contents: dict[Path, str] = {}
    filenames = {path.name for path in paths if path.is_file() or path.is_symlink()}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix not in _REFERENCE_SUFFIXES and path.name != '.gitignore':
            continue
        try:
            old = path.read_bytes().decode('utf-8')
        except UnicodeDecodeError:
            continue
        new = _replace_references(old, mapping, filenames)
        if new != old:
            contents[path] = new
    for path in paths:
        if not path.is_symlink():
            continue
        link_target = os.readlink(path)
        absolute_target = Path(os.path.normpath(path.parent / link_target))
        try:
            relative_target = absolute_target.relative_to(directory).as_posix()
        except ValueError:
            continue  # Links outside the package retain their target.
        renamed_target = mapping.get(relative_target, relative_target)
        renamed_link = mapping.get(path.relative_to(directory).as_posix(), path.relative_to(directory).as_posix())
        if Path(link_target).is_absolute():
            new_target = str(directory / renamed_target)
        else:
            new_target = os.path.relpath(directory / renamed_target, (directory / renamed_link).parent)
        if link_target != new_target:
            path.unlink()
            path.symlink_to(new_target)
    for path, content in contents.items():
        path.write_bytes(content.encode('utf-8'))
    # Children move before parents. Each step changes only the last component.
    depths = sorted({name.count('/') for name in mapping}, reverse=True)
    for depth in depths:
        with tempfile.TemporaryDirectory(prefix='dist-git-paths-', dir=directory.parent) as tmpdir:
            pending: list[tuple[Path, Path]] = []
            for old, new in mapping.items():
                if old.count('/') != depth:
                    continue
                source = directory / old
                target = source.with_name(PurePosixPath(new).name)
                if source != target and (source.exists() or source.is_symlink()):
                    temporary = Path(tmpdir) / str(len(pending))
                    source.rename(temporary)
                    pending.append((temporary, target))
            for temporary, target in pending:
                temporary.rename(target)


def sanitize_package_paths(directory: Path, dry_run: bool = False,
                           include: set[str] | None = None) -> dict[str, str]:
    """Rename payload paths and packaging references; return changed paths.

    Safe names stay unchanged, making normalization idempotent. Check collisions
    before touching anything, including collisions on case-insensitive systems.
    """
    mapping = {}
    sources = directory / 'sources'
    lookaside = set(re.findall(r'^\w+ \((.+)\) = \w+$', sources.read_text(), re.M)) if sources.is_file() else set()
    destinations: dict[str, str] = {}
    for path in _paths(directory):
        relative = path.relative_to(directory).as_posix()
        if include is not None and relative not in include:
            continue
        if relative in lookaside:
            continue
        destination = '/'.join(portable_component(part) for part in PurePosixPath(relative).parts)
        key = destination.casefold()
        if key in destinations:
            raise ValueError(f'Portable path collision: {relative!r} and {destinations[key]!r}')
        destinations[key] = relative
        if destination != relative:
            mapping[relative] = destination
    if not dry_run:
        _apply(directory, mapping, include=include)
    return mapping
