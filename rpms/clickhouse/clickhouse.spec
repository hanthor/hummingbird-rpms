# The debugsource package pulls in the entire source tarball (384k files
# including bundled LLVM tests, Rust vendor crates, docs, and images)
# rather than just compiled sources. Disable it on both arches — on
# aarch64 it is also empty due to debugedit/LTO interaction.
%undefine _debugsource_packages

Name:           clickhouse
Version:        26.7.1.1315
Release:        1%{?dist}
Summary:        Column-oriented database for online analytical processing

# ClickHouse is Apache-2.0. The remaining licenses cover statically linked,
# vendored dependencies recorded by the upstream system.licenses table.
License:        Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND ISC AND MIT AND MPL-2.0 AND OpenSSL AND PostgreSQL AND Zlib
URL:            https://clickhouse.com/
# Generated from the matching signed upstream tag with all submodules checked
# out. See SOURCE.md for the reproducible archive procedure.
Source0:        %{name}-%{version}.tar.zst
# Stub openssl/engine.h — Fedora's OpenSSL 3.x removed the deprecated
# ENGINE API, but some bundled ClickHouse deps include the header
# unconditionally. This shim defines OPENSSL_NO_ENGINE so those
# includes compile until upstream deps drop the legacy include.
Source1:        openssl-engine-compat.h
# PreLoad.cmake hard-errors on ANY non-empty compiler/linker flags —
# both environment variables and CMAKE_C/CXX_FLAGS. This gate lets us
# inject hardening flags via -DCMAKE_C/CXX_FLAGS while keeping the
# upstream protection against accidental environment pollution.
# Wolfi carries an equivalent allow_cflags.patch.
Patch0:         allow-build-flags.patch
# Wire system OpenSSL into the Rust delta-kernel-rs crate so it
# finds system headers and libraries instead of the bundled copy.
Patch1:         use-system-openssl.patch
# Replace the bundled libpq (PostgreSQL C client) with the system copy
# from libpq-devel. Keeps bundled libpqxx (C++ wrapper) which is tightly
# coupled to ClickHouse internals. Avoids carrying a second libpq that
# would need independent CVE tracking.
Patch2:         use-system-libpq.patch
# ClickHouse unconditionally loads the OpenSSL legacy provider at
# startup via OSSL_PROVIDER_load. With bundled OpenSSL the provider is
# compiled in (-DSTATIC_LEGACY). With system OpenSSL it lives in a
# separate .so that requires dlopen, which ClickHouse forbids. Make
# the load non-fatal since legacy algorithms (MD4, RC4, DES) are not
# needed for core operation.
Patch3:         system-openssl-skip-legacy-provider.patch
# ClickHouse's custom sysroot hides /usr/include from CMake feature
# checks, so the bundled liburing's compat.h unconditionally defines
# struct open_how. At compile time the system linux/openat2.h is still
# reachable, causing a redefinition error. Wrap with the kernel header's
# include guard.
Patch4:         liburing-openat2-compat.patch
# Silk's gtest global environment unconditionally initializes io_uring,
# aborting the entire test binary when the syscall is blocked by
# container seccomp policies. Probe io_uring before silk init and
# GTEST_SKIP when unavailable so all other tests still run.
Patch5:         skip-silk-tests-without-io-uring.patch

BuildRequires:  clang >= 21
BuildRequires:  cmake
BuildRequires:  gawk
BuildRequires:  lld
BuildRequires:  make
BuildRequires:  nasm
BuildRequires:  ninja-build
BuildRequires:  openssl-devel
BuildRequires:  perl-interpreter
BuildRequires:  python3
BuildRequires:  rust
BuildRequires:  cargo
BuildRequires:  libpq-devel
BuildRequires:  yasm
BuildRequires:  zstd

# The upstream build supports these two production architectures. Other
# architectures are experimental and are not covered by upstream binaries.
ExclusiveArch:  x86_64 aarch64

