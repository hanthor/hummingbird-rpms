"""Shared pytest fixtures/helpers for hum.meta's test suite."""

from hum.meta.model import FedoraOriginWithRelease


def clean_fedora(**overrides: object) -> FedoraOriginWithRelease:
    defaults: dict[str, object] = {
        "git_url": "https://src.fedoraproject.org/rpms/bash.git",
        "branch": "rawhide",
        "sha": "2768211b5135c7169f513965d79e9f89e0ca6124",
        "release": "3",
    }
    defaults.update(overrides)
    return FedoraOriginWithRelease(**defaults)  # type: ignore[arg-type]
