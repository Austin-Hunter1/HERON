# NeQuick-G validation tables

These files are test data for `test_nequick_jrc_matches_validation_tables`
in `tests/test_openloop.py` (plan test T-10, concern C-7).

Source: the `nequick` 1.0.0 source release on PyPI
(https://pypi.org/project/nequick/, MIT licence, repository
https://github.com/mgfernan/NeQuickJRC), folder `test/benchmark/`, files
`benchmarkHigh`, `benchmarkMid` and `benchmarkLow`. They are the validation
data of the Galileo document "Ionospheric Correction Algorithm for Galileo
Single Frequency Users", issue 1.2, September 2016, for high, medium and low
solar activity.

Format:

- Line 1: the broadcast coefficients ai0, ai1, ai2.
- Each other line: month, UT (hours), receiver longitude (deg), receiver
  latitude (deg), receiver height (m), satellite longitude (deg), satellite
  latitude (deg), satellite height (m), reference STEC (TECU).

Do not edit these files.
