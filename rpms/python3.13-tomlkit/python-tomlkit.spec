%global pypi_name tomlkit

%bcond bootstrap 1
%bcond tests %{without bootstrap}
# Only the TOML spec-data tests need the additional PyYAML package.
%bcond yaml_tests 0

# Track this temporary stage in the PR, not in the RPM release suffix.
%global __bootstrap %{nil}

%global common_description %{expand:
TOML Kit is a 1.0.0-compliant TOML library.

It includes a parser that preserves all comments, indentations, whitespace and
internal element ordering, and makes them accessible and editable via an
intuitive API.

You can also create new TOML documents from scratch using the provided helpers.

Part of the implementation has been adapted, improved and fixed from Molten.}

Name:           python3.13-%{pypi_name}
Summary:        Style preserving TOML library
Version:        0.15.2
Release:        0.1%{?dist}
License:        MIT

URL:            https://github.com/sdispater/tomlkit
Source:         %{pypi_source %{pypi_name}}

# Define project metadata via [project] table (PEP 621)
# This is an upstreamable change that allows us to change build backends more easily
# Merged upstream at https://github.com/python-poetry/tomlkit/pull/607
Patch:          PEP621.patch

# Change build backend to flit-core
# This is downstream only, built on PEP621.patch
# It is motivated purely by ELN/RHEL content set, where poetry-core is undesired.
# We apply this unconditionally, so it's more battle-tested.
# Notes:
#  - This does not migrate tool.poetry.include at all,
#    we do not care about sdist files and py.typed is included automatically.
#  - METADATA differs slightly wrt. missing automatic Python version classifiers etc
Patch:          flit-core.patch

BuildArch:      noarch

# Anchor dependency resolution to the macro-owning Hummingbird interpreter.
BuildRequires:  python3.13-devel >= 3.13.18-1.3

# test dependencies
%if %{with tests}
BuildRequires:  python%{python3_pkgversion}-pytest
%if %{with yaml_tests}
BuildRequires:  python3dist(pyyaml)
%endif
%endif

%description %{common_description}


%prep
%autosetup -n %{pypi_name}-%{version} -p1


%generate_buildrequires
%pyproject_buildrequires -r


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files --assert-license %{pypi_name}


%check
%pyproject_check_import
%if %{with tests}
%pytest %{!?with_yaml_tests:--ignore tests/test_toml_spec_tests.py}
%endif


%files -f %{pyproject_files}
%doc README.md


%changelog
%autochangelog
