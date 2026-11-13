# python3.13-idna

Python 3.13 port of the recorded Fedora source with the complete reviewed
pytest suite, including Hypothesis property tests.

The inherited optional `plans.fmf` smoke test selects Python 3.13 and its idna
package. Hummingbird uses the shared RPM checks and the spec `%check`; the
inherited plan is not an additional CI prerequisite.
