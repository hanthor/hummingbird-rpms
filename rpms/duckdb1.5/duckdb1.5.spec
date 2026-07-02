%bcond check 1

# The upstream release tarball does not carry a .git directory, so the
# CMake build cannot compute its own version via `git describe`. Use the
# version tag as the git describe string.
%global git_describe v%{version}

Name:           duckdb1.5
Version:        1.5.4
Release:        0.1%{?dist}
Summary:        In-process SQL OLAP database management system

License:        MIT
URL:            https://github.com/duckdb/duckdb
Source0:        https://github.com/duckdb/duckdb/archive/refs/tags/v%{version}.tar.gz#/duckdb-%{version}.tar.gz

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

%package -n %{name}
Summary:        Shared library for DuckDB

%description -n %{name}
This package contains the shared library (libduckdb.so) used to embed
DuckDB, an in-process SQL OLAP database management system, into
applications.

%package -n %{name}-devel
Summary:        Development files for DuckDB
Requires:       %{name}%{?_isa} = %{version}-%{release}

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
    -DBUILD_UNITTESTS=OFF \
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
out=$(%{buildroot}%{_bindir}/%{name} -c "SELECT 42")
echo "$out" | grep -q 42
%endif

%files
%license LICENSE
%doc README.md
%{_bindir}/%{name}

%ldconfig_scriptlets -n %{name}

%files -n %{name}
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
