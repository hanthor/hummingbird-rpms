
%if 0%{?fedora} >= 41
# on Fedora >= 41
%undefine __brp_add_determinism
%endif
%global debug_package %{nil}
%undefine _auto_set_build_flags

Version: 6.4.0

# Main swift source and version
%global forgeurl0  https://github.com/swiftlang/swift
%global version0   %{version}
%global tag0       swift-%{version0}-RELEASE
%global subdir0    swift

# Begin forge sources
%global forgeurl1  https://github.com/google/boringssl
%global tag1       817ab07ebb53da35afea409ab9328f578492832d
%global subdir1    boringssl

%global forgeurl2  https://github.com/google/brotli
%global tag2       v1.2.0
%global subdir2    brotli

%global forgeurl3  https://github.com/KitWare/CMake
%global tag3       v3.30.2
%global subdir3    cmake

%global forgeurl4  https://github.com/swiftlang/swift-cmark
%global tag4       swift-%{version0}-RELEASE
%global subdir4    cmark

%global forgeurl5  https://github.com/curl/curl
%global tag5       curl-8_9_1
%global subdir5    curl

%global forgeurl6  https://github.com/swiftlang/indexstore-db
%global tag6       swift-%{version0}-RELEASE
%global subdir6    indexstore-db

%global forgeurl7  https://github.com/gnome/libxml2
%global tag7       v2.11.5
%global subdir7    libxml2

%global forgeurl8  https://github.com/swiftlang/swift-llbuild
%global tag8       swift-%{version0}-RELEASE
%global subdir8    llbuild

%global forgeurl9  https://github.com/swiftlang/llvm-project
%global tag9       swift-%{version0}-RELEASE
%global subdir9    llvm-project

%global forgeurl10  https://github.com/microsoft/mimalloc
%global tag10       v3.0.3
%global subdir10    mimalloc

%global forgeurl11  https://github.com/ninja-build/ninja
%global tag11       v1.13.1
%global subdir11    ninja

%global forgeurl12  https://github.com/swiftlang/sourcekit-lsp
%global tag12       swift-%{version0}-RELEASE
%global subdir12    sourcekit-lsp

%global forgeurl13  https://github.com/apple/swift-argument-parser
%global tag13       1.6.1
%global subdir13    swift-argument-parser

%global forgeurl14  https://github.com/apple/swift-asn1
%global tag14       1.3.2
%global subdir14    swift-asn1

%global forgeurl15  https://github.com/apple/swift-async-algorithms
%global tag15       1.0.1
%global subdir15    swift-async-algorithms

%global forgeurl16  https://github.com/apple/swift-atomics
%global tag16       1.2.0
%global subdir16    swift-atomics

%global forgeurl17  https://github.com/swiftlang/swift-build
%global tag17       swift-%{version0}-RELEASE
%global subdir17    swift-build

%global forgeurl18  https://github.com/apple/swift-certificates
%global tag18       1.10.1
%global subdir18    swift-certificates

%global forgeurl19  https://github.com/apple/swift-collections
%global tag19       1.1.6
%global subdir19    swift-collections

%global forgeurl20  https://github.com/swiftlang/swift-corelibs-blocksruntime
%global tag20       swift-%{version0}-RELEASE
%global subdir20    swift-corelibs-blocksruntime

%global forgeurl21  https://github.com/swiftlang/swift-corelibs-foundation
%global tag21       swift-%{version0}-RELEASE
%global subdir21    swift-corelibs-foundation

%global forgeurl22  https://github.com/swiftlang/swift-corelibs-libdispatch
%global tag22       swift-%{version0}-RELEASE
%global subdir22    swift-corelibs-libdispatch

%global forgeurl23  https://github.com/swiftlang/swift-corelibs-xctest
%global tag23       swift-%{version0}-RELEASE
%global subdir23    swift-corelibs-xctest

%global forgeurl24  https://github.com/apple/swift-crypto
%global tag24       3.12.5
%global subdir24    swift-crypto

%global forgeurl25  https://github.com/swiftlang/swift-docc
%global tag25       swift-%{version0}-RELEASE
%global subdir25    swift-docc

%global forgeurl26  https://github.com/swiftlang/swift-docc-render-artifact
%global tag26       swift-%{version0}-RELEASE
%global subdir26    swift-docc-render-artifact

%global forgeurl27  https://github.com/swiftlang/swift-docc-symbolkit
%global tag27       swift-%{version0}-RELEASE
%global subdir27    swift-docc-symbolkit

%global forgeurl28  https://github.com/swiftlang/swift-driver
%global tag28       swift-%{version0}-RELEASE
%global subdir28    swift-driver

