Name:           node-gyp
Version:        13.1.0
Release:        0.1%{?dist}
Summary:        Node.js native addon build tool
License:        MIT AND BSD-3-Clause AND Apache-2.0 AND ISC AND BlueOak-1.0.0
URL:            https://github.com/nodejs/node-gyp
Source0:        https://registry.npmjs.org/node-gyp/-/node-gyp-%{version}.tgz#/node-gyp-%{version}.tar.gz
Source1:        node-gyp-%{version}-vendor.tar.bz2
Source2:        bundled-npm-provides.inc
Source3:        smoke-test.sh
BuildArch:      noarch

BuildRequires:  nodejs26-bin
BuildRequires:  nodejs26-devel
BuildRequires:  python3-devel
BuildRequires:  gcc-c++
BuildRequires:  make
Requires:       /usr/bin/node
Requires:       ((nodejs(engine) >= 22.22.2 with nodejs(engine) < 23) or (nodejs(engine) >= 24.15.0 with nodejs(engine) < 25) or nodejs(engine) >= 26.0.0)
Recommends:     python3 >= 3.9
Recommends:     gcc-c++
Recommends:     make
Provides:       npm(node-gyp) = %{version}
Provides:       bundled(gyp-next) = 0.22.3
Provides:       bundled(python3dist(packaging)) = 23.3~dev0
%include %{SOURCE2}

%description
node-gyp compiles native addon modules for Node.js. It includes the JavaScript
dependencies and GYP sources needed to generate platform build files.

%prep
%setup -q -n package
tar -xf %{SOURCE1}
# node-gyp invokes GYP through the Python interpreter it discovers. These
# files need no executable bit or automatic hard interpreter dependency.
chmod -x gyp/gyp
find gyp -type f -name '*.py' -exec chmod -x {} +

%build
# JavaScript and Python sources run without compilation. Dependencies are
# vendored in Source1; the RPM build does not contact the npm registry.

%install
dest=%{buildroot}%{_prefix}/lib/node_modules/node-gyp
install -d "$dest" %{buildroot}%{_bindir}
cp -a bin lib gyp src node_modules addon.gypi package.json "$dest/"
chmod 0755 "$dest/bin/node-gyp.js"
ln -s ../lib/node_modules/node-gyp/bin/node-gyp.js %{buildroot}%{_bindir}/node-gyp

%check
node %{buildroot}%{_prefix}/lib/node_modules/node-gyp/bin/node-gyp.js --version | grep -Fx v%{version}
# Use the installed payload, including its vendored Python and JS modules.
bash %{SOURCE3} %{buildroot}%{_prefix}/lib/node_modules/node-gyp/bin/node-gyp.js /usr

%files
%license LICENSE
%doc README.md CHANGELOG.md
%{_bindir}/node-gyp
%{_prefix}/lib/node_modules/node-gyp/

%changelog
%autochangelog
