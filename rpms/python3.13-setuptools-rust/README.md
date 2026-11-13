# python3.13-setuptools-rust

Python 3.13 port using the recorded Fedora source and PyO3 example patch.
The spec retains import checks and the reviewed core pytest suite; integration
tests requiring packaged Rust crates remain outside this port's agreed scope.

`tests/tests.yml` is inherited optional Fedora integration metadata for testing
the shared pyproject RPM macros against Fedora cryptography. It is not the
Hummingbird Python 3.13 test entry point. Hummingbird uses the shared RPM checks
and this package's spec `%check`.
