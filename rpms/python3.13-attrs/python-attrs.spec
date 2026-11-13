# Avoid unwanted/unavailable dependencies in RHEL builds.
# Turn the tests off when bootstrapping Python, because pytest requires attrs
%bcond bootstrap 1
%bcond tests %[%{without bootstrap} && %{undefined rhel}]

# Identify this temporary stage in the PR, not the RPM release suffix.
%global __bootstrap %{nil}

Name:           python3.13-attrs
Version:        26.1.0
Release:        4.1%{?dist}
Summary:        Python attributes without boilerplate

# SPDX
License:        MIT
URL:            http://www.attrs.org/
BuildArch:      noarch
Source:         https://github.com/python-attrs/attrs/archive/%{version}/attrs-%{version}.tar.gz

# Static requirements must select the target before interpreter macros load.
BuildRequires:  python3.13-devel >= 3.13.18-1.3
BuildRequires:  python3.13-packaging

%global _description %{expand:
attrs is an MIT-licensed Python package with class decorators that
ease the chores of implementing the most common attribute-related
object protocols.}

%description %{_description}

%prep
%autosetup -p1 -n attrs-%{version}
# Remove undesired/optional test dependency on pympler
sed -i '/"pympler",/d' pyproject.toml

# Remove tests-mypy extra from tests-no-zope extra
sed -i "/attrs\[tests-mypy\]/d" pyproject.toml

# Preserve core tests without porting optional cloudpickle or parallel plugins.
%pyproject_patch_dependency cloudpickle:ignore:br_only
%pyproject_patch_dependency pytest-xdist:ignore:br_only

%generate_buildrequires
%pyproject_buildrequires %{?with_tests:-g tests}

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l attr attrs

%check
%pyproject_check_import
%if %{with tests}
%pytest
%endif

%files -f %{pyproject_files}
%doc README.md

%changelog
%autochangelog
