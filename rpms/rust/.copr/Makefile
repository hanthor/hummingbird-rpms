# See "make srpm" within https://docs.copr.fedorainfracloud.org/user_documentation.html#scm

outdir?=$(shell pwd)
spec?=rust.spec
required_packages=rpmdevtools rpmautospec

.PHONY: srpm
srpm:
	rpm -q $(required_packages) || dnf install -y $(required_packages) --setopt=install_weak_deps=False

	spectool -g $(spec) --directory $(shell pwd)

	rpmautospec process-distgit $(spec) $(spec)

	rpmbuild \
		--define "_sourcedir $(shell pwd)" \
		--define "_srcrpmdir $(outdir)" \
		-bs $(spec)
