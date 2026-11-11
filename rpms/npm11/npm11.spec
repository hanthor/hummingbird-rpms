Name:           npm11
Version:        11.21.2
Release:        0.1%{?dist}
Summary:        JavaScript package manager
License:        Artistic-2.0 AND Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND BlueOak-1.0.0 AND CC-BY-3.0 AND CC0-1.0 AND ISC AND MIT
URL:            https://github.com/npm/cli
Source0:        npm-%{version}.tar.gz
Source1:        npmrc
Source2:        smoke-test.sh
Source3:        nodejs-requires.inc
BuildArch:      noarch

BuildRequires:  nodejs-packaging
BuildRequires:  nodejs26-bin
BuildRequires:  nodejs26-devel
BuildRequires:  node-gyp >= 13.1.0
BuildRequires:  python3
BuildRequires:  gcc-c++
BuildRequires:  make
Requires:       /usr/bin/node
%include %{SOURCE3}
Requires:       node-gyp >= 13.1.0
Requires:       node-gyp < 14
Conflicts:      npm12
Provides:       npm = %{version}-%{release}
Provides:       npm(npm) = %{version}

%description
npm installs and publishes JavaScript packages. This package tracks npm 11
independently of Node.js and uses the separately packaged node-gyp to build
native addons.

%prep
%setup -q -n package
# Remove node-gyp and the dependencies only it uses.
# Keep @npmcli/node-gyp, which is npm's helper for detecting native addons.
rm -rf node_modules/node-gyp node_modules/undici node_modules/env-paths \
       node_modules/exponential-backoff node_modules/tinyglobby
python3 - <<'PY'
import json
from pathlib import Path
path = Path('package.json')
package = json.loads(path.read_text())
package['bundleDependencies'].remove('node-gyp')
package['dependencies']['node-gyp'] = '^13.1.0'
path.write_text(json.dumps(package, indent=2) + '\n')
PY
# Prefix generic manual names to avoid conflicts with other packages.
for page in man/man5/folders.5 man/man5/install.5 man/man7/*.7; do
    section=${page##*.}
    name=$(basename "$page" ".$section")
    mv "$page" "${page%/*}/npm-$name.$section"
done

%build
# The release archive already contains the JavaScript and generated man pages.

%install
dest=%{buildroot}%{_prefix}/lib/node_modules/npm
install -d "$dest" %{buildroot}%{_bindir} %{buildroot}%{_mandir}
cp -a bin lib node_modules package.json index.js "$dest/"
install -m 0644 %{SOURCE1} "$dest/npmrc"
rm -f "$dest/bin/npm" "$dest/bin/npx" "$dest/bin/"*.cmd "$dest/bin/"*.ps1
chmod 0755 "$dest/bin/npm-cli.js" "$dest/bin/npx-cli.js"
ln -s ../lib/node_modules/npm/bin/npm-cli.js %{buildroot}%{_bindir}/npm
ln -s ../lib/node_modules/npm/bin/npx-cli.js %{buildroot}%{_bindir}/npx
cp -a man/man* %{buildroot}%{_mandir}/
ln -s ../../../share/man "$dest/man"

%check
# npm is staged under buildroot, while BuildRequires live in the build system.
# Installed RPMs resolve their sibling node-gyp through normal Node.js lookup.
export NODE_PATH=%{_prefix}/lib/node_modules
node %{buildroot}%{_prefix}/lib/node_modules/npm/bin/npm-cli.js --version | grep -Fx %{version}
node %{buildroot}%{_prefix}/lib/node_modules/npm/bin/npx-cli.js --version | grep -Fx %{version}
bash %{SOURCE2} %{buildroot}%{_prefix}/lib/node_modules/npm

%files
%license LICENSE
%doc README.md
%{_bindir}/npm
%{_bindir}/npx
%{_prefix}/lib/node_modules/npm/
%{_mandir}/man1/npm*.1*
%{_mandir}/man1/npx.1*
%{_mandir}/man5/*.5*
%{_mandir}/man7/*.7*

%changelog
%autochangelog
