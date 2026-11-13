Name:           python3.13-cffi
Version:        2.1.1
Release:        1.1%{?dist}
Summary:        Foreign Function Interface for Python to call C code
# cffi is MIT-0
%global upstream_license MIT-0
# cffi/_imp_emulation.py has bits copied from CPython (PSF-2.0)
# see https://github.com/python-cffi/cffi/issues/290
License:        %{upstream_license} AND PSF-2.0
URL:            https://github.com/python-cffi/cffi
Source:         %{url}/archive/v%{version}/cffi-%{version}.tar.gz

# Static requirements must select the target before interpreter macros load.
BuildRequires:  python3.13-devel >= 3.13.18-1.3
BuildRequires:  python3.13-pytest
BuildRequires:  make
BuildRequires:  libffi-devel
BuildRequires:  gcc

# For tests:
BuildRequires:  gcc-c++

%description
Foreign Function Interface for Python, providing a convenient and
reliable way of calling existing C code from Python. The interface is
based on LuaJIT’s FFI.


%package doc
Summary:        Documentation for CFFI
BuildArch:      noarch
BuildRequires:  python3-sphinx

%description doc
Documentation for CFFI, the Foreign Function Interface for Python.


%prep
%autosetup -p1 -n cffi-%{version}


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel

cd doc
make html
rm build/html/.buildinfo


%install
%pyproject_install
%pyproject_save_files _cffi_backend cffi


%check
%pyproject_verify_license %{upstream_license}
%pytest


%files -f %{pyproject_files}
%doc README.md
%{_bindir}/cffi-gen-src

%files doc
%license LICENSE
%doc doc/build/html


%changelog
%autochangelog
