"""Run-batch0 marker registration (Tester lane).

New additive file per testing-strategy.md (sha256 e58068c2...) T0.2:
tests/conftest.py is read-only and defines no markers, and a
``tests/conftest_batch0.py`` would not be auto-loaded by pytest, so the
``red_now`` marker is registered here, in a new rootdir conftest, instead.

Marker semantics (T0.2):
- ``@pytest.mark.red_now``  -- asserts the FIXED behavior; EXPECTED TO FAIL on
  the unfixed base SHA 8c5cc925648c. That failure is the point: it proves the
  suite catches the defect. The authoritative red-now/green-now runs are
  executed by the Validator, not by this lane.
- no marker -- a green-now guard: must pass identically before and after the
  fix.
"""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "red_now: asserts FIXED behavior; expected to FAIL on unfixed base "
        "SHA 8c5cc925648c (run batch0, testing-strategy T0.2)",
    )
