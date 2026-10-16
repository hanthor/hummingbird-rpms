"""Inspect package contracts through the public primitive interface."""

import io
import shutil
import struct
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from tmt_rpmtest import PrimitiveContext
from tmt_rpmtest.primitives import Packages


def elf(*, loader=None, needed=False, wide=True, endian="<", machine=62):
    header = bytearray(64)
    header[:6] = b"\x7fELF" + bytes([2 if wide else 1, 1 if endian == "<" else 2])
    struct.pack_into(endian + "H", header, 18, machine)
    struct.pack_into(endian + ("Q" if wide else "I"), header, 32 if wide else 28, 64)
    size = 56 if wide else 32
    count = bool(loader) + bool(needed)
    struct.pack_into(endian + "HH", header, 54 if wide else 42, size, count)
    programs = bytearray(size * count)
    payload = bytearray()
    for index, (kind, data) in enumerate(
        [
            *([(3, loader.encode() + b"\0")] if loader else []),
            *(
                [(2, struct.pack(endian + ("QQQQ" if wide else "IIII"), 1, 1, 0, 0))]
                if needed
                else []
            ),
        ]
    ):
        struct.pack_into(endian + "I", programs, index * size, kind)
        struct.pack_into(
            endian + ("Q" if wide else "I"),
            programs,
            index * size + (8 if wide else 4),
            64 + len(programs) + len(payload),
        )
        struct.pack_into(
            endian + ("Q" if wide else "I"),
            programs,
            index * size + (32 if wide else 16),
            len(data),
        )
        payload.extend(data)
    return bytes(header + programs + payload)


@pytest.fixture
def package_root(tmp_path):
    original = tmp_path / "root.tar"
    loader = "/lib64/ld-linux-x86-64.so.2"
    with tarfile.open(original, "w") as archive:
        item = tarfile.TarInfo(".")
        item.type, item.mode = tarfile.DIRTYPE, 0o750
        archive.addfile(item)
        for path in ("usr", "usr/bin", "usr/lib64", "usr/lib64/nested"):
            item = tarfile.TarInfo(path)
            item.type = tarfile.DIRTYPE
            archive.addfile(item)
        for path, data in {
            "usr/bin/app": elf(loader=loader),
            "usr/bin/static": elf(),
            "usr/lib64/libapp.so": elf(needed=True),
            "usr/lib64/ld-linux-x86-64.so.2": elf(),
            "usr/lib64/nested/data": b"payload",
        }.items():
            item = tarfile.TarInfo(path)
            item.size = len(data)
            item.mode = 0o755
            archive.addfile(item, io.BytesIO(data))
        for path, target in {
            "lib64": "usr/lib64",
            "usr/bin/relative": "../lib64/libapp.so",
            "usr/bin/absolute": "/lib64/libapp.so",
            "usr/bin/escape": "../../../../lib64/libapp.so",
            "usr/bin/broken": "/host-only",
            "usr/bin/loop": "loop",
            "usr/bin/self-dir": ".",
            "usr/bin/parent": "..",
        }.items():
            item = tarfile.TarInfo(path)
            item.type = tarfile.SYMTYPE
            item.linkname = target
            archive.addfile(item)
        item = tarfile.TarInfo("usr/bin/hard")
        item.type = tarfile.LNKTYPE
        item.linkname = "usr/bin/static"
        archive.addfile(item)
    with tarfile.open(original) as archive:
        owned = [
            "/" + item.name
            for item in archive
            if item.name.startswith("usr/bin/") or item.name.startswith("usr/lib64/")
        ]
    owned.append("/lib64/nested/data")
    owned.append("/")
    owned.append("/usr/bin/missing")

    def build(image, work, script, *args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "\n".join(owned) + "\n", "")

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "libapp.so => /lib64/libapp.so", "")

    session = SimpleNamespace(
        snapshot=lambda path: shutil.copyfile(original, path),
        runtime=SimpleNamespace(build=build),
        run=run,
    )
    with PrimitiveContext(session, tmp_path, "unused") as context:
        yield Packages(context), calls, original


