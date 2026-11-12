# when bootstrapping, we cannot yet use sphinx and pytest
# on RHEL, we don't need to build the documentation
%bcond bootstrap 1
%bcond docs 0
%bcond tests %{without bootstrap}
# Contrast validation needs a separate wcag-contrast-ratio port.
%bcond contrast 0
# Default Sphinx owns the generic command; this variant supplies its library.
%bcond cli 0

# Track this temporary stage in the PR, not in the RPM release suffix.
%global __bootstrap %{nil}

Name:           python3.13-pygments
Version:        2.21.0
Release:        1.1%{?dist}
Summary:        Syntax highlighting engine written in Python

License:        BSD-2-Clause
URL:            https://pygments.org/
Source0:        %{pypi_source pygments}

BuildArch:      noarch

# Anchor dependency resolution to the macro-owning Hummingbird interpreter.
BuildRequires:  python3.13-devel >= 3.13.18-1.3
BuildRequires:  pyproject-rpm-macros
%if %{with tests}
BuildRequires:  python%{python3_pkgversion}-pytest
%if %{with contrast}
# this is only used in tests.contrast.test_contrasts
# to avoid pulling this package into RHEL, the test is ignored in %%check
BuildRequires:  python%{python3_pkgversion}-wcag-contrast-ratio
%endif
%endif
%if %{with docs}
BuildRequires:  make
BuildRequires:  python%{python3_pkgversion}-sphinx
# the sphinx config imports tests.contrast.test_contrasts:
BuildRequires:  python%{python3_pkgversion}-wcag-contrast-ratio
%endif


%global _description %{expand:
Pygments is a generic syntax highlighter suitable for use in code hosting,
forums, wikis or other applications that need to prettify source code.

Highlights are:

 * a wide range of over 500 languages and other text formats is supported
 * special attention is paid to details that increase highlighting quality
 * support for new languages and formats are added easily;
   most languages use a simple regex-based lexing mechanism
 * a number of output formats is available, among them HTML, RTF, LaTeX
   and ANSI sequences
 * it is usable as a command-line tool and as a library}

%if %{with cli}
Provides:       pygmentize = %{?epoch:%{epoch}:}%{version}-%{release}
%endif

%description %_description


%prep
%autosetup -p1 -n pygments-%{version}


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files pygments

%if %{with cli}
install doc/pygmentize.1 -Dt %{buildroot}%{_mandir}/man1/
%else
rm %{buildroot}%{_bindir}/pygmentize
%endif

%if %{with docs}
%make_build -C doc html
rm doc/_build/html/.buildinfo
rm -rf doc/_build/html/_sources
chmod -x %{buildroot}%{_mandir}/man1/*.1
%endif


%check
%pyproject_check_import %{!?with_docs:-e pygments.sphinxext}
%if %{with tests}
%pytest %{!?with_contrast:--ignore tests/contrast/test_contrasts.py}
%endif


%files -f %{pyproject_files}
%doc AUTHORS CHANGES
%{?with_docs:%doc doc/_build/html}
%license LICENSE
%if %{with cli}
%{_bindir}/pygmentize
%lang(en) %{_mandir}/man1/pygmentize.1*
%endif


%changelog
%autochangelog
