%global soversion 16

Name:           libxml2
Version:        2.15.4
# Placed after Version: so %{version} is defined when this shell-eval macro
# runs -- a %global before Version: silently captures the un-truncated
# version instead of major.minor (see metadata/libxml2.source-pipeline.yaml
# and HUM-6191 for how this broke Source0).
%global majorminor %(echo %{version} | cut -d. -f1-2)
Release:        1%{?dist}
Summary:        Library providing XML and HTML support

# list.c, dict.c and few others use ISC-Veillard
# the conformance test suite data in Source1 is covered by W3C
License:        MIT AND ISC-Veillard AND W3C
URL:            https://gitlab.gnome.org/GNOME/libxml2/-/wikis/home
Source0:        https://download.gnome.org/sources/%{name}/%{majorminor}/%{name}-%{version}.tar.xz
# https://www.w3.org/XML/Test/xmlconf-20080827.html
Source1:        https://www.w3.org/XML/Test/xmlts20080827.tar.gz
Patch0:         libxml2-multilib.patch

BuildRequires:  cmake-rpm-macros
BuildRequires:  docbook-style-xsl
BuildRequires:  doxygen
BuildRequires:  gcc
BuildRequires:  git
BuildRequires:  meson
BuildRequires:  pkgconfig(liblzma)
BuildRequires:  pkgconfig(readline)
BuildRequires:  pkgconfig(zlib)
BuildRequires:  /usr/bin/xsltproc

%description
This library allows to manipulate XML files. It includes support
to read, modify and write XML and HTML files. There is DTDs support
this includes parsing and validation even with complex DtDs, either
at parse time or later once the document has been modified. The output
can be a simple SAX stream or and in-memory DOM like representations.
In this case one can use the built-in XPath and XPointer implementation
to select sub nodes or ranges. A flexible Input/Output mechanism is
available, with existing HTTP and FTP modules and combined to an
URI library.

%package -n libxml2-%{soversion}
Summary:        XML parsing shared library
# Do not Obsolete old libxml2 - allow co-installation during the soname
# transition so that buildroot packages linked against .so.2 keep working
# while new builds link against .so.16.

%description -n libxml2-%{soversion}
Shared library for libxml2 with soname version %{soversion}.

%package devel
Summary:        Libraries, includes, etc. to develop XML and HTML applications
Requires:       libxml2-%{soversion}%{?_isa} = %{version}-%{release}
Requires:       zlib-devel%{?_isa}
Requires:       xz-devel%{?_isa}

%description devel
Libraries, include files, etc you can use to develop XML applications.
This library allows to manipulate XML files. It includes support
to read, modify and write XML and HTML files. There is DTDs support
this includes parsing and validation even with complex DtDs, either
at parse time or later once the document has been modified. The output
can be a simple SAX stream or and in-memory DOM like representations.
In this case one can use the built-in XPath and XPointer implementation
to select sub nodes or ranges. A flexible Input/Output mechanism is
available, with existing HTTP and FTP modules and combined to an
URI library.

%package static
Summary:        Static library for libxml2

%description static
Static library for libxml2 provided for specific uses or shaving a few
microseconds when parsing, do not link to them for generic purpose packages.

%package -n python3-%{name}
Summary:        Python 3 bindings for the libxml2 library
BuildRequires:  python3-devel
Requires:       libxml2-%{soversion}%{?_isa} = %{version}-%{release}
Obsoletes:      %{name}-python3 < %{version}-%{release}
Provides:       %{name}-python3 = %{version}-%{release}
# Will be removed upstream in 2.16.
Provides:       deprecated()

%description -n python3-%{name}
The libxml2-python3 package contains a Python 3 module that permits
applications written in the Python programming language, version 3, to use the
interface supplied by the libxml2 library to manipulate XML files.

This library allows to manipulate XML files. It includes support
to read, modify and write XML and HTML files. There is DTDs support
this includes parsing and validation even with complex DTDs, either
at parse time or later once the document has been modified.

%prep
%autosetup -p1
find doc -type f -executable -print -exec chmod 0644 {} ';'

%conf
%meson -Ddefault_library=both

%build
%meson_build

%install
%meson_install

%check
# Tests require the XML conformance suite.
tar -xzvf %{SOURCE1}
%meson_test
rm -rf xmlconf

%files
%license Copyright
%doc NEWS README.md
%{_bindir}/xmlcatalog
%{_bindir}/xmllint
%{_mandir}/man1/xmlcatalog.1*
%{_mandir}/man1/xmllint.1*

%files -n libxml2-%{soversion}
%license Copyright
%{_libdir}/libxml2.so.%{soversion}{,.*}

%files devel
%doc %{_docdir}/libxml2/*.html
%doc %{_docdir}/libxml2/html/
%doc example
%{_includedir}/libxml2/
%{_libdir}/libxml2.so
%{_libdir}/pkgconfig/libxml-2.0.pc
%{_libdir}/cmake/libxml2/
%{_bindir}/xml2-config
%{_mandir}/man1/xml2-config.1*

%files static
%license Copyright
%{_libdir}/libxml2.a

%files -n python3-%{name}
%{python3_sitearch}/libxml2mod.*.so
%{python3_sitelib}/libxml2.py
%{python3_sitelib}/__pycache__/libxml2.*
%{python3_sitelib}/drv_libxml2.py
%{python3_sitelib}/__pycache__/drv_libxml2.*

%changelog
%autochangelog
