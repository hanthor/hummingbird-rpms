
%if 0%{?fedora} >= 41
# on Fedora >= 41
%undefine __brp_add_determinism
%endif
%global debug_package %{nil}
%undefine _auto_set_build_flags

Version: 6.4.2

# Main swift source and version
%global forgeurl0  https://github.com/swiftlang/swift
%global version0   %{version}
%global tag0       swift-%{version0}-RELEASE
%global subdir0    swift
%global source_swift 0

# Begin forge sources
%global source_cmark 1
%global forgeurl1  https://github.com/swiftlang/swift-cmark
%global tag1       swift-%{version0}-RELEASE
%global subdir1    cmark

%global source_indexstore_db 2
%global forgeurl2  https://github.com/swiftlang/indexstore-db
%global tag2       swift-%{version0}-RELEASE
%global subdir2    indexstore-db

%global source_llbuild 3
%global forgeurl3  https://github.com/swiftlang/swift-llbuild
%global tag3       swift-%{version0}-RELEASE
%global subdir3    llbuild

%global source_llvm_project 4
%global forgeurl4  https://github.com/swiftlang/llvm-project
%global tag4       swift-%{version0}-RELEASE
%global subdir4    llvm-project

%global source_sourcekit_lsp 5
%global forgeurl5  https://github.com/swiftlang/sourcekit-lsp
%global tag5       swift-%{version0}-RELEASE
%global subdir5    sourcekit-lsp

%global source_swift_argument_parser 6
%global forgeurl6  https://github.com/apple/swift-argument-parser
%global tag6       1.6.1
%global subdir6    swift-argument-parser

%global source_swift_asn1 7
%global forgeurl7  https://github.com/apple/swift-asn1
%global tag7       1.3.2
%global subdir7    swift-asn1

%global source_swift_atomics 8
%global forgeurl8  https://github.com/apple/swift-atomics
%global tag8       1.2.0
%global subdir8    swift-atomics

%global source_swift_build 9
%global forgeurl9  https://github.com/swiftlang/swift-build
%global tag9       swift-%{version0}-RELEASE
%global subdir9    swift-build

%global source_swift_certificates 10
%global forgeurl10  https://github.com/apple/swift-certificates
%global tag10       1.10.1
%global subdir10    swift-certificates

%global source_swift_collections 11
%global forgeurl11  https://github.com/apple/swift-collections
%global tag11       1.1.6
%global subdir11    swift-collections

%global source_swift_corelibs_blocksruntime 12
%global forgeurl12  https://github.com/swiftlang/swift-corelibs-blocksruntime
%global tag12       swift-%{version0}-RELEASE
%global subdir12    swift-corelibs-blocksruntime

%global source_swift_corelibs_foundation 13
%global forgeurl13  https://github.com/swiftlang/swift-corelibs-foundation
%global tag13       swift-%{version0}-RELEASE
%global subdir13    swift-corelibs-foundation

%global source_swift_corelibs_libdispatch 14
%global forgeurl14  https://github.com/swiftlang/swift-corelibs-libdispatch
%global tag14       swift-%{version0}-RELEASE
%global subdir14    swift-corelibs-libdispatch

%global source_swift_corelibs_xctest 15
%global forgeurl15  https://github.com/swiftlang/swift-corelibs-xctest
%global tag15       swift-%{version0}-RELEASE
%global subdir15    swift-corelibs-xctest

%global source_swift_crypto 16
%global forgeurl16  https://github.com/apple/swift-crypto
%global tag16       3.12.5
%global subdir16    swift-crypto

%global source_swift_docc 17
%global forgeurl17  https://github.com/swiftlang/swift-docc
%global tag17       swift-%{version0}-RELEASE
%global subdir17    swift-docc

%global source_swift_docc_symbolkit 18
%global forgeurl18  https://github.com/swiftlang/swift-docc-symbolkit
%global tag18       swift-%{version0}-RELEASE
%global subdir18    swift-docc-symbolkit

%global source_swift_driver 19
%global forgeurl19  https://github.com/swiftlang/swift-driver
%global tag19       swift-%{version0}-RELEASE
%global subdir19    swift-driver

%global source_swift_experimental_string_processing 20
%global forgeurl20  https://github.com/swiftlang/swift-experimental-string-processing
%global tag20       swift-%{version0}-RELEASE
%global subdir20    swift-experimental-string-processing

%global source_swift_format 21
%global forgeurl21  https://github.com/swiftlang/swift-format
%global tag21       swift-%{version0}-RELEASE
%global subdir21    swift-format

%global source_swift_foundation 22
%global forgeurl22  https://github.com/swiftlang/swift-foundation
%global tag22       swift-%{version0}-RELEASE
%global subdir22    swift-foundation

%global source_swift_foundation_icu 23
%global forgeurl23  https://github.com/swiftlang/swift-foundation-icu
%global tag23       swift-%{version0}-RELEASE
%global subdir23    swift-foundation-icu

