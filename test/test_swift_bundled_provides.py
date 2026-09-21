"""Tests for Swift's source and bundled Provides updater."""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
UPDATER_PATH = ROOT_DIR / "rpms" / "swift-lang" / "update_swift_version.py"


def _load_updater() -> Any:
    spec = importlib.util.spec_from_file_location("swift_version_updater", UPDATER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


updater = _load_updater()

SYNCED_PROJECTS = {
    "cmark",
    "indexstore-db",
    "llbuild",
    "llvm-project",
    "sourcekit-lsp",
    "swift",
    "swift-build",
    "swift-docc",
    "swift-docc-symbolkit",
    "swift-driver",
    "swift-foundation",
    "swift-foundation-icu",
    "swift-llvm-bindings",
    "swift-lmdb",
    "swift-markdown",
    "swift-syntax",
    "swift-tools-protocols",
    "swift-tools-support-core",
}
PINNED_PROJECTS = {
    "boringssl": "1111111111111111111111111111111111111111",
    "brotli": "v1.2.0",
    "cmake": "v3.30.2",
    "curl": "curl-8_9_1",
    "ninja": "v1.13.1",
    "swift-argument-parser": "1.6.1",
    "swift-asn1": "1.3.2",
    "swift-atomics": "1.2.0",
    "swift-certificates": "1.10.1",
    "swift-collections": "1.1.6",
    "swift-crypto": "3.12.5",
    "swift-nio": "2.92.2",
    "swift-system": "1.5.0",
    "swift-toolchain-sqlite": "1.0.9",
}


def _checkout_config(*, include_exact_scheme: bool = True) -> dict[str, object]:
    projects = SYNCED_PROJECTS | PINNED_PROJECTS.keys()
    repos = {
        project: {"remote": {"id": f"example/{project}"}}
        for project in projects
    }
    repos["swift-crypto"] = {"remote": {"id": "apple/swift-crypto"}}
    fallback_repos = {
        **{project: "release/6.4" for project in SYNCED_PROJECTS},
        **PINNED_PROJECTS,
    }
    schemes: dict[str, object] = {"release/6.4": {"repos": fallback_repos}}
    if include_exact_scheme:
        exact_repos = {
            **{project: "release/6.4.0" for project in SYNCED_PROJECTS},
            **PINNED_PROJECTS,
        }
        schemes["release/6.4.0"] = {"repos": exact_repos}
    return {"repos": repos, "branch-schemes": schemes}


def _spec_text() -> str:
    return """Version: 6.3.0

# Begin forge sources
old source
# End forge sources

Name: swift-lang
Source101:      bundled-provides.inc
Provides:       swiftlang = %{version}-%{release}
%global swift_bundled_provides_main 1
%include %{SOURCE101}
%undefine swift_bundled_provides_main

%description
Swift

%package runtime
Summary: Swift runtime libraries
%global swift_bundled_provides_runtime 1
%include %{SOURCE101}
%undefine swift_bundled_provides_runtime

%description runtime
Swift runtime
"""


def _responses(*, include_exact_scheme: bool = True, malformed_llvm: bool = False) -> dict[str, str]:
    llvm = "not an LLVM version\n" if malformed_llvm else """\
set(LLVM_VERSION_MAJOR 21)
set(LLVM_VERSION_MINOR 1)
set(LLVM_VERSION_PATCH 6)
"""
    return {
        "utils/update_checkout/update-checkout-config.json": json.dumps(
            _checkout_config(include_exact_scheme=include_exact_scheme)
        ),
        "cmake/Modules/LLVMVersion.cmake": llvm,
        "src/include/cmark-gfm_version.h": (
            '#define CMARK_GFM_VERSION_STRING "0.29.0.gfm.13"\n'
        ),
        "icuSources/include/_foundation_unicode/uvernum.h": (
            '#define U_ICU_VERSION "76.1"\n'
        ),
        "libraries/liblmdb/lmdb.h": """\
#define MDB_VERSION_MAJOR 0
#define MDB_VERSION_MINOR 9
#define MDB_VERSION_PATCH 70
""",
        "Sources/CCryptoBoringSSL/hash.txt": (
            "This directory is derived from BoringSSL cloned from "
            "https://boringssl.googlesource.com/boringssl at revision "
            "aefa5d24da34ef77ac797bdbe684734e5bd870f4\n"
        ),
    }


class FakeFetcher:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def __call__(self, url: str) -> str:
        self.urls.append(url)
        for suffix, response in self.responses.items():
            if url.endswith(suffix):
                return response
        raise updater.UpdateError(f"unexpected URL: {url}")


def _prepare_update(
    tmp_path: Path,
    *,
    include_exact_scheme: bool = True,
    malformed_llvm: bool = False,
) -> tuple[Path, Path, FakeFetcher]:
    spec_path = tmp_path / "swift-lang.spec"
    spec_path.write_text(_spec_text())
    provides_path = tmp_path / "bundled-provides.inc"
    fetcher = FakeFetcher(
        _responses(
            include_exact_scheme=include_exact_scheme,
            malformed_llvm=malformed_llvm,
        )
    )
    return spec_path, provides_path, fetcher


def _block(text: str, begin: str, end: str) -> str:
    return text.split(begin, 1)[1].split(end, 1)[0]


def test_update_generates_sources_and_owned_bundled_provides(tmp_path: Path) -> None:
    spec_path, provides_path, fetcher = _prepare_update(tmp_path)

    updater.update_swift_version(
        "6.4.0", spec_path, provides_path=provides_path, fetch=fetcher
    )

    spec_text = spec_path.read_text()
    provides_text = provides_path.read_text()
    main = _block(
        provides_text,
        "%if 0%{?swift_bundled_provides_main}",
        "%endif",
    )
    runtime = _block(
        provides_text,
        "%if 0%{?swift_bundled_provides_runtime}",
        "%endif",
    )
    assert "Version: 6.4.0" in spec_text
    assert "Generated by update_swift_version.py. Do not edit." in provides_text
    assert "Provides:       bundled(llvm) = 21.1.6" in main
    assert "Provides:       bundled(clang) = 21.1.6" in main
    assert "Provides:       bundled(cmark-gfm) = 0.29.0.gfm.13" in main
    assert "Provides:       bundled(lmdb) = 0.9.70" in main
    assert (
        "Provides:       bundled(boringssl) = "
        "aefa5d24da34ef77ac797bdbe684734e5bd870f4"
    ) in main
    assert "Provides:       bundled(swift-driver) = %{version}" in main
    assert "Provides:       bundled(swift-atomics) = 1.2.0" in main
    assert "Provides:       bundled(swift-nio) = 2.92.2" in main
    assert "bundled(swift-toolchain-sqlite)" not in provides_text
    assert "Provides:       bundled(icu) = 76.1" in runtime
    assert "Provides:       bundled(swift-foundation) = %{version}" in runtime
    assert "bundled(icu)" not in main
    assert "bundled(curl)" not in provides_text
    assert "bundled(cmake)" not in provides_text
    assert "bundled(ninja)" not in provides_text
    assert "bundled(swift-llvm-bindings)" not in provides_text
    assert "%global source_cmark 1" in spec_text
    assert "%global subdir1    boringssl" not in spec_text
    assert "    cmake\n" not in spec_text
    assert "    ninja\n" not in spec_text
    assert "%global source_swift_toolchain_sqlite" in spec_text
    assert "    swift-toolchain-sqlite\n" in spec_text
    assert "%global forge_source_last" in spec_text
    assert "swift-%{version0}-RELEASE" in spec_text


def test_update_is_idempotent(tmp_path: Path) -> None:
    spec_path, provides_path, fetcher = _prepare_update(tmp_path)
    updater.update_swift_version(
        "6.4.0", spec_path, provides_path=provides_path, fetch=fetcher
    )
    first_spec = spec_path.read_bytes()
    first_provides = provides_path.read_bytes()

    updater.update_swift_version(
        "6.4.0", spec_path, provides_path=provides_path, fetch=fetcher
    )

    assert spec_path.read_bytes() == first_spec
    assert provides_path.read_bytes() == first_provides


def test_update_uses_major_minor_scheme_as_fallback(tmp_path: Path) -> None:
    spec_path, provides_path, fetcher = _prepare_update(
        tmp_path, include_exact_scheme=False
    )

    updater.update_swift_version(
        "6.4.0", spec_path, provides_path=provides_path, fetch=fetcher
    )

    assert "Provides:       bundled(swift-system) = 1.5.0" in provides_path.read_text()
    assert any("swift-6.4.0-RELEASE" in url for url in fetcher.urls)


def test_malformed_nested_version_leaves_outputs_unchanged(tmp_path: Path) -> None:
    spec_path, provides_path, fetcher = _prepare_update(
        tmp_path, malformed_llvm=True
    )
    original_spec = spec_path.read_bytes()
    provides_path.write_text("existing include\n")
    original_provides = provides_path.read_bytes()

    try:
        updater.update_swift_version(
            "6.4.0", spec_path, provides_path=provides_path, fetch=fetcher
        )
    except updater.UpdateError as error:
        assert "LLVM_VERSION_MAJOR" in str(error)
    else:
        raise AssertionError("malformed LLVM version did not fail")

    assert spec_path.read_bytes() == original_spec
    assert provides_path.read_bytes() == original_provides


def test_missing_forge_marker_leaves_outputs_unchanged(tmp_path: Path) -> None:
    spec_path, provides_path, fetcher = _prepare_update(tmp_path)
    spec_path.write_text(spec_path.read_text().replace("# End forge sources\n", ""))
    original_spec = spec_path.read_bytes()
    provides_path.write_text("existing include\n")
    original_provides = provides_path.read_bytes()

    try:
        updater.update_swift_version(
            "6.4.0", spec_path, provides_path=provides_path, fetch=fetcher
        )
    except updater.UpdateError as error:
        assert "expected one" in str(error)
    else:
        raise AssertionError("missing forge marker did not fail")

    assert spec_path.read_bytes() == original_spec
    assert provides_path.read_bytes() == original_provides