%global forgeurl29  https://github.com/swiftlang/swift-experimental-string-processing
%global tag29       swift-%{version0}-RELEASE
%global subdir29    swift-experimental-string-processing

%global forgeurl30  https://github.com/swiftlang/swift-format
%global tag30       swift-%{version0}-RELEASE
%global subdir30    swift-format

%global forgeurl31  https://github.com/swiftlang/swift-foundation
%global tag31       swift-%{version0}-RELEASE
%global subdir31    swift-foundation

%global forgeurl32  https://github.com/swiftlang/swift-foundation-icu
%global tag32       swift-%{version0}-RELEASE
%global subdir32    swift-foundation-icu

%global forgeurl33  https://github.com/swiftlang/swift-installer-scripts
%global tag33       swift-%{version0}-RELEASE
%global subdir33    swift-installer-scripts

%global forgeurl34  https://github.com/swiftlang/swift-integration-tests
%global tag34       swift-%{version0}-RELEASE
%global subdir34    swift-integration-tests

%global forgeurl35  https://github.com/swiftlang/swift-llvm-bindings
%global tag35       swift-%{version0}-RELEASE
%global subdir35    swift-llvm-bindings

%global forgeurl36  https://github.com/swiftlang/swift-lmdb
%global tag36       swift-%{version0}-RELEASE
%global subdir36    swift-lmdb

%global forgeurl37  https://github.com/apple/swift-log
%global tag37       1.5.4
%global subdir37    swift-log

%global forgeurl38  https://github.com/swiftlang/swift-markdown
%global tag38       swift-%{version0}-RELEASE
%global subdir38    swift-markdown

%global forgeurl39  https://github.com/apple/swift-nio
%global tag39       2.92.2
%global subdir39    swift-nio

%global forgeurl40  https://github.com/apple/swift-numerics
%global tag40       1.0.2
%global subdir40    swift-numerics

%global forgeurl41  https://github.com/swiftlang/swift-sdk-generator
%global tag41       swift-%{version0}-RELEASE
%global subdir41    swift-sdk-generator

%global forgeurl42  https://github.com/swiftlang/swift-stress-tester
%global tag42       swift-%{version0}-RELEASE
%global subdir42    swift-stress-tester

%global forgeurl43  https://github.com/swiftlang/swift-subprocess
%global tag43       0.4
%global subdir43    swift-subprocess

%global forgeurl44  https://github.com/swiftlang/swift-syntax
%global tag44       swift-%{version0}-RELEASE
%global subdir44    swift-syntax

%global forgeurl45  https://github.com/apple/swift-system
%global tag45       1.5.0
%global subdir45    swift-system

%global forgeurl46  https://github.com/swiftlang/swift-testing
%global tag46       swift-%{version0}-RELEASE
%global subdir46    swift-testing

%global forgeurl47  https://github.com/swiftlang/swift-toolchain-sqlite
%global tag47       1.0.9
%global subdir47    swift-toolchain-sqlite

%global forgeurl48  https://github.com/swiftlang/swift-tools-protocols
%global tag48       swift-%{version0}-RELEASE
%global subdir48    swift-tools-protocols

%global forgeurl49  https://github.com/swiftlang/swift-tools-support-core
%global tag49       swift-%{version0}-RELEASE
%global subdir49    swift-tools-support-core

%global forgeurl50  https://github.com/apple/swift-xcode-playground-support
%global tag50       swift-%{version0}-RELEASE
%global subdir50    swift-xcode-playground-support

%global forgeurl51  https://github.com/swiftlang/swift-package-manager
%global tag51       swift-%{version0}-RELEASE
%global subdir51    swiftpm

%global forgeurl52  https://github.com/WebAssembly/wasi-libc
%global tag52       wasi-sdk-31
%global subdir52    wasi-libc

%global forgeurl53  https://github.com/swiftwasm/WasmKit
%global tag53       0.3.1
%global subdir53    wasmkit

%global forgeurl54  https://github.com/madler/zlib
%global tag54       v1.3.1
%global subdir54    zlib

# End forge sources

Name:           swift-lang
Release:        0.2%{?dist}
%forgemeta -a

Summary:        The Swift programming language
License:        Apache-2.0
URL:            https://www.swift.org

%{lua:
for i = 0, 54 do
  local forgesource = rpm.expand("%{?forgesource" .. i .. "}")
  if forgesource ~= "" then
    print("Source" .. i .. ":    " .. forgesource .. "\n")
  end
end
}
Source99:       swiftlang.conf
Source100:      fedora-presets.ini

