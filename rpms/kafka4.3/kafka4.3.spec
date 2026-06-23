%global scala_version 2.13

Name:           kafka4.3
Version:        4.3.0
Release:        1%{?dist}
Summary:        Apache Kafka distributed event streaming platform

Provides:       kafka = %{version}-%{release}
# BEGIN bundled provides
Provides:       bundled(activation) = 1.1.1
Provides:       bundled(aopalliance-repackaged) = 3.0.6
Provides:       bundled(argparse4j) = 0.7.0
Provides:       bundled(caffeine) = 3.2.0
Provides:       bundled(classgraph) = 4.8.179
Provides:       bundled(commons-beanutils) = 1.11.0
Provides:       bundled(commons-collections) = 3.2.2
Provides:       bundled(commons-digester) = 2.1
Provides:       bundled(commons-logging) = 1.3.5
Provides:       bundled(commons-validator) = 1.10.1
Provides:       bundled(hash4j) = 0.22.0
Provides:       bundled(HdrHistogram) = 2.2.2
Provides:       bundled(hk2-api) = 3.0.6
Provides:       bundled(hk2-locator) = 3.0.6
Provides:       bundled(hk2-utils) = 3.0.6
Provides:       bundled(jackson-annotations) = 2.21
Provides:       bundled(jackson-core) = 2.21.2
Provides:       bundled(jackson-databind) = 2.21.2
Provides:       bundled(jackson-dataformat-csv) = 2.21.2
Provides:       bundled(jackson-dataformat-yaml) = 2.21.2
Provides:       bundled(jackson-datatype-jdk8) = 2.21.2
Provides:       bundled(jackson-jakarta-rs-base) = 2.21.2
Provides:       bundled(jackson-jakarta-rs-json-provider) = 2.21.2
Provides:       bundled(jackson-module-blackbird) = 2.21.2
Provides:       bundled(jackson-module-jakarta-xmlbind-annotations) = 2.21.2
Provides:       bundled(jakarta.activation) = 2.0.1
Provides:       bundled(jakarta.activation-api) = 2.1.0
Provides:       bundled(jakarta.annotation-api) = 2.1.1
Provides:       bundled(jakarta.inject-api) = 2.0.1
Provides:       bundled(jakarta.servlet-api) = 6.0.0
Provides:       bundled(jakarta.validation-api) = 3.0.2
Provides:       bundled(jakarta.ws.rs-api) = 3.1.0
Provides:       bundled(jakarta.xml.bind-api) = 3.0.1
Provides:       bundled(javassist) = 3.30.2-GA
Provides:       bundled(javax.activation-api) = 1.2.0
Provides:       bundled(javax.annotation-api) = 1.3.2
Provides:       bundled(jaxb-api) = 2.3.1
Provides:       bundled(jersey-client) = 3.1.10
Provides:       bundled(jersey-common) = 3.1.10
Provides:       bundled(jersey-container-servlet) = 3.1.10
Provides:       bundled(jersey-container-servlet-core) = 3.1.10
Provides:       bundled(jersey-hk2) = 3.1.10
Provides:       bundled(jersey-server) = 3.1.10
Provides:       bundled(jetty-alpn-client) = 12.0.34
Provides:       bundled(jetty-client) = 12.0.34
Provides:       bundled(jetty-ee10-servlet) = 12.0.34
Provides:       bundled(jetty-ee10-servlets) = 12.0.34
Provides:       bundled(jetty-http) = 12.0.34
Provides:       bundled(jetty-io) = 12.0.34
Provides:       bundled(jetty-security) = 12.0.34
Provides:       bundled(jetty-server) = 12.0.34
Provides:       bundled(jetty-session) = 12.0.34
Provides:       bundled(jetty-util) = 12.0.34
Provides:       bundled(jline) = 3.30.4
Provides:       bundled(jopt-simple) = 5.0.4
Provides:       bundled(jose4j) = 0.9.6
Provides:       bundled(jspecify) = 1.0.0
Provides:       bundled(log4j-1.2-api) = 2.25.4
Provides:       bundled(log4j-api) = 2.25.4
Provides:       bundled(log4j-core) = 2.25.4
Provides:       bundled(log4j-slf4j-impl) = 2.25.4
Provides:       bundled(lz4-java) = 1.10.2
Provides:       bundled(maven-artifact) = 3.9.15
Provides:       bundled(metrics-core) = 2.2.0
Provides:       bundled(opentelemetry-proto) = 1.3.2-alpha
Provides:       bundled(osgi-resource-locator) = 1.0.3
Provides:       bundled(pcollections) = 4.0.2
Provides:       bundled(plexus-utils) = 3.6.1
Provides:       bundled(protobuf-java) = 3.25.5
Provides:       bundled(re2j) = 1.8
Provides:       bundled(rocksdbjni) = 10.1.3
Provides:       bundled(scala-library) = 2.13.18
Provides:       bundled(scala-logging_2.13) = 3.9.6
Provides:       bundled(scala-reflect) = 2.13.18
Provides:       bundled(slf4j-api) = 1.7.36
Provides:       bundled(snakeyaml) = 2.5
Provides:       bundled(snappy-java) = 1.1.10.7
Provides:       bundled(swagger-annotations) = 2.2.39
Provides:       bundled(zstd-jni) = 1.5.6-10
# END bundled provides


