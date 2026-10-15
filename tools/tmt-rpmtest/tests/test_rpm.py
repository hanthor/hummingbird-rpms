from dataclasses import replace

import pytest

from tmt_rpmtest.rpm import Identity, identities, verify_installed

RPM = Identity("curl", "curl-8.23.0-1.x86_64", "a" * 64, "b" * 64)


@pytest.mark.parametrize(
    "installed",
    [
        [],
        [replace(RPM, payload_sha256="c" * 64)],
        [RPM, replace(RPM, name="extra", nevra="extra-1-1.noarch")],
    ],
)
def test_installed_inventory_rejects_missing_replaced_and_extra_rpms(installed):
    with pytest.raises(ValueError, match="differs"):
        verify_installed([RPM], installed)


def test_rpm_metadata_requires_digests_and_unique_identities():
    record = "\t".join([RPM.name, RPM.nevra, RPM.header_sha256, RPM.payload_sha256]) + "\n"
    assert identities(record) == [RPM]
    verify_installed([RPM], identities(record))
    with pytest.raises(ValueError, match="duplicate"):
        identities(record * 2)
    with pytest.raises(ValueError, match="digests"):
        identities(record.replace(RPM.payload_sha256, "(none)"))
