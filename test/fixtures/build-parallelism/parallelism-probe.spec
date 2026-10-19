Name:           parallelism-probe
Version:        1
Release:        1
Summary:        Measure the parallelism of an actual RPM build
License:        MIT
Source0:        probe.py
Source1:        Makefile
BuildRequires:  make
BuildRequires:  python3
BuildArch:      noarch

%description
Diagnostic fixture; its report intentionally records build execution details.

%{?probe_cap:%constrain_build -c %{probe_cap}}

%prep
%setup -q -c -T
cp %{SOURCE0} probe.py
cp %{SOURCE1} Makefile

%build
%make_build
python3 probe.py report %{probe_expected} %{_smp_build_ncpus} "$RPM_BUILD_NCPUS" > report.json
cat report.json

%install
mkdir -p %{buildroot}%{_datadir}/parallelism-probe
cp report.json %{buildroot}%{_datadir}/parallelism-probe/

%files
%{_datadir}/parallelism-probe/report.json

%changelog
* Mon Oct 19 2026 Hummingbird <hummingbird@redhat.com> - 1-1
- Build parallelism diagnostic fixture
