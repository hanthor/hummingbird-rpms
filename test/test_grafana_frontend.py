"""Release-specific Grafana frontend patch generation."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parent.parent / "ci/grafana_frontend.py"


def test_generated_patch_replays_manifest_lockfile_and_asset(tmp_path):
    package_dir = tmp_path / "grafana13.2"
    package_dir.mkdir()
    config = {
        "pre_patches": ["grafana-yarn-cache.patch"],
        "manifests": {
            "package.json": {"resolutions": {"fast-uri": "3.1.9"}},
        },
        "assets": {".yarn/patches/storybook.patch": "storybook.patch"},
    }
    (package_dir / "frontend-overrides.json").write_text(json.dumps(config))
    (package_dir / "storybook.patch").write_text("storybook fix\n")
    (package_dir / "grafana-yarn-cache.patch").write_text(
        "--- a/.yarnrc.yml\n"
        "+++ b/.yarnrc.yml\n"
        "@@ -1,3 +1,6 @@\n"
        " compressionLevel: mixed\n"
        "-enableGlobalCache: true\n"
        "+enableGlobalCache: false\n"
        "+cacheFolder: .yarn/cache\n"
        "+supportedArchitectures:\n"
        "+  cpu: [x64, arm64, s390x]\n"
        " nodeLinker: node-modules\n"
    )

    source = tmp_path / "source"
    (source / ".yarn/releases").mkdir(parents=True)
    (source / ".yarn/releases/yarn-test.cjs").write_text("")
    (source / "package.json").write_text('{"resolutions": {}}\n')
    (source / ".yarnrc.yml").write_text(
        "compressionLevel: mixed\nenableGlobalCache: true\nnodeLinker: node-modules\n"
    )
    (source / "yarn.lock").write_text("original lock\n")
    pristine = tmp_path / "pristine"
    shutil.copytree(source, pristine)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    node = bin_dir / "node"
    node.write_text(
        "#!/bin/sh\n"
        'test "$YARN_ENABLE_IMMUTABLE_INSTALLS" = false || exit 1\n'
        "printf 'updated lock\\n' > yarn.lock\n"
    )
    node.chmod(0o755)
    subprocess.run(
        [sys.executable, str(SCRIPT), str(package_dir), str(source), "13.2.5", "generate"],
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        check=True,
        capture_output=True,
    )

    generated = source / "grafana-frontend-13.2.5.patch"
    assert generated.is_file()
    shutil.copyfile(generated, package_dir / generated.name)
    subprocess.run(
        [sys.executable, str(SCRIPT), str(package_dir), str(pristine), "13.2.5", "apply"],
        check=True, capture_output=True,
    )
    for name in ("package.json", ".yarnrc.yml", "yarn.lock", ".yarn/patches/storybook.patch"):
        assert (pristine / name).read_bytes() == (source / name).read_bytes()


def test_generated_patch_includes_requested_native_architectures(tmp_path):
    package_dir = tmp_path / "grafana12.4"
    package_dir.mkdir()
    (package_dir / "frontend-overrides.json").write_text(json.dumps({
        "manifests": {}, "assets": {},
        "supported_architectures": ["x64", "arm64"],
    }))
    source = tmp_path / "source"
    (source / ".yarn/releases").mkdir(parents=True)
    (source / ".yarn/releases/yarn-test.cjs").write_text("")
    (source / ".yarnrc.yml").write_text("compressionLevel: mixed\n")
    (source / "yarn.lock").write_text("original lock\n")
    pristine = tmp_path / "pristine"
    shutil.copytree(source, pristine)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    node = bin_dir / "node"
    node.write_text("#!/bin/sh\nexit 0\n")
    node.chmod(0o755)

    subprocess.run(
        [sys.executable, str(SCRIPT), str(package_dir), str(source), "12.4.14", "generate"],
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        check=True, capture_output=True,
    )
    generated = source / "grafana-frontend-12.4.14.patch"
    subprocess.run(
        ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(generated)],
        cwd=pristine, check=True, capture_output=True,
    )
    yarnrc = (pristine / ".yarnrc.yml").read_text()
    assert "supportedArchitectures:" in yarnrc
    assert "    - arm64\n" in yarnrc


def test_activate_spec_switches_patch_stacks_after_version_update(tmp_path):
    package_dir = tmp_path / "grafana13.2"
    package_dir.mkdir()
    (package_dir / "frontend-overrides.json").write_text(json.dumps({
        "legacy_patches": [1, 4],
        "frontend_patch_number": 19,
    }))
    spec = package_dir / "grafana13.2.spec"
    spec.write_text(
        "Version: 13.2.5\n"
        "Patch0: base.patch\n"
        "Patch1: old-compression.patch\n"
        "Patch2: build-fix.patch\n"
        "Patch4: old-security.patch\n"
    )
    command = [sys.executable, str(SCRIPT), str(package_dir), ".", "13.2.5",
               "activate-spec"]
    subprocess.run(command, check=True, capture_output=True)
    subprocess.run(command, check=True, capture_output=True)

    updated = spec.read_text()
    assert "#Patch1: old-compression.patch" in updated
    assert "#Patch4: old-security.patch" in updated
    assert "Patch2: build-fix.patch" in updated
    assert updated.count("Patch19:        grafana-frontend-%{version}.patch") == 1
