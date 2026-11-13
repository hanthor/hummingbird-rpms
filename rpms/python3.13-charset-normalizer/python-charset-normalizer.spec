Name:           python3.13-charset-normalizer
Version:        3.5.2
Release:        1.1%{?dist}
Summary:        The Real First Universal Charset Detector
# SPDX
License:        MIT
URL:            https://github.com/jawah/charset_normalizer
Source0:        %{url}/archive/refs/tags/%{version}.tar.gz
BuildArch:      noarch

# Static requirements must select the target before interpreter macros load.
BuildRequires:  python3.13-devel >= 3.13.18-1.3
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3.13-pytest


%description
A library that helps you read text from an unknown charset encoding.
Motivated by chardet, trying to resolve the issue by taking
a new approach. All IANA character set names for which the Python core
library provides codecs are supported.

%prep
%autosetup -n charset_normalizer-%{version}
# Drop mypy from build dependencies
sed -i 's/"mypy.*"//' pyproject.toml

%generate_buildrequires
%pyproject_buildrequires -r

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files charset_normalizer

%check
%pytest

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/normalizer

%changelog
%autochangelog