# NOTE: The patch number corresponds to the source it's packaging. For example,
# Patch31 is patching Source31, swift-foundation.
Patch0:         swift.patch
Patch9:         llvm-project.patch
Patch4:         cmark.patch
Patch8:         llbuild.patch
Patch51:        swiftpm.patch
Patch31:        swift-foundation.patch
Patch22:        swift-corelibs-libdispatch.patch
Patch24:        swift-crypto-CVE-2026-43823.patch
Patch3:         cmake.patch

BuildRequires:  clang
BuildRequires:  swig
BuildRequires:  rsync
BuildRequires:  python3
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  libxml2-devel
BuildRequires:  sqlite-devel
BuildRequires:  libcurl-devel
BuildRequires:  libuuid-devel
BuildRequires:  libedit-devel
BuildRequires:  perl-podlators
BuildRequires:  lld
BuildRequires:  kernel-headers
BuildRequires:  kernel-cross-headers
# Swift 6.4 no longer supports the C++-only bootstrap compiler.  Use the
# previous Swift toolchain as the host compiler for the build.
BuildRequires:  swift-lang

Requires:       glibc-devel
Requires:       lld
Requires:       gcc
Requires:       %{name}-runtime = %{version}-%{release}

Recommends:     libstdc++-devel
Recommends:     gcc-c++

ExclusiveArch:  x86_64 aarch64 

Provides:       swiftlang = %{version}-%{release}

# https://bugzilla.redhat.com/show_bug.cgi?id=2291122
# (python3-swiftclient provides a program called "swift" 
# that clashes with the binary created by this package)
# This is currently for all versions, so we don't
# specify one
Conflicts:      python3-swiftclient


# Per https://bugzilla.redhat.com/show_bug.cgi?id=2324076 we
# need to exclude all of the LLVM libraries, basically everything
# we bundle, from being picked up by the RPM dependency 
# generator for "provides" (i.e. we don't want to have our
# version of liblldb.so found when someone is searching for 
# general version of LLDB).
%global __provides_exclude ^(libLTO[.]so.*|libclang_rt.*.so.*|liblldb[.]so.*)$
%global __requires_exclude ^(libLTO[.]so.*|libclang_rt.*.so.*|liblldb[.]so.*)$


%description
Swift is a general-purpose programming language built using 
a modern approach to safety, performance, and software design 
patterns.

The goal of the Swift project is to create the best available 
language for uses ranging from systems programming, to mobile 
and desktop apps, scaling up to cloud services. Most 
importantly, Swift is designed to make writing and maintaining 
correct programs easier for the developer. 

%package runtime
Summary:        Swift runtime libraries

%description runtime
Swift is a general-purpose programming language built using 
a modern approach to safety, performance, and software design 
patterns.

This package contains the Swift runtime libraries needed to run 
applications built with Swift.



%prep
%forgesetup -a
cd %{builddir}
%{lua:
for i = 0, 54 do
  local subdir = rpm.expand("%{?subdir" .. i .. "}")
  if subdir ~= "" then
    print(rpm.expand("mv %{archivename" .. i .. "} " .. subdir .. "\n"))
  end
end
}

%patch 0
%patch 9
%patch 4
%patch 8
%patch 51
%patch 31
%patch 22
%patch 24
%patch 3

# Install custom Fedora preset
cp %{SOURCE100} swift/utils/fedora-presets.ini

# Fix python to python3 
%py3_shebang_fix swift/utils/api_checker/swift-api-checker.py
%py3_shebang_fix llvm-project/compiler-rt/lib/hwasan/scripts/hwasan_symbolize

%global buildsubdir %{nil}
%build
export VERBOSE=1

# Workaround for GCC 16 ICE in cmath / riemann_zeta
export CFLAGS="-O2 -g -fno-builtin-logf"
export CXXFLAGS="-O2 -g -fno-builtin-logf"

