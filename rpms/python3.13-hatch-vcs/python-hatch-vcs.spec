# Let’s try to build this as early as we can, since it’s a dependency for
# some important libraries, such as python-platformdirs.
%bcond bootstrap 0
%bcond tests %{without bootstrap}

Name:           python3.13-hatch-vcs
Version:        0.5.0
Release:        6.1%{?dist}
Summary:        Hatch plugin for versioning with your preferred VCS

# SPDX
License:        MIT
URL:            https://github.com/ofek/hatch-vcs
Source:         %{pypi_source hatch_vcs}

BuildSystem:            pyproject
BuildOption(install):   -l hatch_vcs

BuildArch:      noarch

# Static requirements must select the target before interpreter macros load.
BuildRequires:  python3.13-devel >= 3.13.18-1.3

%if %{with tests}
BuildRequires:  python3.13-pytest
BuildRequires:  git-core
%endif

%global common_description %{expand:
This provides a plugin for Hatch that uses your preferred version control
system (like Git) to determine project versions.}

%description %{common_description}


%check -a
%if %{with tests}
%pytest
%endif


%files -f %{pyproject_files}
%doc HISTORY.md
%doc README.md


%changelog
%autochangelog
