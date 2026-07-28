%global upstream_version 17.0.19+10
%global upstream_version_url 17.0.19%%2B10
%global archive_version 17.0.19_10
%global jvm_dir %{_prefix}/lib/jvm/%{name}
%global jre_dir %{_prefix}/lib/jvm/temurin-17-jre

Name:           temurin-17-jdk
Version:        17.0.19.0.0.10
Release:        1%{?dist}
Summary:        Eclipse Temurin 17 JDK
License:        GPL-2.0-only WITH Classpath-exception-2.0
URL:            https://adoptium.net/temurin/
Vendor:         Eclipse Adoptium

Source0:        https://github.com/adoptium/temurin17-binaries/releases/download/jdk-%{upstream_version_url}/OpenJDK17U-jdk_x64_linux_hotspot_%{archive_version}.tar.gz
Source1:        https://github.com/adoptium/temurin17-binaries/releases/download/jdk-%{upstream_version_url}/OpenJDK17U-jdk_aarch64_linux_hotspot_%{archive_version}.tar.gz
Source2:        https://github.com/adoptium/temurin17-binaries/releases/download/jdk-%{upstream_version_url}/OpenJDK17U-jre_x64_linux_hotspot_%{archive_version}.tar.gz
Source3:        https://github.com/adoptium/temurin17-binaries/releases/download/jdk-%{upstream_version_url}/OpenJDK17U-jre_aarch64_linux_hotspot_%{archive_version}.tar.gz

ExclusiveArch:  x86_64 aarch64
AutoReqProv:    no
BuildRequires:  tar
Requires:       alsa-lib%{?_isa}
Requires:       ca-certificates
Requires:       dejavu-sans-fonts
Requires:       fontconfig%{?_isa}
Requires:       glibc%{?_isa}
Requires:       libX11%{?_isa}
Requires:       libXext%{?_isa}
Requires:       libXi%{?_isa}
Requires:       libXrender%{?_isa}
Requires:       libXtst%{?_isa}
Requires:       zlib%{?_isa}

%global debug_package %{nil}
%global __brp_strip %{nil}

%description
Eclipse Temurin 17 is an OpenJDK-based Java development kit built by the
Eclipse Adoptium project. This package installs the complete, prebuilt JDK
under %{jvm_dir}. It does not register system alternatives.

%package -n temurin-17-jre
Summary:        Eclipse Temurin 17 JRE
AutoReqProv:    no
Requires:       alsa-lib%{?_isa}
Requires:       ca-certificates
Requires:       dejavu-sans-fonts
Requires:       fontconfig%{?_isa}
Requires:       glibc%{?_isa}
Requires:       libX11%{?_isa}
Requires:       libXext%{?_isa}
Requires:       libXi%{?_isa}
Requires:       libXrender%{?_isa}
Requires:       libXtst%{?_isa}
Requires:       zlib%{?_isa}

%description -n temurin-17-jre
Eclipse Temurin 17 Java runtime environment. This package installs Adoptium's
prebuilt JRE under %{jre_dir}. It does not register system alternatives.

%prep

%build

%install
install -d %{buildroot}%{jvm_dir} %{buildroot}%{jre_dir}
%ifarch x86_64
tar --strip-components=1 -C %{buildroot}%{jvm_dir} -xf %{SOURCE0}
tar --strip-components=1 -C %{buildroot}%{jre_dir} -xf %{SOURCE2}
%endif
%ifarch aarch64
tar --strip-components=1 -C %{buildroot}%{jvm_dir} -xf %{SOURCE1}
tar --strip-components=1 -C %{buildroot}%{jre_dir} -xf %{SOURCE3}
%endif
rm -f %{buildroot}%{jvm_dir}/lib/security/cacerts
ln -s /etc/pki/java/cacerts %{buildroot}%{jvm_dir}/lib/security/cacerts
rm -f %{buildroot}%{jre_dir}/lib/security/cacerts
ln -s /etc/pki/java/cacerts %{buildroot}%{jre_dir}/lib/security/cacerts

%check
%{buildroot}%{jvm_dir}/bin/java -version
%{buildroot}%{jvm_dir}/bin/javac -version
%{buildroot}%{jre_dir}/bin/java -version

%files
%{jvm_dir}

%files -n temurin-17-jre
%{jre_dir}

%changelog
%autochangelog