def test_contents_recursive_globs_missing_files_modes_and_parent_symlinks(package_root):
    packages, _, _ = package_root
    contents = {item.path: item.info for item in packages.contents("app", match=("/usr/bin/*",))}
    assert contents["/usr/bin/missing"] is None
    assert contents["/usr/bin/app"].isfile()
    assert contents["/usr/bin/app"].mode == 0o755
    assert contents["/usr/bin/relative"].issym()
    assert contents["/usr/bin/hard"].isfile()
    assert packages.contents("app", match=("/lib64/**",))[0].info.size == len(b"payload")
    assert packages.contents("app", match=("/",))[0].info.mode == 0o750
    assert not packages.contents("app", match=("/usr/lib64/data",))
    assert [item.path for item in packages.contents("app", match=("/usr/lib64/**/d?ta",))] == [
        "/usr/lib64/nested/data"
    ]
    with pytest.raises(ValueError, match="absolute"):
        packages.contents("app", match=("relative/*",))


def test_links_resolve_inside_root_and_report_dangling_and_cycles(package_root):
    packages, _, _ = package_root
    links = {item.path: item for item in packages.links("app")}
    for name in ("relative", "absolute", "escape"):
        assert links[f"/usr/bin/{name}"].resolved == "/usr/lib64/libapp.so"
        assert links[f"/usr/bin/{name}"].error is None
    assert links["/usr/bin/self-dir"].resolved == "/usr/bin"
    assert links["/usr/bin/parent"].resolved == "/usr"
    assert links["/usr/bin/broken"].resolved is None
    assert links["/usr/bin/broken"].error
    assert "Too many links" in links["/usr/bin/loop"].error


def test_runtime_links_checks_dynamic_executables_and_libraries(package_root):
    packages, calls, _ = package_root
    results = packages.runtime_links("app")
    assert {item.path for item in results} == {"/usr/bin/app", "/usr/lib64/libapp.so"}
    assert all(item.resolved for item in results)
    assert len(calls) == 2
    assert all(argv[:2] == ["/lib64/ld-linux-x86-64.so.2", "--list"] for argv in calls)


@pytest.mark.parametrize(
    "returncode,output", [(1, "missing library"), (0, "libapp.so => not found")]
)
def test_runtime_links_reports_missing_dependencies(package_root, returncode, output):
    packages, _, _ = package_root
    packages._context._session.run = lambda argv, **kwargs: subprocess.CompletedProcess(
        argv, returncode, output, ""
    )
    assert all(not item.resolved for item in packages.runtime_links("app"))


def test_runtime_links_reports_missing_loader_without_attempting_execution(package_root):
    packages, calls, original = package_root
    with tarfile.open(original, "a") as archive:
        item = tarfile.TarInfo("usr/bin/app")
        data = elf(loader="/missing-loader")
        item.size = len(data)
        archive.addfile(item, io.BytesIO(data))
    app = next(item for item in packages.runtime_links("app") if item.path == "/usr/bin/app")
    assert not app.resolved and app.result.returncode == 127
    assert "Missing dynamic loader" in app.result.stderr
    assert not any(argv[0] == "/missing-loader" for argv in calls)


@pytest.mark.parametrize("method", ["contents", "links", "runtime_links"])
def test_missing_packages_raise_query_errors(package_root, method):
    packages, _, _ = package_root
    packages._context._session.runtime.build = lambda *args, **kwargs: subprocess.CompletedProcess(
        args, 1, "", "package is not installed"
    )
    with pytest.raises(subprocess.CalledProcessError):
        getattr(packages, method)("missing")


@pytest.mark.parametrize(
    "wide,endian,machine", [(True, "<", 62), (True, ">", 183), (False, "<", 62), (False, ">", 183)]
)
def test_elf_formats(wide, endian, machine):
    from tmt_rpmtest.primitives.elf import LOADERS, interpreter

    assert (
        interpreter(io.BytesIO(elf(needed=True, wide=wide, endian=endian, machine=machine)))
        == (LOADERS[machine])
    )


def test_elf_rejects_truncated_headers_and_unknown_dynamic_architecture():
    from tmt_rpmtest.primitives.elf import interpreter

    for data in (
        b"\x7fELF",
        elf(loader="/loader")[:70],
        elf(needed=True)[:-1],
        elf(needed=True, machine=999),
    ):
        with pytest.raises(ValueError):
            interpreter(io.BytesIO(data))
    assert interpreter(io.BytesIO(b"ordinary text")) is None
