#!/usr/bin/env python3
"""
Audit non-native packages by source tarball complexity (HUM-4620).

Classifies every non-native package into one of:

- ``already_migrated``: has a ``metadata/<package>.source-pipeline.yaml`` --
  handled by the ongoing gorget migration (HUM-4823/HUM-4623), not in scope
  here. Some of these (e.g. nodejs) now have a bare-filename ``Source:`` with
  no URL at all since gorget's pipeline took over fetching, which would
  otherwise misclassify as ``no_upstream_url``.
- ``no_upstream_url``: Source0 doesn't resolve to a fetchable http(s)/ftp
  URL (Fedora-internal macros, dist-git-only sources, empty sources file).
- ``trivial``: a fresh fetch of Source0 matches the checksum already
  recorded in ``sources`` -- Fedora's tarball is byte-identical to
  upstream's own release artifact.
- ``transformed``: fetched, but the checksum doesn't match (or the filename
  isn't in ``sources`` at all) -- Fedora modifies the tarball before
  uploading it.
- ``error``: the fetch itself failed (network error, timeout, 404) --
  needs manual follow-up, not a real category.

Reuses check_upstream_versions.py's spec-parsing/download primitives rather
than reimplementing them: `_get_spec_sources` (rpm-macro/Lua-expanded
source locations via the `specfile` library), `_get_spec_source_urls`
(filters to fetchable locations), `_download_file`, `_compute_file_hash`.

Only one real network fetch per package is needed (Source0's resolved
upstream URL) -- the checksum already recorded in the package's local
``sources`` file *is* the "what Fedora's lookaside cache has" data point,
so there's no need to separately download from there too.

Resumable: results are appended to a JSONL scratch file as each package
completes; re-running skips packages already present there.

Usage:
    ./ci/audit_source_tarballs.py run [--workers N] [--scratch PATH]
    ./ci/audit_source_tarballs.py aggregate [--scratch PATH] [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from check_upstream_versions import (
    METADATA_DIR,
    RPMS_DIR,
    _compute_file_hash,
    _download_file,
    _get_spec_source_urls,
    _get_spec_sources,
    _parse_sources_file,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("audit_source_tarballs")

DEFAULT_SCRATCH = Path(tempfile.gettempdir()) / "source-tarball-audit.jsonl"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent.parent
    / "documentation"
    / "design"
    / "source-tarball-audit.json"
)

# Legacy Fedora sources files predate the "ALGO (filename) = hash" format
# _parse_sources_file expects -- a handful of packages (jbigkit, opendbx,
# php-patchwork-jsqueeze, rubypick, ttmkfdir, unzip, zip) still have the old
# bare "<md5> <filename>" lines.
_LEGACY_SOURCES_RE = re.compile(r"^([0-9a-f]{32})\s+(.+)$")


def _parse_sources_file_with_legacy_fallback(sources_path: Path) -> list[dict]:
    entries = _parse_sources_file(sources_path)
    if entries or not sources_path.exists():
        return entries
    for line in sources_path.read_text().splitlines():
        match = _LEGACY_SOURCES_RE.match(line.strip())
        if match:
            entries.append({"algo": "MD5", "filename": match.group(2), "hash": match.group(1)})
    return entries


def is_native(package: str) -> bool:
    metadata_path = METADATA_DIR / f"{package}.json"
    if not metadata_path.is_file():
        return False
    try:
        data = json.loads(metadata_path.read_text())
    except json.JSONDecodeError:
        return False
    return data.get("modification_status") == "native"


def enumerate_packages() -> list[str]:
    return sorted(
        p.name for p in RPMS_DIR.iterdir() if p.is_dir() and not is_native(p.name)
    )


_GNU_FTP_RE = re.compile(r"^(?:https?|ftp)://ftp\.gnu\.org/(?:pub/)?gnu/(.*)$")


def _prefer_gnu_mirror(url: str) -> str:
    """ftp.gnu.org has a well-documented history of unreliability -- outages,
    IPv6 slowness, and multi-minute hangs reported against it for years across
    unrelated projects (Spack, Guix, Conan Center), not specific to any one
    network. GNU's own guidance recommends ftpmirror.gnu.org instead, which
    redirects to a current, nearby mirror. Confirmed empirically 2026-08-06:
    ftp.gnu.org was unreachable from two independent networks investigating
    bash's "transformed" result, while ftpmirror.gnu.org fetched the same
    package cleanly and reproducibly.

    Matches all three URL shapes seen across specs in this repo:
    http/https/ftp scheme, and an optional "/pub/" before "/gnu/" (e.g.
    bash uses https://ftp.gnu.org/gnu/..., grep uses
    https://ftp.gnu.org/pub/gnu/..., automake uses
    ftp://ftp.gnu.org/gnu/...). ftpmirror.gnu.org's own URL scheme drops
    both the scheme-specific quirks and the "/gnu/"+"/pub/" prefix entirely.
    """
    match = _GNU_FTP_RE.match(url)
    if match:
        return f"https://ftpmirror.gnu.org/{match.group(1)}"
    return url


def classify_package(package: str) -> dict:
    result: dict = {"package": package}

    if (METADATA_DIR / f"{package}.source-pipeline.yaml").is_file():
        result["category"] = "already_migrated"
        return result

    package_dir = RPMS_DIR / package
    spec_files = list(package_dir.glob("*.spec"))
    if len(spec_files) != 1:
        result["category"] = "error"
        result["error"] = f"expected exactly one .spec file, found {len(spec_files)}"
        return result
    spec_path = spec_files[0]

    try:
        sources = _get_spec_sources(str(spec_path), str(package_dir))
        fetchable = _get_spec_source_urls(str(spec_path), str(package_dir), sources)
    except Exception as exc:  # spec parsing itself can fail for odd specs
        result["category"] = "error"
        result["error"] = f"spec parsing failed: {exc}"
        return result

    source0 = sources.get(0)
    result["source0"] = source0

    if 0 not in fetchable:
        result["category"] = "no_upstream_url"
        return result

    url = _prefer_gnu_mirror(fetchable[0])
    recorded_entries = _parse_sources_file_with_legacy_fallback(package_dir / "sources")
    filename = url.rsplit("/", 1)[-1]
    recorded = next((e for e in recorded_entries if e["filename"] == filename), None)

    with tempfile.TemporaryDirectory(prefix="audit-") as tmp_dir:
        dest = Path(tmp_dir) / filename
        try:
            _download_file(url, dest)
        except Exception as exc:
            result["category"] = "error"
            result["error"] = f"download failed: {exc}"
            return result

        if recorded is None:
            result["category"] = "transformed"
            result["note"] = f"{filename!r} not found in recorded sources"
            return result

        computed = _compute_file_hash(dest, recorded["algo"])
        result["recorded_hash"] = recorded["hash"]
        result["computed_hash"] = computed
        result["category"] = "trivial" if computed.lower() == recorded["hash"].lower() else "transformed"
        return result


def run(workers: int, scratch: Path) -> None:
    already_done = set()
    if scratch.exists():
        for line in scratch.read_text().splitlines():
            if line.strip():
                try:
                    already_done.add(json.loads(line)["package"])
                except (json.JSONDecodeError, KeyError):
                    logger.warning(f"Skipping malformed scratch line: {line[:80]!r}")
        logger.info(f"Resuming: {len(already_done)} package(s) already audited")

    packages = [p for p in enumerate_packages() if p not in already_done]
    total = len(packages) + len(already_done)
    logger.info(f"{len(packages)} package(s) remaining out of {total} total")

    # ProcessPoolExecutor, not threads: the rpm/specfile libraries used by
    # _get_spec_sources() share global C-level macro state that isn't
    # thread-safe -- concurrent parsing across threads was observed to
    # corrupt unrelated packages' parses (e.g. aardvark-dns.spec parses fine
    # in isolation but failed with "Unknown tag" errors when parsed
    # concurrently with other packages). Separate processes avoid this
    # entirely.
    with scratch.open("a") as out, ProcessPoolExecutor(max_workers=workers) as executor:
        future_to_pkg = {executor.submit(classify_package, p): p for p in packages}
        for i, future in enumerate(as_completed(future_to_pkg), 1):
            pkg = future_to_pkg[future]
            try:
                result = future.result()
            except Exception as exc:  # classify_package should catch its own errors, but be safe
                result = {"package": pkg, "category": "error", "error": str(exc)}
            out.write(json.dumps(result) + "\n")
            out.flush()
            logger.info(
                f"[{len(already_done) + i}/{total}] {pkg}: {result['category']}"
            )


def _annotate_no_upstream_url(result: dict) -> dict:
    """For `no_upstream_url` packages, distinguish "genuinely nothing to fetch"
    (empty/missing `sources` -- a macro-only package or metapackage) from
    "has a real, tracked, locally-generated artifact" (non-empty `sources` --
    the actual HUM-4622 candidate: something is producing that artifact today,
    it just isn't expressible as a plain fetchable URL, e.g. gcc/glibc/openssl/
    cyrus-sasl's own bare, versioned-but-URL-less Source0).
    """
    if result["category"] != "no_upstream_url":
        return result
    sources_path = RPMS_DIR / result["package"] / "sources"
    result["needs_pipeline"] = bool(_parse_sources_file_with_legacy_fallback(sources_path))
    return result


def aggregate(scratch: Path, output: Path) -> None:
    if not scratch.exists():
        logger.error(f"Scratch file {scratch} not found -- run 'run' first")
        sys.exit(1)
    results = [
        _annotate_no_upstream_url(json.loads(line))
        for line in scratch.read_text().splitlines()
        if line.strip()
    ]
    results.sort(key=lambda r: r["package"])

    summary: dict[str, int] = {}
    for r in results:
        summary[r["category"]] = summary.get(r["category"], 0) + 1
    summary["no_upstream_url_needs_pipeline"] = sum(
        1 for r in results if r["category"] == "no_upstream_url" and r["needs_pipeline"]
    )
    summary["no_upstream_url_no_work_needed"] = sum(
        1 for r in results if r["category"] == "no_upstream_url" and not r["needs_pipeline"]
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"summary": summary, "packages": results}, indent=2, sort_keys=True) + "\n"
    )
    logger.info(f"Wrote {len(results)} package result(s) to {output}")
    for category, count in sorted(summary.items(), key=lambda kv: -kv[1]):
        logger.info(f"  {category}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Classify packages, appending to the scratch file")
    run_parser.add_argument("--workers", type=int, default=10)
    run_parser.add_argument("--scratch", type=Path, default=DEFAULT_SCRATCH)

    agg_parser = subparsers.add_parser("aggregate", help="Aggregate the scratch file into the final report")
    agg_parser.add_argument("--scratch", type=Path, default=DEFAULT_SCRATCH)
    agg_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)

    args = parser.parse_args()

    try:
        if args.command == "run":
            run(args.workers, args.scratch)
        elif args.command == "aggregate":
            aggregate(args.scratch, args.output)
    except Exception as exc:
        logger.error(f"Fatal: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
