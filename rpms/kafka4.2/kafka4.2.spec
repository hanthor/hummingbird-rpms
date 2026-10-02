%global scala_version 2.13

Name:           kafka4.2
Version:        4.2.2
Release:        0.1%{?dist}
Summary:        Apache Kafka distributed event streaming platform

Provides:       kafka = %{version}-%{release}

License:        Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND CDDL-1.1 AND EPL-2.0 AND GPL-2.0-only WITH Classpath-exception-2.0 AND MIT
URL:            https://kafka.apache.org/
Source0:        https://github.com/apache/kafka/archive/refs/tags/%{version}/kafka-%{version}.tar.gz
Source1:        kafka-%{version}-gradle-vendor.tar.gz
Source2:        bundled-provides.inc

%include %{SOURCE2}

BuildArch:      noarch

BuildRequires:  java-25-openjdk-devel

Requires:       java-25-openjdk-headless


%description
Apache Kafka is a distributed event streaming platform used for building
real-time data pipelines and streaming applications. It provides a unified,
high-throughput, low-latency platform for handling real-time data feeds.

Kafka 4.x uses the KRaft consensus protocol and has no ZooKeeper dependency.


%prep
%setup -q -n kafka-%{version}

tar -xf %{SOURCE1}


%build
export JAVA_HOME=/usr/lib/jvm/java-25-openjdk
export GRADLE_USER_HOME=$(pwd)/vendor

# Kafka's wrapper downloads its jar even with --offline. Use the distribution
# already populated by gorget's native Gradle vendor backend.
gradle_bin=$(find "$GRADLE_USER_HOME/wrapper/dists" -path '*/bin/gradle' -type f)
test -x "$gradle_bin"
"$gradle_bin" --offline --no-daemon --max-workers=4 clean releaseTarGz


%install
tar -xzf core/build/distributions/kafka_%{scala_version}-%{version}.tgz

mkdir -p %{buildroot}/usr/lib/kafka4.2
cp -a kafka_%{scala_version}-%{version}/bin     %{buildroot}/usr/lib/kafka4.2/
cp -a kafka_%{scala_version}-%{version}/libs    %{buildroot}/usr/lib/kafka4.2/
cp -a kafka_%{scala_version}-%{version}/config  %{buildroot}/usr/lib/kafka4.2/
cp -a kafka_%{scala_version}-%{version}/licenses %{buildroot}/usr/lib/kafka4.2/

find %{buildroot}/usr/lib/kafka4.2/bin -name '*.bat' -delete
rm -rf %{buildroot}/usr/lib/kafka4.2/bin/windows


%files
%license LICENSE NOTICE
%doc README.md
/usr/lib/kafka4.2/


%changelog
%autochangelog
