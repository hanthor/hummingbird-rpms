%bcond check 1

%global goipath github.com/prometheus/blackbox_exporter
%global forgeurl https://github.com/prometheus/blackbox_exporter
Version: 0.29.1

%gometa -L -f

Name: prometheus-blackbox-exporter
%global forgesetupargs -n %{name}-%{version}
%global gosourcedir %{_builddir}/%{name}-%{version}
Release: 0.1%{?dist}
Summary: Prometheus exporter for probing network endpoints
License: Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND MIT
URL: %{gourl}
Source0: %{name}-%{version}.tar.gz
Source1: %{name}-%{version}-vendor.tar.bz2
Source2: go-vendor-tools.toml
BuildRequires: go-vendor-tools

%description
The Prometheus blackbox exporter probes endpoints over HTTP, HTTPS, DNS,
TCP, ICMP, and gRPC and exposes the results as Prometheus metrics.

%prep
%goprep -p1
tar -xf %{S:1}

%generate_buildrequires
%go_vendor_license_buildrequires -c %{S:2}

%build
%global gomodulesmode GO111MODULE=on
export GOFLAGS="-mod=vendor -buildvcs=false"
export GO_LDFLAGS="-X github.com/prometheus/common/version.Version=%{version}"
%gobuild -o %{gobuilddir}/bin/blackbox_exporter %{goipath}

%install
%go_vendor_license_install -c %{S:2}
install -D -p -m 0755 %{gobuilddir}/bin/blackbox_exporter %{buildroot}%{_bindir}/blackbox_exporter
install -D -p -m 0644 blackbox.yml %{buildroot}%{_sysconfdir}/blackbox_exporter/config.yml

%check
%go_vendor_license_check -c %{S:2}
%if %{with check}
# Skip upstream tests that require public DNS and HTTPS access.
%gotest -short ./...
%endif

%files -f %{go_vendor_license_filelist}
%doc README.md CHANGELOG.md CONFIGURATION.md example.yml
%{_bindir}/blackbox_exporter
%dir %{_sysconfdir}/blackbox_exporter
%config(noreplace) %{_sysconfdir}/blackbox_exporter/config.yml

%changelog
%autochangelog
