# Break the pytest / setuptools-scm cycle for the initial seed only.
%bcond bootstrap 1
%bcond tests %{without bootstrap}

# The optional rich companion would expand the target package set.
%bcond rich 0

# Track this temporary stage in the PR, not in the RPM release suffix.
%global __bootstrap %{nil}

Name:           python3.13-setuptools_scm
Version:        10.3.4
Release:        0.1%{?dist}
Summary:        Blessed package to manage your versions by SCM tags

# SPDX
License:        MIT
URL:            https://github.com/pypa/setuptools_scm/
Source:         %{pypi_source setuptools_scm}

BuildArch:      noarch

# Static requirements must select the target before interpreter macros load.
BuildRequires:  python3.13-devel >= 3.13.18-1.3
BuildRequires:  python3.13-packaging
%if %{with tests}
BuildRequires:  git-core
# Don't pull mercurial into RHEL just to test this work with it
%if %{undefined rhel}
BuildRequires:  mercurial
%endif
%endif

%py_provides    python%{python3_pkgversion}-setuptools-scm

%description
Setuptools_scm handles managing your Python package versions in SCM metadata.
It also handles file finders for the supported SCMs.


# We don't package the [rich] extra on RHELs, to avoid pulling rich into the buildroot
%pyproject_extras_subpkg -n python%{python3_pkgversion}-setuptools_scm simple,toml%{?with_rich:,rich}


%prep
%autosetup -p1 -n setuptools_scm-%{version}
# Remove flake8, mypy, ruff, … from the test dependencies
%pyproject_patch_dependency griffe:ignore
%pyproject_patch_dependency griffe-public-wildcard-imports:ignore
%pyproject_patch_dependency griffecli:ignore
%pyproject_patch_dependency mypy:ignore
%pyproject_patch_dependency ruff:ignore
%pyproject_patch_dependency flake8:ignore
%if %{defined rhel}
# Remove unnecessary test dependencies:
%pyproject_patch_dependency rich:ignore
%pyproject_patch_dependency pytest-timeout:ignore
%pyproject_patch_dependency pytest-xdist:ignore
sed -Ei '/^\[tool.pytest.ini_options\]/,/^\[/ { /^timeout/d }' pyproject.toml
%endif


%generate_buildrequires
# Note: We only pull in the [rich] extra when running tests.
# This is to make the new Python version bootstrapping simpler
# as setuptools_scm is an early package and rich is a late one.
%pyproject_buildrequires %{?with_tests:-g test %{!?rhel:-x rich}} -x simple,toml


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files setuptools_scm

# Allow parallel-installable executable for alternate Python stacks (e.g. in RHEL)
mv %{buildroot}%{_bindir}/setuptools-scm %{buildroot}%{_bindir}/setuptools-scm-%{python3_version}
%if "%{python3_pkgversion}" == "3"
ln -s ./setuptools-scm-%{python3_version} %{buildroot}%{_bindir}/setuptools-scm
%endif


%check
%pyproject_check_import
%if %{with tests}
# test_pip_download, test_xmlsec_download_regression try to download from the internet
# test_get_version_blank_tag_regex, test_setuptools_version_keyword_ensures_regex fail because
# vcs-versioning 2.x added DeprecationWarnings for tag_regex that the tests don't expect;
# fixed upstream: https://github.com/pypa/setuptools-scm/commit/ba215a70a88bbd35ecf0961c8b1a1301c940629e
%pytest -v %{!?rhel:-n auto} -k 'not test_pip_download and not test_xmlsec_download_regression and not test_get_version_blank_tag_regex and not test_setuptools_version_keyword_ensures_regex'
%endif


%files -f %{pyproject_files}
%doc README.md
%{_bindir}/setuptools-scm-%{python3_version}
%if "%{python3_pkgversion}" == "3"
%{_bindir}/setuptools-scm
%endif


%changelog
%autochangelog