License:        Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND CDDL-1.1 AND EPL-2.0 AND GPL-2.0-only WITH Classpath-exception-2.0 AND MIT
URL:            https://kafka.apache.org/
Source0:        https://downloads.apache.org/kafka/%{version}/kafka-%{version}-src.tgz
Source1:        kafka-%{version}-gradle-home.tar.xz

BuildArch:      noarch
ExclusiveArch:  %{java_arches} noarch

BuildRequires:  java-25-openjdk-devel
BuildRequires:  javapackages-local-openjdk25

Requires:       java-25-openjdk-headless


%description
Apache Kafka is a distributed event streaming platform used for building
real-time data pipelines and streaming applications. It provides a unified,
high-throughput, low-latency platform for handling real-time data feeds.

Kafka 4.x uses the KRaft consensus protocol and has no ZooKeeper dependency.


%prep
%setup -q -n kafka-%{version}-src

mkdir -p .gradle-home
tar -xf %{SOURCE1} -C .gradle-home --strip-components=1


%build
export JAVA_HOME=%{_jvmdir}/java-25-openjdk
export GRADLE_USER_HOME=$(pwd)/.gradle-home

# Use Gradle directly from vendored distribution instead of gradlew,
# which requires gradle-wrapper.jar (excluded from Apache source tarballs)
gradle_bin=$(find .gradle-home/wrapper/dists -name gradle -path '*/bin/gradle' | head -1)
"${gradle_bin}" --offline --no-daemon clean releaseTarGz


%install
tar -xzf core/build/distributions/kafka_%{scala_version}-%{version}.tgz

mkdir -p %{buildroot}/usr/lib/kafka4.3
cp -a kafka_%{scala_version}-%{version}/bin     %{buildroot}/usr/lib/kafka4.3/
cp -a kafka_%{scala_version}-%{version}/libs    %{buildroot}/usr/lib/kafka4.3/
cp -a kafka_%{scala_version}-%{version}/config  %{buildroot}/usr/lib/kafka4.3/
cp -a kafka_%{scala_version}-%{version}/licenses %{buildroot}/usr/lib/kafka4.3/

find %{buildroot}/usr/lib/kafka4.3/bin -name '*.bat' -delete
rm -rf %{buildroot}/usr/lib/kafka4.3/bin/windows


%files
%license LICENSE NOTICE
%doc README.md
/usr/lib/kafka4.3/


%changelog
%autochangelog
