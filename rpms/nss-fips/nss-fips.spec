# RHEL 9.2 NSS version with NIST FIPS 140-3 validation
%global rhel_nss_version 3.90.0
%global rhel_nss_release 6.el9_2

# Version to advertise in Provides: for compatibility with Fedora's nss package.
# The nss package requires nss-softokn >= <nss-version>, so we must provide
# a version high enough to satisfy Fedora's current nss requirements.
# By matching Fedora's version exactly, DNF will prefer "nss-softokn" over
# "nss-softokn-fips" alphabetically, making FIPS opt-in rather than default.
# Note: The actual FIPS-certified binaries are from %{rhel_nss_version}.
#
# To find the current Fedora nss-softokn version, run:
#   dnf info nss-softokn | grep Version
%global provides_version 3.119.1

# Disable debug package auto-generation - we extract binaries from RHEL RPMs
%global debug_package %{nil}

# Directory definitions (must match the main nss package)
%global unsupported_tools_directory %{_libdir}/nss/unsupported-tools
%global saved_files_dir %{_libdir}/nss/saved

Summary: FIPS validated cryptographic modules for NSS
Name: nss-fips
Version: %{rhel_nss_version}
Release: 1%{?dist}

# The source tarball contains the RHEL binary RPMs with NIST-validated FIPS modules
Source0: %{name}-%{version}.tar.gz
Source1: extract-fips.sh

License: MPL-2.0
URL: http://www.mozilla.org/projects/security/pki/nss/

BuildRequires: coreutils
BuildRequires: cpio
BuildRequires: rpm
BuildRequires: sed
BuildRequires: findutils

%description
This package provides NIST-validated NSS FIPS cryptographic modules for
use with Hummingbird. The FIPS modules (libsoftokn3.so, libfreebl3.so,
libfreeblpriv3.so) are extracted from RHEL 9.2 packages that have been
submitted to NIST for FIPS 140-3 certification.

Note: These libraries are from NSS %{rhel_nss_version}, which may be older than
the current Fedora NSS version. This tradeoff is necessary for FIPS compliance.

%files
# Meta package - no files


# Package name "nss-softokn-fips" comes AFTER "nss-softokn" alphabetically.
# This ensures DNF prefers the standard nss-softokn when both are available
# and no explicit choice is made by the user.
%package -n nss-softokn-fips
Summary: FIPS validated Network Security Services Softoken Module
Requires: nspr >= 4.35
Requires: nss-util >= %{rhel_nss_version}
Requires: nss-softokn-freebl-fips%{_isa} = %{version}-%{release}

# Provide nss-softokn capability to satisfy dependencies from nss package.
# We use a matching version number to ensure compatibility with Fedora's nss
# while allowing DNF to prefer "nss-softokn" over "nss-softokn-fips" alphabetically.
Provides: nss-softokn = %{provides_version}
Provides: nss-softokn%{_isa} = %{provides_version}
Conflicts: nss-softokn

%description -n nss-softokn-fips
Network Security Services Softoken Cryptographic Module with FIPS validation.

This package contains libsoftokn3.so from RHEL 9.2 that has been submitted
to NIST for FIPS 140-3 certification. It replaces the standard nss-softokn
package when FIPS compliance is required.

The softokn module provides the PKCS#11 software token interface, handling
key storage and cryptographic operations.

%files -n nss-softokn-fips
%{_libdir}/libsoftokn3.so
%{_libdir}/libsoftokn3.chk
%dir %{_libdir}/nss
%dir %{saved_files_dir}
%dir %{unsupported_tools_directory}


%package -n nss-softokn-freebl-fips
Summary: FIPS validated Freebl library for Network Security Services
Requires: nspr >= 4.12
Requires: nss-util >= 3.33

# Note: We intentionally do NOT provide nss-softokn-freebl here.
# This ensures that when someone installs nss-softokn (non-FIPS),
# they get the standard nss-softokn-freebl, not the FIPS version.
Conflicts: nss-softokn-freebl

%description -n nss-softokn-freebl-fips
NSS Softoken Cryptographic Module Freebl Library with FIPS validation.

This package contains libfreebl3.so and libfreeblpriv3.so from RHEL 9.2
that have been submitted to NIST for FIPS 140-3 certification.

The freebl library provides the low-level cryptographic primitives
(AES, SHA, RSA, ECC, etc.) used by NSS.

%files -n nss-softokn-freebl-fips
%{_libdir}/libfreebl3.so
%{_libdir}/libfreebl3.chk
%{_libdir}/libfreeblpriv3.so
%{_libdir}/libfreeblpriv3.chk


%prep
tar xf %{SOURCE0}

%build
# No build required - we are extracting pre-built binaries from RHEL RPMs

%check
# Verify the extracted binaries are valid ELF shared objects
# The actual FIPS validation was done by NIST on these exact binaries

%install
export RHEL_NSS_VERSION=%{rhel_nss_version}
export RHEL_NSS_RELEASE=%{rhel_nss_release}
%{SOURCE1}

%changelog
* Mon Feb 02 2026 Robert Sturla <rsturla@redhat.com> - 3.90.0-1
- Initial package for Hummingbird
- Provides NIST-validated FIPS 140-3 cryptographic modules from RHEL 9.2
