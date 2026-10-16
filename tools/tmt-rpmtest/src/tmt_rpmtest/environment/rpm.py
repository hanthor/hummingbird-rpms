"""Resolve RPM files, assemble an empty root, and check installed identities."""

import glob
import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from tmt_rpmtest.drivers.podman import Podman

DEFAULT_BUILDER = (
    "quay.io/hummingbird-ci/hummingbird-builder@sha256:"
    "3defcdbfc19d33ec3686cfb97be7372fb97d2c8ea3935697b54f7d80b11fb59c"
)
QUERY = "%{NAME}\\t%{NEVRA}\\t%{SHA256HEADER}\\t%{PAYLOADSHA256}\\n"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class Identity:
    name: str
    nevra: str
    header_sha256: str
    payload_sha256: str


def identities(output: str) -> list[Identity]:
    result = []
    for line in output.splitlines():
        values = line.split("\t")
        if len(values) != 4 or not all(SHA256.fullmatch(value) for value in values[2:]):
            raise ValueError("RPM metadata must contain valid header and payload SHA-256 digests.")
        result.append(Identity(*values))
    if len({item.nevra for item in result}) != len(result):
        raise ValueError("The RPM inventory contains duplicate identities.")
    return result


def verify_installed(expected: list[Identity], installed: list[Identity]) -> None:
    if not expected or set(expected) != set(installed):
        raise ValueError("The installed RPM inventory differs from the selected artifacts.")


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


RESOLVE = r"""
set -eu
mkdir -p /work/packages /work/cache /work/resolve-root
dnf5 --installroot=/work/resolve-root --use-host-config --releasever=rpmtest \
  --setopt=reposdir=/work/repos --setopt=cachedir=/work/cache \
  --setopt=keepcache=True --setopt=install_weak_deps=False \
  --no-gpgchecks -y install --downloadonly "$@" >&2
find /work/resolve-root /work/cache -name '*.rpm' -exec cp '{}' /work/packages/ \;
"""

ASSEMBLE = r"""
set -eu
mkdir /newroot
createrepo_c /work/packages >&2
dnf-installroot /newroot -y --setopt=reposdir=/dev/null \
  --repofrompath=resolved,file:///work/packages --repo=resolved \
  --setopt=install_weak_deps=False --no-gpgchecks --nodocs install /work/packages/*.rpm >&2
rpm --root /newroot -qa --qf "$1" > /work/installed.tsv
python3 -m venv --without-pip /tmp/rpmtest-archive
/tmp/rpmtest-archive/bin/python3 - <<'PY'
import os
import tarfile

def metadata(info):
    info.uname = info.gname = ""
    source = os.path.join("/newroot", info.name)
    for key in os.listxattr(source, follow_symlinks=False):
        if key != "security.selinux":
            value = os.getxattr(source, key, follow_symlinks=False)
            info.pax_headers["SCHILY.xattr." + key] = value.decode("utf-8", "surrogateescape")
    return info

with tarfile.open("/work/rootfs.tar", "w", format=tarfile.PAX_FORMAT) as archive:
    archive.add("/newroot", arcname=".", filter=metadata)
PY
"""


class RPMBuilder:
    def __init__(self, runtime: Podman, image: str = DEFAULT_BUILDER):
        if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image):
            raise ValueError("The builder image must use a sha256 digest.")
        self.runtime = runtime
        self.image = image

    def prepare(
        self,
        packages: list[str],
        repositories: list[str],
        artifacts: list[str],
        source: Path,
        work: Path,
    ) -> Path:
        if not packages:
            raise ValueError("Specify the packages required by this environment.")
        if any(not re.fullmatch(r"[A-Za-z0-9_+][A-Za-z0-9_.+:~-]*", p) for p in packages):
            raise ValueError("Package names must not contain options, globs, or paths.")
        for url in repositories:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.netloc or any(c in url for c in "\r\n"):
                raise ValueError("Repositories must use HTTPS URLs.")
        work.mkdir(parents=True, exist_ok=False)
        candidates_dir = work / "candidates"
        candidates_dir.mkdir()
        (work / "repos").mkdir()
        for pattern in artifacts:
            matches = sorted(glob.glob(str(source / pattern)))
            if not matches:
                raise ValueError(f"No RPM artifacts match {pattern!r}.")
            for artifact in map(Path, matches):
                target = candidates_dir / f"{digest(artifact)}.rpm"
                shutil.copyfile(artifact, target)
                if target.stem != digest(target):
                    raise ValueError("An RPM artifact changed while it was copied.")
        self.runtime.pull_image(self.image)
        candidate_paths = sorted(candidates_dir.glob("*.rpm"))
        candidates = {}
        if candidate_paths:
            metadata = self.runtime.build(
                self.image,
                work,
                "set -eu; rpm -K --nosignature /work/candidates/*.rpm >&2; "
                'rpm -qp --qf "$1" /work/candidates/*.rpm',
                QUERY,
            ).stdout
            for path, identity in zip(candidate_paths, identities(metadata), strict=True):
                candidates[identity.nevra] = {**asdict(identity), "sha256": digest(path)}
            names = [item["name"] for item in candidates.values()]
            if len(set(names)) != len(names):
                raise ValueError("Supply only one candidate version and architecture per package.")
            self.runtime.build(self.image, work, "createrepo_c /work/candidates >&2")
            (work / "repos/candidate.repo").write_text(
                "[candidate]\nname=candidate\nbaseurl=file:///work/candidates\n"
                "enabled=1\ngpgcheck=0\npriority=1\n"
            )
        excluded = ",".join(item["name"] for item in candidates.values())
        for index, url in enumerate(repositories):
            (work / "repos" / f"source-{index}.repo").write_text(
                f"[source-{index}]\nname=source-{index}\nbaseurl={url}\nenabled=1\ngpgcheck=0\n"
                f"excludepkgs={excluded}\n"
            )
        self.runtime.build(self.image, work, RESOLVE, *packages, internet=True)
        files = sorted((work / "packages").glob("*.rpm"))
        if not files:
            raise ValueError("The RPM transaction selected no artifacts.")
        selected = identities(
            self.runtime.build(
                self.image, work, 'rpm -qp --qf "$1" /work/packages/*.rpm', QUERY
            ).stdout
        )
        hashes = {path.name: digest(path) for path in files}
        for path, identity in zip(files, selected, strict=True):
            candidate = candidates.get(identity.nevra)
            if candidate and candidate["sha256"] != hashes[path.name]:
                raise ValueError(f"The transaction substituted candidate {identity.nevra}.")
        self.runtime.build(self.image, work, ASSEMBLE, QUERY)
        verify_installed(selected, identities((work / "installed.tsv").read_text()))
        if hashes != {path.name: digest(path) for path in files}:
            raise ValueError("An RPM artifact changed during assembly.")
        selected_nevras = {item.nevra for item in selected}
        manifest = {
            "builder": self.image,
            "packages": packages,
            "repositories": repositories,
            "candidates": [
                {**item, "selected": nevra in selected_nevras} for nevra, item in candidates.items()
            ],
            "artifacts": hashes,
            "installed": [asdict(item) for item in sorted(selected, key=lambda item: item.nevra)],
            "rootfs_sha256": digest(work / "rootfs.tar"),
        }
        path = work / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        return path
