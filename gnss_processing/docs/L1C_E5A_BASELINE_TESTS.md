# Baseline test result before the GPS L1C and Galileo E5a work

Date: 2026-10-20. Branch: `feat/bds-b2a-b1c-ttjeh0`, head 79168b9 (the plan commit; no code
change since the BeiDou port).
Machine: cloud container, Linux, Python 3.13, `uv run --no-sync pytest -q -p no:cacheprovider
--continue-on-collection-errors -rfEs` in `gnss_processing/`.

Result: **44 failed, 1152 passed, 3 collection errors**, 786 s.

This is baseline v1 of `L1C_E5A_PORT_PLAN.md` (section 6.3). It is identical to the baseline
of the end of the BeiDou port (Phase 9). Baseline v2 is saved later, in Phase L2, after the
L1C module exists.

Cause (found in the BeiDou port, still true): the pinned `gnss-tools` commit b5409db has no
`CARRIER_FREQ` and no L1CD or L1CO generators in `gnss_tools.signals.gps_l1c`. So `GpsL1C` is
`None` in `utils/signal_interfaces.py`, and every test that needs L1C fails or cannot be
collected. The golden files and the ICD check sums for L1C are in the repository already.

| File | Failures (and collection errors) |
|------|------|
| tests/test_acquisition.py | 1 |
| tests/test_coherent_integration.py | 4 |
| tests/test_double_estimator.py | 1 |
| tests/test_epoch_symbol_alignment.py | 1 |
| tests/test_l1c.py | 22 |
| tests/test_l1c_codes.py | 1 |
| tests/test_tracking_regression.py | 17 |

Full list of test names:

- ERROR tests/test_acquisition.py
- ERROR tests/test_double_estimator.py
- ERROR tests/test_l1c_codes.py
- FAILED tests/test_coherent_integration.py::test_a_long_epoch_actually_locks
- FAILED tests/test_coherent_integration.py::test_every_signal_opens_at_one_correlation_interval[None]
- FAILED tests/test_coherent_integration.py::test_the_requested_duration_is_capped_not_exceeded[None-20-10]
- FAILED tests/test_coherent_integration.py::test_the_requested_duration_is_capped_not_exceeded[None-5-5]
- FAILED tests/test_epoch_symbol_alignment.py::test_only_l1ca_needs_bit_sync
- FAILED tests/test_l1c.py::test_a_replica_without_the_subcarrier_would_not_acquire_at_all
- FAILED tests/test_l1c.py::test_a_very_strong_signal_raises_cross_correlation_peaks
- FAILED tests/test_l1c.py::test_acquisition_detects_the_signal_and_reports_its_code_phase
- FAILED tests/test_l1c.py::test_both_components_come_out_at_their_transmitted_amplitudes
- FAILED tests/test_l1c.py::test_every_divisor_of_the_code_period_is_a_legal_epoch[10]
- FAILED tests/test_l1c.py::test_every_divisor_of_the_code_period_is_a_legal_epoch[1]
- FAILED tests/test_l1c.py::test_every_divisor_of_the_code_period_is_a_legal_epoch[2]
- FAILED tests/test_l1c.py::test_every_divisor_of_the_code_period_is_a_legal_epoch[5]
- FAILED tests/test_l1c.py::test_extension_does_not_wait_for_the_overlay_search
- FAILED tests/test_l1c.py::test_only_the_pilot_carries_a_subcarrier_pattern
- FAILED tests/test_l1c.py::test_the_acquisition_code_is_the_pilot_on_the_signals_own_chip_axis
- FAILED tests/test_l1c.py::test_the_carrier_loop_runs_on_the_pilot
- FAILED tests/test_l1c.py::test_the_ceiling_on_integration_is_the_data_symbol_not_the_overlay
- FAILED tests/test_l1c.py::test_the_overlay_is_not_folded_into_the_replica
- FAILED tests/test_l1c.py::test_the_overlay_syncs_and_buys_a_four_quadrant_discriminator
- FAILED tests/test_l1c.py::test_the_power_split_is_carried_into_the_code_set
- FAILED tests/test_l1c.py::test_the_primary_period_is_ten_milliseconds_and_the_overlay_is_eighteen_seconds
- FAILED tests/test_l1c.py::test_the_synchroniser_uses_the_fft_search
- FAILED tests/test_l1c.py::test_the_two_components_share_a_carrier_branch
- FAILED tests/test_l1c.py::test_tracking_converges_and_hands_over_to_the_pll
- FAILED tests/test_l1c.py::test_tracking_survives_noise_and_a_realistic_seeding_error
- FAILED tests/test_l1c.py::test_wipe_off_holds_one_sign_for_all_ten_intervals_of_a_code_period
- FAILED tests/test_tracking_regression.py::test_tracking_converges[l1c_clean]
- FAILED tests/test_tracking_regression.py::test_tracking_converges[l1c_navbits_seeded]
- FAILED tests/test_tracking_regression.py::test_tracking_converges[l1c_noisy]
- FAILED tests/test_tracking_regression.py::test_tracking_converges[l1c_overlay_synced]
- FAILED tests/test_tracking_regression.py::test_tracking_converges[l1c_side_peak_seed]
- FAILED tests/test_tracking_regression.py::test_tracking_matches_golden[l1c_clean]
- FAILED tests/test_tracking_regression.py::test_tracking_matches_golden[l1c_navbits_seeded]
- FAILED tests/test_tracking_regression.py::test_tracking_matches_golden[l1c_noisy]
- FAILED tests/test_tracking_regression.py::test_tracking_matches_golden[l1c_overlay_synced]
- FAILED tests/test_tracking_regression.py::test_tracking_matches_golden[l1c_side_peak_seed]
- FAILED tests/test_tracking_regression.py::test_tracking_matches_golden[l1ca_navbits_seeded]
- FAILED tests/test_tracking_regression.py::test_tracking_matches_golden[l1ca_noisy]
- FAILED tests/test_tracking_regression.py::test_tracking_matches_golden[l2c_late_code_phase]
- FAILED tests/test_tracking_regression.py::test_tracking_matches_golden[l2c_navbits_noisy]
- FAILED tests/test_tracking_regression.py::test_tracking_matches_golden[l5_clean]
- FAILED tests/test_tracking_regression.py::test_tracking_matches_golden[l5_navbits_seeded]
- FAILED tests/test_tracking_regression.py::test_tracking_matches_golden[l5_noisy]
