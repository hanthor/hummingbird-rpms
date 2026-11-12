# pytest requires iniconfig, so seed it before restoring the final suite.
%bcond bootstrap 1
%bcond tests %{without bootstrap}

# Track this temporary stage in the PR, not in the RPM release suffix.
%global __bootstrap %{nil}

Name:               python3.13-iniconfig
Version:            2.3.0
Release:            5.1%{?dist}
Summary:            Brain-dead simple parsing of ini files
# SPDX
License:            MIT
URL:                http://github.com/pytest-dev/iniconfig
BuildArch:          noarch
# Anchor dependency resolution to the macro-owning Hummingbird interpreter.
BuildRequires:      python3.13-devel >= 3.13.18-1.3

# pytest 6+ needs this and this uses pytest for tests

%if %{with tests}
# We BR pytest manually to avoid a dependency on tox in ELN/RHEL
BuildRequires:      python%{python3_pkgversion}-pytest
%endif

Source:             %{url}/archive/v%{version}/iniconfig-%{version}.tar.gz

%global _description %{expand:
iniconfig is a small and simple INI-file parser module
having a unique set of features:

* tested against Python2.4 across to Python3.2, Jython, PyPy
* maintains order of sections and entries
* supports multi-line values with or without line-continuations
* supports "#" comments everywhere
* raises errors with proper line-numbers
* no bells and whistles like automatic substitutions
* iniconfig raises an Error if two sections have the same name.}
%description %_description


%prep
%autosetup -n iniconfig-%{version}


%generate_buildrequires
export SETUPTOOLS_SCM_PRETEND_VERSION=%{version}
%pyproject_buildrequires


%build
export SETUPTOOLS_SCM_PRETEND_VERSION=%{version}
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l iniconfig


%check
%pyproject_check_import
%if %{with tests}
%pytest -v
%endif


%files -f %{pyproject_files}
%doc README.rst


%changelog
%autochangelog
