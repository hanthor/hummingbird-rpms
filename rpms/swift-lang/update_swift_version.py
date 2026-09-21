#!/usr/bin/python3
"""Update Swift sources and bundled RPM Provides from a release manifest."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

TextFetcher = Callable[[str], str]

# Swift's checkout manifest covers every supported platform and build workflow.
# The Fedora production preset does not use these repositories.
EXCLUDED_CHECKOUTS = frozenset(
    {
        "boringssl",
        "brotli",
        "cmake",
        "curl",
        "libxml2",
        "mimalloc",
        "ninja",
        "swift-async-algorithms",
        "swift-docc-render-artifact",
        "swift-installer-scripts",
        "swift-integration-tests",
        "swift-llvm-bindings",
        "swift-log",
        "swift-numerics",
        "swift-sdk-generator",
        "swift-stress-tester",
        "swift-subprocess",
        "swift-xcode-playground-support",
        "wasi-libc",
        "wasmkit",
        "zlib",
    }
)

RUNTIME_CHECKOUTS = frozenset(
    {
        "swift-corelibs-blocksruntime",
        "swift-corelibs-foundation",
        "swift-corelibs-libdispatch",
        "swift-corelibs-xctest",
        "swift-experimental-string-processing",
        "swift-foundation",
        "swift-foundation-icu",
        "swift-testing",
    }
)

PROVIDE_NAMES = {
    "cmark": "cmark-gfm",
    "llbuild": "swift-llbuild",
    "llvm-project": "llvm",
    "swiftpm": "swift-package-manager",
}

VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
DIRECT_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:[._+-][0-9A-Za-z.-]+)?$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REMOTE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RELEASE_REF_RE = re.compile(r"^(?:swift/)?release/|^swift-[0-9].*-RELEASE$")


class UpdateError(RuntimeError):
    """The release metadata cannot produce a safe package update."""


@dataclass(frozen=True)
class CheckoutConfig:
    scheme: str
    remote_ids: Mapping[str, str]
    refs: Mapping[str, str]

    def remote_id(self, project: str) -> str:
        try:
            remote_id = self.remote_ids[project]
        except KeyError as error:
            raise UpdateError(
                f"checkout configuration has no remote ID for {project}"
            ) from error
        if not REMOTE_ID_RE.fullmatch(remote_id):
            raise UpdateError(f"invalid remote ID for {project}: {remote_id}")
        return remote_id

    def ref(self, project: str) -> str:
        try:
            return self.refs[project]
        except KeyError as error:
            raise UpdateError(f"{self.scheme} has no ref for {project}") from error


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "swift-rpm-updater"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        raise UpdateError(f"failed to download {url}: {error}") from error
    except UnicodeDecodeError as error:
        raise UpdateError(f"downloaded file is not UTF-8: {url}") from error


def _string_mapping(value: object, description: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise UpdateError(f"{description} is not an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str) or not item:
            raise UpdateError(f"{description} contains an invalid entry")
        result[key] = item
    return result


def parse_checkout_config(text: str, version: str) -> CheckoutConfig:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise UpdateError(f"checkout configuration is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise UpdateError("checkout configuration is not an object")

    branch_schemes = document.get("branch-schemes")
    if not isinstance(branch_schemes, dict):
        raise UpdateError("checkout configuration has no branch-schemes object")
    exact_scheme = f"release/{version}"
    fallback_scheme = f"release/{version.rsplit('.', 1)[0]}"
    exact_data = branch_schemes.get(exact_scheme)
    scheme = (
        exact_scheme
        if isinstance(exact_data, dict) and isinstance(exact_data.get("repos"), dict)
        else fallback_scheme
    )
    scheme_data = branch_schemes.get(scheme)
    if not isinstance(scheme_data, dict):
        raise UpdateError(f"checkout configuration has no {scheme} scheme")
    refs = _string_mapping(scheme_data.get("repos"), f"{scheme} repos")

    repos = document.get("repos")
    if not isinstance(repos, dict):
        raise UpdateError("checkout configuration has no repos object")
    remote_ids: dict[str, str] = {}
    for project, repo_data in repos.items():
        if not isinstance(project, str) or not isinstance(repo_data, dict):
            raise UpdateError("checkout configuration contains an invalid repository")
        remote = repo_data.get("remote")
        remote_id = remote.get("id") if isinstance(remote, dict) else None
        if isinstance(remote_id, str) and remote_id:
            remote_ids[project] = remote_id

    return CheckoutConfig(scheme=scheme, remote_ids=remote_ids, refs=refs)


def _archive_ref(ref: str, version: str) -> str:
    if RELEASE_REF_RE.search(ref):
        return f"swift-{version}-RELEASE"
    return ref


def _project_file_url(
    config: CheckoutConfig, version: str, project: str, path: str
) -> str:
    ref = _archive_ref(config.ref(project), version)
    remote_id = config.remote_id(project)
    quoted_ref = urllib.parse.quote(ref, safe="/")
    quoted_path = urllib.parse.quote(path, safe="/")
    return f"https://raw.githubusercontent.com/{remote_id}/{quoted_ref}/{quoted_path}"


def _one_match(pattern: str, text: str, description: str) -> str:
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    if len(matches) != 1 or not matches[0]:
        raise UpdateError(f"could not read one {description}")
    return matches[0]


def _cmake_number(text: str, name: str) -> str:
    return _one_match(
        rf"^[ \t]*set\({re.escape(name)}[ \t]+([0-9]+)\)[ \t]*$",
        text,
        name,
    )


def _define_number(text: str, name: str) -> str:
    return _one_match(
        rf"^[ \t]*#[ \t]*define[ \t]+{re.escape(name)}[ \t]+([0-9]+).*$",
        text,
        name,
    )


def resolve_nested_versions(
    config: CheckoutConfig, version: str, fetch: TextFetcher
) -> dict[str, str]:
    def project_file(project: str, path: str) -> str:
        return fetch(_project_file_url(config, version, project, path))

    llvm_text = project_file("llvm-project", "cmake/Modules/LLVMVersion.cmake")
    llvm = ".".join(
        _cmake_number(llvm_text, name)
        for name in (
            "LLVM_VERSION_MAJOR",
            "LLVM_VERSION_MINOR",
            "LLVM_VERSION_PATCH",
        )
    )
    cmark = _one_match(
        r'^[ \t]*#[ \t]*define[ \t]+CMARK_GFM_VERSION_STRING[ \t]+"([0-9]+(?:\.[0-9]+)*\.gfm\.[0-9]+)"[ \t]*$',
        project_file("cmark", "src/include/cmark-gfm_version.h"),
        "cmark-gfm version",
    )
    icu = _one_match(
        r'^[ \t]*#[ \t]*define[ \t]+U_ICU_VERSION[ \t]+"([0-9]+(?:\.[0-9]+){1,3})".*$',
        project_file(
            "swift-foundation-icu",
            "icuSources/include/_foundation_unicode/uvernum.h",
        ),
        "ICU version",
    )
    lmdb_text = project_file("swift-lmdb", "libraries/liblmdb/lmdb.h")
    lmdb = ".".join(
        _define_number(lmdb_text, name)
        for name in (
            "MDB_VERSION_MAJOR",
            "MDB_VERSION_MINOR",
            "MDB_VERSION_PATCH",
        )
    )
    boringssl = _one_match(
        r"^This directory is derived from BoringSSL cloned from https://boringssl\.googlesource\.com/boringssl at revision ([0-9a-f]{40})[ \t]*$",
        project_file("swift-crypto", "Sources/CCryptoBoringSSL/hash.txt"),
        "BoringSSL revision",
    )
    return {
        "boringssl": boringssl,
        "cmark": cmark,
        "icu": icu,
        "llvm-project": llvm,
        "lmdb": lmdb,
    }


def _direct_version(config: CheckoutConfig, project: str) -> str:
    ref = config.ref(project)
    if RELEASE_REF_RE.search(ref):
        return "%{version}"
    version = ref.removeprefix("v")
    if not DIRECT_VERSION_RE.fullmatch(version):
        raise UpdateError(
            f"cannot derive a release version for {project} from ref '{ref}'"
        )
    return version


def retained_projects(config: CheckoutConfig) -> list[str]:
    return sorted(
        project
        for project in config.refs
        if project != "swift" and project not in EXCLUDED_CHECKOUTS
    )


def generate_provides(
    config: CheckoutConfig, projects: Sequence[str], nested: Mapping[str, str]
) -> str:
    main: set[str] = set()
    runtime: set[str] = set()
    for project in projects:
        name = PROVIDE_NAMES.get(project, project)
        version = nested[project] if project in nested else _direct_version(config, project)
        target = runtime if project in RUNTIME_CHECKOUTS else main
        target.add(f"Provides:       bundled({name}) = {version}")

    llvm = nested["llvm-project"]
    main.update(
        {
            f"Provides:       bundled(boringssl) = {nested['boringssl']}",
            f"Provides:       bundled(clang) = {llvm}",
            f"Provides:       bundled(compiler-rt) = {llvm}",
            f"Provides:       bundled(lld) = {llvm}",
            f"Provides:       bundled(lldb) = {llvm}",
            f"Provides:       bundled(lmdb) = {nested['lmdb']}",
        }
    )
    runtime.add(f"Provides:       bundled(icu) = {nested['icu']}")

    return "\n".join(
        [
            "# Generated by update_swift_version.py. Do not edit.",
            "",
            "%if 0%{?swift_bundled_provides_main}",
            *sorted(main),
            "%endif",
            "",
            "%if 0%{?swift_bundled_provides_runtime}",
            *sorted(runtime),
            "%endif",
            "",
        ]
    )


def generate_source_block(config: CheckoutConfig, projects: Sequence[str]) -> str:
    lines: list[str] = []
    for index, project in enumerate(projects, start=1):
        if not PROJECT_RE.fullmatch(project):
            raise UpdateError(f"invalid project name: {project}")
        ref = config.ref(project)
        if RELEASE_REF_RE.search(ref):
            ref = "swift-%{version0}-RELEASE"
        macro = project.replace("-", "_")
        lines.extend(
            [
                f"%global source_{macro} {index}",
                f"%global forgeurl{index}  https://github.com/{config.remote_id(project)}",
                f"%global tag{index}       {ref}",
                f"%global subdir{index}    {project}",
                "",
            ]
        )
    lines.extend([f"%global forge_source_last {len(projects)}", ""])
    return "\n".join(lines)


def _replace_block(text: str, begin: str, end: str, replacement: str) -> str:
    lines = text.splitlines(keepends=True)
    begin_indexes = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == begin]
    end_indexes = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == end]
    if len(begin_indexes) != 1 or len(end_indexes) != 1:
        raise UpdateError(f"expected one '{begin}' and one '{end}'")
    begin_index = begin_indexes[0]
    end_index = end_indexes[0]
    if end_index <= begin_index:
        raise UpdateError(f"'{end}' must follow '{begin}'")
    replacement_lines = replacement.splitlines(keepends=True)
    return "".join(
        lines[: begin_index + 1] + replacement_lines + lines[end_index:]
    )


def update_spec(text: str, version: str, source_block: str) -> str:
    updated = _replace_block(
        text, "# Begin forge sources", "# End forge sources", source_block
    )
    updated, count = re.subn(
        r"^Version:[ \t]*.*$", f"Version: {version}", updated, flags=re.MULTILINE
    )
    if count != 1:
        raise UpdateError("expected one Version field in the spec")
    return updated


def _write_atomic(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        temporary.chmod(mode)
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def update_swift_version(
    version: str,
    spec_path: Path,
    *,
    provides_path: Path | None = None,
    fetch: TextFetcher = fetch_text,
) -> None:
    if not VERSION_RE.fullmatch(version):
        raise UpdateError(f"invalid Swift version: {version}")
    try:
        spec_text = spec_path.read_text(encoding="utf-8")
    except OSError as error:
        raise UpdateError(f"cannot read spec file {spec_path}: {error}") from error

    tag = f"swift-{version}-RELEASE"
    config_url = (
        f"https://raw.githubusercontent.com/swiftlang/swift/{tag}/"
        "utils/update_checkout/update-checkout-config.json"
    )
    print(f"Downloading Swift {version} configuration...")
    config = parse_checkout_config(fetch(config_url), version)
    projects = retained_projects(config)

    print("Resolving bundled dependency versions...")
    nested = resolve_nested_versions(config, version, fetch)
    provides = generate_provides(config, projects, nested)

    print("Generating forge sources...")
    source_block = generate_source_block(config, projects)
    updated_spec = update_spec(spec_text, version, source_block)

    output_provides = provides_path or spec_path.parent / "bundled-provides.inc"
    try:
        _write_atomic(output_provides, provides)
        _write_atomic(spec_path, updated_spec)
    except OSError as error:
        raise UpdateError(f"cannot write package files: {error}") from error
    print(f"Updated {spec_path} and {output_provides} for Swift {version}.")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Swift release version, for example 6.4.0")
    parser.add_argument(
        "--spec",
        type=Path,
        default=Path(os.environ.get("SWIFT_SPEC_FILE", "swift-lang.spec")),
        help="spec file to update",
    )
    parser.add_argument(
        "--provides",
        type=Path,
        default=(
            Path(os.environ["SWIFT_PROVIDES_FILE"])
            if "SWIFT_PROVIDES_FILE" in os.environ
            else None
        ),
        help="generated bundled Provides include",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        update_swift_version(
            args.version,
            args.spec,
            provides_path=args.provides,
        )
    except UpdateError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
