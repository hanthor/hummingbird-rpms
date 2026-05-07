Name:           rpm-lockfile-prototype
Version:        0.21.0
Release:        1%{?dist}
Summary:        Generate lockfiles for RPM package dependencies

License:        GPL-3.0-or-later
URL:            https://github.com/konflux-ci/rpm-lockfile-prototype
Source:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-dnf

%global _description %{expand:
Tool for generating lockfiles for RPM package dependency resolution,
enabling builds without network access. Resolves RPM transactions and
outputs lockfile formats compatible with Hermeto.}

%description %{_description}

%package -n python3-%{name}
Summary:        %{summary}
Requires:       python3-dnf
Recommends:     skopeo

%description -n python3-%{name} %{_description}

%prep
%autosetup -p1

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files rpm_lockfile

%check
%pyproject_check_import

%files -n python3-%{name} -f %{pyproject_files}
%doc README.md
%{_bindir}/rpm-lockfile-prototype
%{_bindir}/caching-rpm-lockfile-prototype

%changelog
%autochangelog