# ClickHouse bundles ~120 libraries in contrib/ and builds them statically.
# Upstream explicitly refuses to support system libraries for most of these;
# their build philosophy is "bundle everything" for reproducibility.
# Per Fedora Bundling Guidelines, declare the major bundled components.
#
# LLVM components (C++ stdlib, unwinder, compiler builtins)
Provides:       bundled(libc++)
Provides:       bundled(libc++abi)
Provides:       bundled(libunwind)
Provides:       bundled(compiler-rt)
# glibc compatibility shims (hermetic build, GLIBC_COMPATIBILITY=ON)
Provides:       bundled(glibc-compatibility)
# Data structures and algorithms
Provides:       bundled(abseil-cpp)
Provides:       bundled(boost)
Provides:       bundled(re2)
Provides:       bundled(simdjson)
Provides:       bundled(simdutf)
Provides:       bundled(pdqsort)
Provides:       bundled(cityhash)
Provides:       bundled(xxHash)
Provides:       bundled(croaring)
# Compression
Provides:       bundled(lz4)
Provides:       bundled(zstd)
Provides:       bundled(zlib-ng)
Provides:       bundled(brotli)
Provides:       bundled(bzip2)
Provides:       bundled(snappy)
Provides:       bundled(xz)
Provides:       bundled(libdeflate)
# Networking and RPC
Provides:       bundled(grpc)
Provides:       bundled(protobuf)
Provides:       bundled(c-ares)
Provides:       bundled(thrift)
Provides:       bundled(librdkafka)
Provides:       bundled(AMQP-CPP)
Provides:       bundled(nats-io)
# Database clients
Provides:       bundled(libpqxx)
Provides:       bundled(mariadb-connector-c)
Provides:       bundled(mongo-c-driver)
Provides:       bundled(mongo-cxx-driver)
Provides:       bundled(rocksdb)
Provides:       bundled(sqlite)
Provides:       bundled(cassandra)
# Cloud SDKs
Provides:       bundled(aws-sdk-cpp)
Provides:       bundled(azure-sdk-cpp)
Provides:       bundled(google-cloud-cpp)
# Security and auth
Provides:       bundled(krb5)
Provides:       bundled(cyrus-sasl)
Provides:       bundled(libssh)
Provides:       bundled(openldap)
Provides:       bundled(libgsasl)
# I/O and runtime
Provides:       bundled(liburing)
Provides:       bundled(jemalloc)
Provides:       bundled(libuv)
Provides:       bundled(libevent)
# Serialization and formats
Provides:       bundled(arrow)
Provides:       bundled(avro)
Provides:       bundled(capnproto)
Provides:       bundled(msgpack-c)
Provides:       bundled(rapidjson)
Provides:       bundled(yaml-cpp)
Provides:       bundled(libxml2)
Provides:       bundled(fmtlib)
# Text and encoding
Provides:       bundled(icu)
Provides:       bundled(idna)
Provides:       bundled(cld2)
Provides:       bundled(vectorscan)
# Geospatial
Provides:       bundled(h3)
Provides:       bundled(s2geometry)
# Misc
Provides:       bundled(libarchive)
Provides:       bundled(double-conversion)
Provides:       bundled(fast_float)
Provides:       bundled(magic_enum)
Provides:       bundled(nlohmann-json)

%description
ClickHouse is a column-oriented database management system for online
analytical processing. It can answer analytical queries in real time using
SQL and is designed for large data sets.


%package client
Summary:        Command-line clients for ClickHouse
Requires:       %{name}%{?_isa} = %{version}-%{release}

%description client
This package provides the ClickHouse command-line client, local query tool,
benchmark, formatter, compressor, and data obfuscator.


%package server
Summary:        ClickHouse database server
Requires:       %{name}%{?_isa} = %{version}-%{release}

%description server
This package provides the ClickHouse database server and its default
configuration.


%package keeper
Summary:        ClickHouse coordination service
Requires:       %{name}%{?_isa} = %{version}-%{release}

%description keeper
ClickHouse Keeper is a ZooKeeper-compatible coordination service used for
replication and distributed DDL queries.


%package tests
Summary:        Unit tests for ClickHouse
Requires:       %{name}%{?_isa} = %{version}-%{release}

%description tests
This package provides the ClickHouse unit test binary. Run on a host
(not in a container) for full coverage including Silk fiber/io_uring
tests.


