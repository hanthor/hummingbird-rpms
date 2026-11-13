# Break the dependency loop - this is needed by maturin through python-setuptools-rust
# while django requires cryptography which requires maturin
%bcond bootstrap 0
%bcond django_tests 0

Name:           python3.13-semantic_version
Version:        2.10.0
Release:        27.1%{?dist}
Summary:        Library implementing the 'SemVer' scheme

License:        BSD-2-Clause
URL:            https://github.com/rbarrois/python-semanticversion
Source:         %{url}/archive/%{version}/python-semanticversion-%{version}.tar.gz

BuildArch:      noarch
# Static requirements must select the target before interpreter macros load.
BuildRequires:  python3.13-devel >= 3.13.18-1.3

# Test dependencies manually cherry-picked from the [dev] extra
# Upstream uses nose2, but pytest works as well
BuildRequires:  python3.13-pytest
%if (%{undefined rhel} || %{defined epel}) && %{without bootstrap} && %{with django_tests}
# Optional test dependency
BuildRequires:  python3-django
%endif

%global _description %{expand:
This small python library provides a few tools to handle semantic versioning in
Python.}

%description %{_description}

%prep
%autosetup -p1 -n python-semanticversion-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l semantic_version

%check
%pyproject_check_import -e semantic_version.django_fields
%pytest

%files -f %{pyproject_files}
%doc README.rst ChangeLog CREDITS

%changelog
%autochangelog
