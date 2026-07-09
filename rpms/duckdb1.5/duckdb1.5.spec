# Disable LTO: GCC 15 LTO miscompiles DuckDB's CSV subsystem, causing
# basic_string::_M_create crashes in 22 tests under _FORTIFY_SOURCE=3.
# Re-test with GCC 16.
%global _lto_cflags %{nil}

%bcond check 1

# The upstream release tarball does not carry a .git directory, so the
# CMake build cannot compute its own version via `git describe`. Use the
# version tag as the git describe string.
%global git_describe v%{version}

%if %{with check}
%global build_unittests ON
%else
%global build_unittests OFF
%endif

Name:           duckdb1.5
Version:        1.5.4
Release:        0.1%{?dist}
Summary:        In-process SQL OLAP database management system

License:        MIT
URL:            https://github.com/duckdb/duckdb
Source0:        https://github.com/duckdb/duckdb/archive/refs/tags/v%{version}.tar.gz#/duckdb-%{version}.tar.gz

# Fix out-of-bounds access in IEJoin completed array when stage reaches DONE
# https://github.com/duckdb/duckdb/issues/XXXXX
Patch0:         fix-iejoin-oob-access.patch

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  libicu-devel
BuildRequires:  ninja-build
BuildRequires:  python3

%description
DuckDB is an in-process SQL OLAP database management system. It is designed
to be fast, reliable, portable, and easy to use, supporting complex
analytical queries with a simple, feature-rich SQL dialect.

This package contains the duckdb command-line interface (CLI) shell.

%package -n %{name}-libs
Summary:        Shared library for DuckDB

%description -n %{name}-libs
This package contains the shared library (libduckdb.so) used to embed
DuckDB, an in-process SQL OLAP database management system, into
applications.

%package -n %{name}-devel
Summary:        Development files for DuckDB
Requires:       %{name}-libs%{?_isa} = %{version}-%{release}

%description -n %{name}-devel
This package contains the header files and CMake package configuration
needed to develop applications that link against %{name}.

%package -n %{name}-static
Summary:        Static library for DuckDB
Requires:       %{name}-devel%{?_isa} = %{version}-%{release}

%description -n %{name}-static
This package contains the static library (libduckdb_static.a) for DuckDB.

%prep
%autosetup -p1 -n duckdb-%{version}

%build
%cmake -G Ninja \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_CXX_STANDARD=17 \
    -DBUILD_UNITTESTS=%{build_unittests} \
    -DBUILD_SHELL=ON \
    -DBUILD_EXTENSIONS="icu" \
    -DWITH_INTERNAL_ICU=OFF \
    -DSET_DUCKDB_LIBRARY_VERSION=ON \
    -DOVERRIDE_GIT_DESCRIBE=%{git_describe}
%cmake_build

%install
%cmake_install

%check
%if %{with check}
out=$(%{buildroot}%{_bindir}/duckdb -c "SELECT 42")
echo "$out" | grep -q 42

# Run the upstream unit and SQL logic test suite. Using no explicit filter
# lets Catch2 exclude [.]-tagged (hidden) tests by default, which includes
# *.test_slow fixtures. Using "~[slow]" would paradoxically include them
# because an explicit filter overrides Catch2's default [.] exclusion.
%{__cmake_builddir}/test/unittest
%endif

%files
%license LICENSE
%doc README.md
%{_bindir}/duckdb

%ldconfig_scriptlets -n %{name}-libs

%files -n %{name}-libs
%{_libdir}/libduckdb.so.*

%files -n %{name}-devel
%{_includedir}/duckdb.h
%{_includedir}/duckdb.hpp
%{_includedir}/duckdb/
%{_libdir}/libduckdb.so
%{_libdir}/cmake/DuckDB/

%files -n %{name}-static
%{_libdir}/libduckdb_static.a

%changelog
%autochangelog
