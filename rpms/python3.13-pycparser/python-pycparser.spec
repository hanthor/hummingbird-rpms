%bcond_without tests

Name:           python3.13-pycparser
Summary:        C parser and AST generator written in Python
Version:        3.12
Release:        0.1%{?dist}
License:        BSD-3-Clause

URL:            https://github.com/eliben/pycparser
Source0:        %{url}/archive/release_v%{version}/pycparser-release_v%{version}.tar.gz
Source1:        pycparser-3.00-remove-relative-sys-path.py

BuildArch:      noarch

# Anchor dependency resolution to the macro-owning Hummingbird interpreter.
BuildRequires:  python3.13-devel >= 3.13.18-1.3

# for unit tests
%if %{with tests}
BuildRequires:  gcc
%endif

%description
pycparser is a complete parser for the C language, written in pure Python.
It is a module designed to be easily integrated into applications that
need to parse C source code.

%prep
%autosetup -p1 -n pycparser-release_v%{version}

# Remove relative sys.path from the examples
%{python3} %{SOURCE1} examples

%generate_buildrequires
%pyproject_buildrequires

%build
pushd pycparser
%{python3} _ast_gen.py
popd
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l pycparser

%check
%pyproject_check_import
export %{py3_test_envvars}
%if %{with tests}
%{python3} -m unittest discover
%endif
 
%files -f %{pyproject_files}
%doc README.rst
%doc examples

%changelog
%autochangelog
