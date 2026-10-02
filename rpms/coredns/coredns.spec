%global goipath github.com/coredns/coredns

Name:           coredns
Version:        1.14.7
Release:        0.1%{?dist}
Summary:        DNS server with a plugin architecture
License:        Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND CC0-1.0 AND ISC AND MIT AND MPL-2.0
URL:            https://github.com/coredns/coredns
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz
Source1:        %{name}-%{version}-vendor.tar.bz2
Source2:        go-vendor-tools.toml

ExclusiveArch:  %{golang_arches_future}
BuildRequires:  go-vendor-tools
BuildRequires:  go-rpm-macros

%description
CoreDNS is a DNS server that chains plugins. It supports service discovery,
DNS forwarding, caching, and serving authoritative zones.

%prep
%autosetup -p1 -C
tar -xf %{S:1}

%generate_buildrequires
%go_vendor_license_buildrequires -c %{S:2}

%build
%global gomodulesmode GO111MODULE=on
%gobuild -o coredns .

%install
%go_vendor_license_install -c %{S:2}
install -Dpm 0755 coredns %{buildroot}%{_bindir}/coredns

%check
%go_vendor_license_check -c %{S:2}
./coredns -version | grep -F 'CoreDNS-%{version}'
./coredns -plugins | grep -Fx 'kubernetes'

%files -f %{go_vendor_license_filelist}
%license LICENSE
%doc README.md
%{_bindir}/coredns

%changelog
%autochangelog