# Swift 6.4 requires Swift-in-Swift sources when building the compiler.  The
# upstream-supported path is hosttools, using the previous Swift release as
# the host compiler.
for swift_host_bin in %{_libexecdir}/swift/*/bin; do
    if [ -x "$swift_host_bin/swiftc" ]; then
        export PATH="$swift_host_bin:$PATH"
        break
    fi
done
command -v swiftc

echo "=== Building final production toolchain with host Swift compiler ==="
%{builddir}/swift/utils/build-script --preset=fedora_final \
    --preset-file=%{builddir}/swift/utils/fedora-presets.ini \
    build_subdir=fedora_final \
    install_destdir=%{_builddir} \
    installable_package=%{_builddir}/swift-%{version}-f%{fedora}.tar.gz \
    extra-cmake-options="-DLLVM_USE_LINKER=lld -DCLANG_DEFAULT_LINKER=ld.lld -DCMAKE_EXE_LINKER_FLAGS=-Wl,-z,relro,-z,now -DCMAKE_SHARED_LINKER_FLAGS=-Wl,-z,relro,-z,now" \
    swift-cmake-options="-DSWIFT_USE_LINKER=lld"


%install
# Create directory structure
mkdir -p %{buildroot}%{_bindir}
mkdir -p %{buildroot}%{_libdir}
mkdir -p %{buildroot}%{_libexecdir}/swift/%{version}/bin
mkdir -p %{buildroot}%{_libexecdir}/swift/%{version}/lib
mkdir -p %{buildroot}%{_libexecdir}/swift/%{version}/include
mkdir -p %{buildroot}%{_includedir}/swift
mkdir -p %{buildroot}%{_datadir}/swift
mkdir -p %{buildroot}%{_mandir}/man1
mkdir -p %{buildroot}%{_sysconfdir}/ld.so.conf.d

# Install executables to %{_libexecdir} to maintain Swift's internal directory structure
cp -a %{_builddir}/usr/bin/* %{buildroot}%{_libexecdir}/swift/%{version}/bin/

# Create symlinks in %{_bindir} for user-facing tools
TOOLS="swift swiftc swift-build swift-run swift-package swift-test sourcekit-lsp swift-format swift-demangle"
for tool in ${TOOLS}; do
    if [ -f %{buildroot}%{_libexecdir}/swift/%{version}/bin/$tool ]; then
        ln -sf %{_libexecdir}/swift/%{version}/bin/$tool %{buildroot}%{_bindir}/$tool
    fi
done

# Install Swift runtime libraries to %{_libdir}
if [ -d %{_builddir}/usr/lib/swift/linux ]; then
    for lib in %{_builddir}/usr/lib/swift/linux/*.so*; do
        [ -L "$lib" ] && continue  # Skip symlinks, we'll handle them later
        [ -f "$lib" ] && install -m 0755 "$lib" %{buildroot}%{_libdir}/
    done
    # Now create symlinks
    for link in %{_builddir}/usr/lib/swift/linux/*.so*; do
        [ -L "$link" ] || continue
        target=$(readlink "$link")
        linkname=$(basename "$link")
        ln -sf "$target" %{buildroot}%{_libdir}/$linkname
    done
fi

# Install libsourcekitdInProc.so from build directory to %%{_builddir}/usr/lib (it's not installed by CMake)
if [ -f %{_builddir}/build/fedora_final/swift-linux-%{_arch}/lib/libsourcekitdInProc.so ]; then
    cp -a %{_builddir}/build/fedora_final/swift-linux-%{_arch}/lib/libsourcekitdInProc.so %{_builddir}/usr/lib/
fi

# Install private libraries to toolchain lib dir
for lib in libIndexStore.so libIndexStore.so.17.0 libsourcekitdInProc.so libswiftDemangle.so; do
    if [ -f %{_builddir}/usr/lib/$lib ]; then
        install -m 0755 %{_builddir}/usr/lib/$lib %{buildroot}%{_libexecdir}/swift/%{version}/lib/
    fi
done
# Create any missing version symlinks in private dir
if [ -f %{buildroot}%{_libexecdir}/swift/%{version}/lib/libIndexStore.so.17.0 ] && [ ! -e %{buildroot}%{_libexecdir}/swift/%{version}/lib/libIndexStore.so ]; then
    ln -sf libIndexStore.so.17.0 %{buildroot}%{_libexecdir}/swift/%{version}/lib/libIndexStore.so
fi

# Install lldb libraries to %{_libexecdir} (private, bundled with Swift toolchain)
if [ -d %{_builddir}/usr/lib ]; then
    for lib in %{_builddir}/usr/lib/liblldb.so*; do
        if [ -e "$lib" ]; then
            mkdir -p %{buildroot}%{_libexecdir}/swift/%{version}/lib
            cp -a "$lib" %{buildroot}%{_libexecdir}/swift/%{version}/lib/
        fi
    done
fi

# Install lldb Python bindings
if [ -d %{_builddir}/usr/lib64/python%{python3_version}/site-packages/lldb ]; then
    mkdir -p %{buildroot}%{_libexecdir}/swift/%{version}/lib64/python%{python3_version}/site-packages
    cp -a %{_builddir}/usr/lib64/python%{python3_version}/site-packages/lldb \
          %{buildroot}%{_libexecdir}/swift/%{version}/lib64/python%{python3_version}/site-packages/
fi

# Install compiler private libraries and modules to %{_libexecdir}
mkdir -p %{buildroot}%{_libexecdir}/swift/%{version}/lib/swift
cp -a %{_builddir}/usr/lib/swift/* %{buildroot}%{_libexecdir}/swift/%{version}/lib/swift/
# Remove the runtime .so files we already installed to %{_libdir}
if [ -d %{buildroot}%{_libexecdir}/swift/%{version}/lib/swift/linux ]; then
    find %{buildroot}%{_libexecdir}/swift/%{version}/lib/swift/linux -name '*.so*' -type f -delete
    find %{buildroot}%{_libexecdir}/swift/%{version}/lib/swift/linux -name '*.so*' -type l -delete
fi

# Install clang resource directory (sanitizer libraries, builtins)
# Keep only what Swift needs internally
if [ -d %{_builddir}/usr/lib/clang ]; then
    mkdir -p %{buildroot}%{_libexecdir}/swift/%{version}/lib/clang
    cp -a %{_builddir}/usr/lib/clang/* %{buildroot}%{_libexecdir}/swift/%{version}/lib/clang/
fi

# Install SwiftDemangle headers (public API)
if [ -d %{_builddir}/swift/include/swift/SwiftDemangle ]; then
    cp -a %{_builddir}/swift/include/swift/SwiftDemangle %{buildroot}%{_includedir}/swift/
fi

# Install Swift headers and modulemaps (for C interop)
if [ -d %{_builddir}/usr/include ]; then
    cp -a %{_builddir}/usr/include/* %{buildroot}%{_libexecdir}/swift/%{version}/include/
fi

# Install data files
if [ -d %{_builddir}/usr/share/swift ]; then
    cp -a %{_builddir}/usr/share/swift/* %{buildroot}%{_datadir}/swift/
fi
mkdir -p %{buildroot}%{_datadir}/pm
cp -a %{_builddir}/usr/share/pm/. %{buildroot}%{_datadir}/pm/

# Install man pages
if [ -f %{_builddir}/usr/share/man/man1/swift.1 ]; then
    install -m 0644 %{_builddir}/usr/share/man/man1/swift.1 %{buildroot}%{_mandir}/man1/
fi

# Create compatibility symlink for module lookup
ln -sf %{_libexecdir}/swift/%{version}/lib/swift %{buildroot}%{_libdir}/swift

# Install ld.so.conf.d configuration
install -m 0644 %{SOURCE99} %{buildroot}%{_sysconfdir}/ld.so.conf.d/swiftlang.conf


# This is to fix an issue with check-rpaths complaining about
# how the Swift binaries use RPATH
export QA_SKIP_RPATHS=1


%files
%license swift/LICENSE.txt

# User-facing executables (symlinks to %%{_libexecdir}/swift/%%{version}/bin/)
%{_bindir}/swift
%{_bindir}/swiftc
%{_bindir}/swift-build
%{_bindir}/swift-run
%{_bindir}/swift-package
%{_bindir}/swift-test
%{_bindir}/sourcekit-lsp
%{_bindir}/swift-format
%{_bindir}/swift-demangle

# Man pages
%{_mandir}/man1/swift.1.gz

# SourceKit/IndexStore/SwiftDemangle private libraries
%{_libexecdir}/swift/%{version}/lib/libIndexStore.so*
%{_libexecdir}/swift/%{version}/lib/libsourcekitdInProc.so*
%{_libexecdir}/swift/%{version}/lib/libswiftDemangle.so*

# Compatibility symlink for module lookup
%{_libdir}/swift

# Backend tools and compiler internals
%{_libexecdir}/swift/

# Public headers
%{_includedir}/swift/

# Data files (diagnostics, features.json, etc.)
%{_datadir}/swift/
%{_datadir}/pm/

%files runtime
%license swift/LICENSE.txt

%{_libdir}/libswift*.so*
%{_libdir}/libFoundation*.so*
%{_libdir}/libdispatch.so*
%{_libdir}/libBlocksRuntime.so*
%{_libdir}/libTesting.so*
%{_libdir}/libXCTest.so*
%{_libdir}/lib_*.so*

# Dynamic linker configuration
%{_sysconfdir}/ld.so.conf.d/swiftlang.conf


%post -p /sbin/ldconfig
%postun -p /sbin/ldconfig

%post runtime -p /sbin/ldconfig
%postun runtime -p /sbin/ldconfig


%changelog
%autochangelog