%global source_swift_lmdb 24
%global forgeurl24  https://github.com/swiftlang/swift-lmdb
%global tag24       swift-%{version0}-RELEASE
%global subdir24    swift-lmdb

%global source_swift_markdown 25
%global forgeurl25  https://github.com/swiftlang/swift-markdown
%global tag25       swift-%{version0}-RELEASE
%global subdir25    swift-markdown

%global source_swift_nio 26
%global forgeurl26  https://github.com/apple/swift-nio
%global tag26       2.92.2
%global subdir26    swift-nio

%global source_swift_syntax 27
%global forgeurl27  https://github.com/swiftlang/swift-syntax
%global tag27       swift-%{version0}-RELEASE
%global subdir27    swift-syntax

%global source_swift_system 28
%global forgeurl28  https://github.com/apple/swift-system
%global tag28       1.5.0
%global subdir28    swift-system

%global source_swift_testing 29
%global forgeurl29  https://github.com/swiftlang/swift-testing
%global tag29       swift-%{version0}-RELEASE
%global subdir29    swift-testing

%global source_swift_toolchain_sqlite 30
%global forgeurl30  https://github.com/swiftlang/swift-toolchain-sqlite
%global tag30       1.0.9
%global subdir30    swift-toolchain-sqlite

%global source_swift_tools_protocols 31
%global forgeurl31  https://github.com/swiftlang/swift-tools-protocols
%global tag31       swift-%{version0}-RELEASE
%global subdir31    swift-tools-protocols

%global source_swift_tools_support_core 32
%global forgeurl32  https://github.com/swiftlang/swift-tools-support-core
%global tag32       swift-%{version0}-RELEASE
%global subdir32    swift-tools-support-core

%global source_swiftpm 33
%global forgeurl33  https://github.com/swiftlang/swift-package-manager
%global tag33       swift-%{version0}-RELEASE
%global subdir33    swiftpm

%global forge_source_last 33
# End forge sources

Name:           swift-lang
Release:        0.1%{?dist}
%forgemeta -a

Summary:        The Swift programming language
License:        Apache-2.0
URL:            https://www.swift.org

%{lua:
for i = 0, tonumber(rpm.expand("%{forge_source_last}")) do
  local forgesource = rpm.expand("%{?forgesource" .. i .. "}")
  if forgesource ~= "" then
    print("Source" .. i .. ":    " .. forgesource .. "\n")
  end
end
}
Source99:       swiftlang.conf
Source100:      fedora-presets.ini
Source101:      bundled-provides.inc

# Patch numbers follow the generated source indexes. A 01 suffix identifies a
# second patch for the same checkout.
Patch%{source_swift}:                     swift.patch
Patch%{source_llvm_project}:              llvm-project.patch
Patch%{source_cmark}:                     cmark.patch
Patch%{source_llbuild}:                   llbuild.patch
Patch%{source_swiftpm}:                   swiftpm.patch
Patch%{source_swift_build}:               swift-build-toolchain-layout.patch
Patch%{source_swift_driver}:              swift-driver-swiftscan-symlink.patch
Patch%{source_swiftpm}01:                 swiftpm-toolchain-layout.patch
Patch%{source_swift_foundation}:          swift-foundation.patch
Patch%{source_swift_corelibs_libdispatch}: swift-corelibs-libdispatch.patch
Patch%{source_swift_crypto}:              swift-crypto-CVE-2026-43823.patch

BuildRequires:  clang
BuildRequires:  cmake
BuildRequires:  ninja-build
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

%global swift_bundled_provides_main 1
%include %{SOURCE101}
%undefine swift_bundled_provides_main

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

%global swift_bundled_provides_runtime 1
%include %{SOURCE101}
%undefine swift_bundled_provides_runtime

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
for i = 0, tonumber(rpm.expand("%{forge_source_last}")) do
  local subdir = rpm.expand("%{?subdir" .. i .. "}")
  if subdir ~= "" then
    print(rpm.expand("mv %{archivename" .. i .. "} " .. subdir .. "\n"))
  end
end
}

%patch %{source_swift}
%patch %{source_llvm_project}
%patch %{source_cmark}
%patch %{source_llbuild}
%patch %{source_swiftpm}
%patch %{source_swift_build} -p1 -d swift-build
%patch %{source_swift_driver} -p1 -d swift-driver
%patch %{source_swiftpm}01 -p1 -d swiftpm
%patch %{source_swift_foundation}
%patch %{source_swift_corelibs_libdispatch}
%patch %{source_swift_crypto}

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
TOOLS="swift swiftc swift-build swift-run swift-package swift-test sourcekit-lsp swift-format swift-demangle swift-autolink-extract"
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
# Keep SwiftPM resources beside the relocated Swift toolchain.
mkdir -p %{buildroot}%{_libexecdir}/swift/%{version}/share/pm
cp -a %{_builddir}/usr/share/pm/. %{buildroot}%{_libexecdir}/swift/%{version}/share/pm/

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
%{_bindir}/swift-autolink-extract

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
