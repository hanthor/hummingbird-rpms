# python3.13-packaging

Normal replacement for the bootstrap, retaining the recorded pytest profile
and its narrow pretend/tomli-w exclusions. Upstream bootstrap controls remain
available; the local release-suffix override is no longer needed.

The inherited optional FMF plan and smoke script select Python 3.13 for this
package. The plan's separate shared Fedora RPM-tooling integration tests use
default Python intentionally. Hummingbird uses shared RPM checks and the spec
`%check`; the inherited plan is not an additional CI prerequisite.
