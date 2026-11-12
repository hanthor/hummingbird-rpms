Name:           python3.13-hatchling
Version:        1.32.4
Release:        1.1%{?dist}
Summary:        The build backend used by Hatch

# SPDX
License:        MIT
URL:            https://pypi.org/project/hatchling
Source0:        %{pypi_source hatchling}
# Written for Fedora in groff_man(7) format based on --help output
Source100:      hatchling.1
Source200:      hatchling-build.1
Source300:      hatchling-dep.1
Source310:      hatchling-dep-synced.1
Source400:      hatchling-metadata.1
Source500:      hatchling-version.1

BuildSystem:    pyproject
BuildOption(install): --assert-license hatchling
# We cannot run the “downstream integration tests” included with the PyPI sdist
# in an offline build. The primary tests are Hatch’s “backend” tests.

BuildArch:      noarch

# Anchor dependency resolution to the macro-owning Hummingbird interpreter.
BuildRequires:  python3.13-devel >= 3.13.18-1.3

%global common_description %{expand:
This is the extensible, standards compliant build backend used by Hatch.}

%description %{common_description}


%install -a
install -D --preserve-timestamps --mode=0644 \
    --target='%{buildroot}%{_mandir}/man1' \
    '%{SOURCE100}' \
    '%{SOURCE200}' \
    '%{SOURCE300}' '%{SOURCE310}' \
    '%{SOURCE400}' \
    '%{SOURCE500}'


%files -f %{pyproject_files}
%doc README.md

%{_bindir}/hatchling
%{_mandir}/man1/hatchling.1*
%{_mandir}/man1/hatchling-*.1*


%changelog
%autochangelog