%prep
%autosetup -p1
# Replace bundled OpenSSL source with symlinks to system headers. The
# hermetic build's cmake expects contrib/openssl/include to exist;
# ENABLE_OPENSSL_DYNAMIC links the system .so at runtime.
rm -rf contrib/openssl contrib/openssl-cmake
mkdir -p contrib/openssl/include
ln -sf /usr/include/openssl contrib/openssl/include/openssl
# Remove bundled PostgreSQL client; system libpq used via patch.
rm -rf contrib/postgres contrib/postgres-cmake
# Fedora's OpenSSL 3.x removed the deprecated ENGINE API header. Some
# bundled deps include it unconditionally. Provide a narrow shim that
# defines OPENSSL_NO_ENGINE so those includes compile.
install -Dpm 0644 %{SOURCE1} openssl-compat/openssl/engine.h
# Upstream's generated file contains a shortened build number even on the
# release tag. Preserve the complete RPM version in the binary metadata.
sed -i \
    -e 's|^SET(VERSION_STRING [^)]*)|SET(VERSION_STRING %{version})|' \
    -e 's|^SET(VERSION_DESCRIBE [^)]*)|SET(VERSION_DESCRIBE v%{version}-stable)|' \
    cmake/autogenerated_versions.txt
# PRQL's partial-link symbol stripping (ld.lld -r) produces broken
# .eh_frame relocations on x86_64 and aarch64. Upstream only excludes
# ppc64le; extend to our arches. Wolfi carries the same workaround.
sed -i \
    's/AND NOT CMAKE_SYSTEM_PROCESSOR STREQUAL "ppc64le")/AND NOT CMAKE_SYSTEM_PROCESSOR MATCHES "ppc64le|x86_64|aarch64")/' \
    rust/workspace/CMakeLists.txt


%build
# Hermetic build (CLICKHOUSE_OFFICIAL_BUILD) — upstream's bundled toolchain
# and dependencies, matching the Wolfi/Chainguard container packaging approach.
# System OpenSSL and libpq are grafted in via directory symlinks (%prep) and
# ENABLE_OPENSSL_DYNAMIC so CVEs in those libraries are fixed by a base image
# update without rebuilding ClickHouse.
# Hardening flags are injected via CMAKE_C/CXX_FLAGS since the hermetic build
# ignores environment CFLAGS. XRay is disabled because its bundled compiler-rt
# sources require obsolete kernel UAPI headers (linux/scc.h).
# Clear RPM-injected env flags — PreLoad.cmake hard-errors on any non-empty
# CFLAGS/CXXFLAGS/LDFLAGS. Our hardening flags go through CMAKE_C/CXX_FLAGS.
unset CFLAGS CXXFLAGS LDFLAGS
cmake -S . -B build -G Ninja \
    -DALLOW_CUSTOM_BUILD_FLAGS=ON \
    -DCHECK_LARGE_OBJECT_SIZES=0 \
    -DCLICKHOUSE_OFFICIAL_BUILD=1 \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_C_FLAGS="-I$PWD/openssl-compat -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fstack-clash-protection" \
    -DCMAKE_CXX_FLAGS="-I$PWD/openssl-compat -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fstack-clash-protection -Wno-error=unused-result" \
    -DCMAKE_INSTALL_LIBDIR=%{_lib} \
    -DCMAKE_INSTALL_PREFIX=%{_prefix} \
    -DCMAKE_SKIP_RPATH=ON \
    -DCOMPILER_CACHE=disabled \
    -DENABLE_OPENSSL=YES \
    -DENABLE_OPENSSL_DYNAMIC=YES \
    -DENABLE_SSL=ON \
    -DENABLE_XRAY=OFF \
    -DOPENSSL_INCLUDE_DIR=$PWD/contrib/openssl/include \
    -DOPENSSL_ROOT_DIR=/usr \
    -DWERROR=OFF
cmake --build build --parallel %{_smp_build_ncpus} \
    --target clickhouse unit_tests_dbms


%install
install -Dpm 0755 build/programs/clickhouse \
    %{buildroot}%{_bindir}/clickhouse

for command in \
    clickhouse-benchmark clickhouse-client clickhouse-compressor \
    clickhouse-disks clickhouse-format clickhouse-git-import \
    clickhouse-local clickhouse-obfuscator \
    clickhouse-static-files-disk-uploader \
    clickhouse-server clickhouse-keeper clickhouse-keeper-client \
    clickhouse-keeper-converter clickhouse-su; do
    ln -s clickhouse %{buildroot}%{_bindir}/${command}
done

install -Dpm 0644 programs/server/config.xml \
    %{buildroot}%{_sysconfdir}/clickhouse-server/config.xml
install -Dpm 0644 programs/server/users.xml \
    %{buildroot}%{_sysconfdir}/clickhouse-server/users.xml
