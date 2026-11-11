%bcond bootstrap 1
%bcond tests %{without bootstrap}

# Track this temporary stage in the PR, not in the RPM release suffix.
%global __bootstrap %{nil}

Name:           python3.13-trove-classifiers
Version:        2026.9.21.13
Release:        1.1%{?dist}
Summary:        Canonical source for classifiers on PyPI (pypi.org)

License:        Apache-2.0
URL:            https://github.com/pypa/trove-classifiers
Source:         %{pypi_source trove_classifiers}

# Drop dependency on calver which is not packaged in Fedora.
# This patch is rebased version of upstream PR:
# https://github.com/pypa/trove-classifiers/pull/126/commits/809156bb35852bcaa1c753e0165f1814f2bcedf6
Patch:          Move-to-PEP-621-declarative-metadata.patch

BuildArch:      noarch
# Anchor dependency resolution to the macro-owning Hummingbird interpreter.
BuildRequires:  python3.13-devel >= 3.13.18-1.3

# Tests require python-iniconfig which requires python-trove-classifiers
# The bcond is needed for new Python bootstrap

%if %{with tests}
BuildRequires:  python%{python3_pkgversion}-pytest
%endif

%global _description %{expand:
Canonical source for classifiers on PyPI.
Classifiers categorize projects per PEP 301. Use this package to validate
classifiers in packages for PyPI upload or download.
}

%description %_description

%prep
%autosetup -p1 -n trove_classifiers-%{version}
# Replace @@VERSION@@ with %%version
%writevars -f pyproject.toml version

# Make the the CLI tests work in %%check
# https://github.com/pypa/trove-classifiers/issues/219
sed -i 's@{BINDIR}/@@' tests/test_cli.py


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files trove_classifiers


%check
%pyproject_check_import
%if %{with tests}
%pytest
%endif


%files -f %{pyproject_files}
%doc README.*
%{_bindir}/trove-classifiers


%changelog
%autochangelog
