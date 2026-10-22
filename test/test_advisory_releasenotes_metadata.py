"""Rendering checks for RPM names in advisory release notes."""

from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, StrictUndefined


TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "konflux-templates/macros/releng/advisory-releasenotes-metadata.yaml"
)
VERSION = "9.20.27-0.1.hum1"


def artifact(architecture, qualifiers="", cves=None):
    """Make an advisory artifact with a realistic RPM PURL."""
    purl = f"pkg:rpm/redhat/bind@{VERSION}"
    if qualifiers:
        purl += f"?{qualifiers}"
    return {
        "architecture": architecture,
        "component": "bind",
        "purl": purl,
        "cves": {"fixed": cves or {}},
    }


def render(artifacts, issues=None):
    """Render the checked-in template with the advisory fields it reads."""
    advisory = {
        "spec": {
            "type": "RHSA",
            "product_name": "Hummingbird",
            "content": {"artifacts": artifacts},
            "issues": {"fixed": issues or []},
        }
    }
    template = Environment(undefined=StrictUndefined).from_string(TEMPLATE.read_text())
    return yaml.safe_load(template.render(advisory=advisory))


@pytest.mark.parametrize(
    "qualifiers",
    ["epoch=32&arch=x86_64", "arch=x86_64&epoch=32"],
)
def test_nonzero_epoch_in_binary_and_source_rpms(qualifiers):
    rendered = render(
        [
            artifact("x86_64", qualifiers, cves={"CVE-2026-1234": {}}),
            artifact("src", qualifiers.replace("x86_64", "src")),
        ],
        issues=[{"id": "HUM-9402", "summary": "Show epochs"}],
    )

    assert "* bind-32:9.20.27-0.1.hum1 (x86_64)" in rendered["description"]
    assert "* bind-32:9.20.27-0.1.hum1.src (src)" in rendered["description"]
    assert "CVE-2026-1234" in rendered["description"]
    assert "HUM-9402 (Show epochs)" in rendered["description"]
    assert rendered["synopsis"].strip() == "Hummingbird RPMs Security Update"
    assert rendered["topic"].strip() == "An update for Hummingbird RPMs is now available."


@pytest.mark.parametrize("qualifiers", ["", "arch=x86_64", "arch=x86_64&epoch=0"])
def test_zero_or_missing_epoch_keeps_legacy_display(qualifiers):
    source_qualifiers = qualifiers.replace("x86_64", "src")
    description = render(
        [artifact("x86_64", qualifiers), artifact("src", source_qualifiers)]
    )["description"]

    assert description == (
        "This update includes the following RPMs:\n\n"
        "bind:\n"
        "  * bind-9.20.27-0.1.hum1 (x86_64)\n"
        "  * bind-9.20.27-0.1.hum1.src (src)\n"
    )


def test_same_epoch_groups_binary_architectures():
    description = render(
        [
            artifact("x86_64", "arch=x86_64&epoch=32"),
            artifact("aarch64", "epoch=32&arch=aarch64"),
            artifact("src", "epoch=32&arch=src"),
        ]
    )["description"]

    assert description.count("* bind-32:9.20.27-0.1.hum1 (aarch64, x86_64)") == 1
    assert description.count("* bind-32:9.20.27-0.1.hum1.src (src)") == 1


def test_different_epochs_do_not_share_one_binary_row():
    description = render(
        [
            artifact("x86_64", "arch=x86_64&epoch=32"),
            artifact("aarch64", "arch=aarch64&epoch=31"),
            artifact("src", "arch=src&epoch=32"),
        ]
    )["description"]

    assert "* bind-32:9.20.27-0.1.hum1 (x86_64)" in description
    assert "* bind-31:9.20.27-0.1.hum1 (aarch64)" in description
    assert "* bind-32:9.20.27-0.1.hum1.src (src)" in description
    assert "* bind-32:9.20.27-0.1.hum1 (aarch64, x86_64)" not in description