install -Dpm 0644 programs/client/clickhouse-client.xml \
    %{buildroot}%{_sysconfdir}/clickhouse-client/config.xml
install -Dpm 0644 programs/keeper/keeper_config.xml \
    %{buildroot}%{_sysconfdir}/clickhouse-keeper/keeper_config.xml

install -d %{buildroot}%{_sysconfdir}/clickhouse-server/config.d
install -d %{buildroot}%{_sysconfdir}/clickhouse-server/users.d
install -d %{buildroot}%{_sysconfdir}/clickhouse-keeper/keeper_config.d

install -Dpm 0755 build/src/unit_tests_dbms \
    %{buildroot}%{_libexecdir}/clickhouse-tests/unit_tests_dbms


%check
# Linking integrity — resolve every dynamic relocation.
ldd -r build/programs/clickhouse

# Version string must match the RPM.
LD_BIND_NOW=1 build/programs/clickhouse --version | grep -F '%{version}'

# Basic SQL — arithmetic, aggregation, array functions.
build/programs/clickhouse local \
    --query 'SELECT sum(number) FROM numbers(100)' | grep -Fx 4950
build/programs/clickhouse local \
    --query "SELECT count(), min(number), max(number) FROM numbers(1000) WHERE number %% 3 = 0" \
    | grep -Fx $'334\t0\t999'
build/programs/clickhouse local \
    --query "SELECT arraySort(groupArray(x)) FROM (SELECT number %% 5 AS x FROM numbers(10) GROUP BY x)" \
    | grep -Fx '[0,1,2,3,4]'

# Table DDL — create, insert, select, drop.
build/programs/clickhouse local --multiquery "
    CREATE TABLE test_tbl (id UInt32, name String) ENGINE = Memory;
    INSERT INTO test_tbl VALUES (1, 'alice'), (2, 'bob'), (3, 'charlie');
    SELECT count() FROM test_tbl;
    DROP TABLE test_tbl;
" | grep -Fx 3

# String and date functions.
build/programs/clickhouse local \
    --query "SELECT length('hello'), upper('hello'), toYYYYMM(toDate('2026-08-01'))" \
    | grep -Fx $'5\tHELLO\t202608'

# System tables are accessible.
build/programs/clickhouse local \
    --query "SELECT count() > 0 FROM system.functions WHERE name = 'sum'" \
    | grep -Fx 1

# Compression round-trip.
echo 'hello clickhouse' | build/programs/clickhouse compressor | \
    build/programs/clickhouse compressor --decompress | grep -Fx 'hello clickhouse'

# Unit tests (silk tests auto-skip when io_uring is unavailable).
build/src/unit_tests_dbms


%files
%license LICENSE
%doc AUTHORS CHANGELOG.md README.md
%{_bindir}/clickhouse

%files client
%dir %{_sysconfdir}/clickhouse-client
%config(noreplace) %{_sysconfdir}/clickhouse-client/config.xml
%{_bindir}/clickhouse-benchmark
%{_bindir}/clickhouse-client
%{_bindir}/clickhouse-compressor
%{_bindir}/clickhouse-disks
%{_bindir}/clickhouse-format
%{_bindir}/clickhouse-git-import
%{_bindir}/clickhouse-local
%{_bindir}/clickhouse-obfuscator
%{_bindir}/clickhouse-static-files-disk-uploader

%files server
%{_bindir}/clickhouse-server
%{_bindir}/clickhouse-su
%dir %{_sysconfdir}/clickhouse-server
%dir %{_sysconfdir}/clickhouse-server/config.d
%dir %{_sysconfdir}/clickhouse-server/users.d
%config(noreplace) %{_sysconfdir}/clickhouse-server/config.xml
%config(noreplace) %{_sysconfdir}/clickhouse-server/users.xml

%files keeper
%{_bindir}/clickhouse-keeper
%{_bindir}/clickhouse-keeper-client
%{_bindir}/clickhouse-keeper-converter
%dir %{_sysconfdir}/clickhouse-keeper
%dir %{_sysconfdir}/clickhouse-keeper/keeper_config.d
%config(noreplace) %{_sysconfdir}/clickhouse-keeper/keeper_config.xml


%files tests
%dir %{_libexecdir}/clickhouse-tests
%{_libexecdir}/clickhouse-tests/unit_tests_dbms


%changelog
%autochangelog
