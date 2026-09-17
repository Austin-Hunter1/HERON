"""
Round-tripping tracking results, and refusing files that do not match.

The round trip has to be exact for a reason beyond tidiness: notebook 02 forms
pseudoranges from `code_phase_ms`, where a millisecond is 300 km.  Anything less
than bit-exact on the float arrays is a bug, not a rounding tolerance.

The guard tests matter just as much.  A tracking file that is merely *stale* --
the right shape, from the wrong collect -- produces a navigation solution that
converges to somewhere confidently wrong, which is far harder to notice than a
crash.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils import tracking_channel, tracking_io


def make_outputs(*, epochs: int = 40, components: int = 2, cn0: int = 4, seed: int = 0):
    """A populated SignalTrackingOutputs with a partially-filled capacity."""
    rng = np.random.default_rng(seed)
    outputs = tracking_channel.SignalTrackingOutputs(
        capacity=epochs + 25,  # deliberately larger, so the zero tail exists
        num_components=components,
        cn0_capacity=cn0 + 5,
        tap_layout=tracking_channel.epl_tap_layout(0.5),
    )
    for i in range(epochs):
        outputs.uptime_epoch_ms[i] = i * 10.0
        outputs.code_phase_ms[i] = i * 10.0 + 0.123456789
        outputs.carr_phase_errors_cycles[i] = rng.normal()
        outputs.code_phase_errors_chips[i] = rng.normal()
        outputs.subcarrier_offset_chips[i] = rng.normal()
        outputs.carr_phase_cycles[i] = rng.normal() * 1e4
        outputs.doppler_freq_hz[i] = 1234.5 + rng.normal()
        outputs.delta_omega[i] = rng.normal()
        outputs.prompt_corr_circ_length[i] = rng.random()
        outputs.pll_mode[i] = i > 10
        outputs.epoch_duration_ms[i] = 5.0 if i < 12 else 10.0
        outputs.overlay_synced[i] = i >= 8
        outputs.corr[i] = rng.normal(size=(3, components)) + 1j * rng.normal(
            size=(3, components)
        )
    outputs.output_index = epochs
    for i in range(cn0):
        outputs.cn0_dbhz[i] = 42.0 + rng.normal(size=components)
        outputs.cn0_uptime_ms[i] = i * 100.0
    outputs.cn0_index = cn0
    return outputs


class FakeChannel:
    def __init__(self, outputs):
        self.outputs = outputs


class FakeAcquisition:
    """Mirrors the two `bpsk_acquisition.AcquisitionResult` properties that are saved."""

    def __init__(self, doppler_hz, code_phase_ms):
        self.acq_doppler_hz = doppler_hz
        self.acq_code_phase_ms = code_phase_ms


@pytest.fixture
def saved(tmp_path):
    channels = {
        "G01": FakeChannel(make_outputs(seed=1)),
        "G19": FakeChannel(make_outputs(epochs=30, seed=2)),
    }
    path = tracking_io.save_tracking_results(
        tmp_path / "nested" / "tracking.h5",
        channels,
        collect_id="20230417_103222_BALLOON",
        signal_type_id="GPS_L5",
        samp_rate=22e6,
        experiment_name="20230417_SURGE_L5AERORooftop",
        acquisition_results={
            "G01": FakeAcquisition(-2100.0, 0.37),
            "G19": FakeAcquisition(1500.0, 0.81),
        },
    )
    return path, channels


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_save_creates_parent_directories(saved):
    path, _ = saved
    assert path.exists()


def test_metadata_round_trips(saved):
    path, _ = saved
    run = tracking_io.load_tracking_results(path)
    assert run.collect_id == "20230417_103222_BALLOON"
    assert run.signal_type_id == "GPS_L5"
    assert run.samp_rate == 22e6
    assert run.experiment_name == "20230417_SURGE_L5AERORooftop"
    assert run.format_version == tracking_io.FORMAT_VERSION
    assert run.signal_ids == ["G01", "G19"]
    assert len(run) == 2


@pytest.mark.parametrize("field", tracking_io._EPOCH_FIELDS)
def test_every_epoch_field_round_trips_exactly(saved, field):
    path, channels = saved
    run = tracking_io.load_tracking_results(path)
    original = channels["G01"].outputs
    loaded = run["G01"].outputs
    assert np.array_equal(
        np.asarray(getattr(original, field))[original.valid],
        np.asarray(getattr(loaded, field))[loaded.valid],
    )


def test_code_phase_round_trips_bit_exactly(saved):
    """
    Not a tolerance test.  One millisecond of code phase is 300 km of pseudorange,
    so a float that comes back merely close is a defect.
    """
    path, channels = saved
    loaded = tracking_io.load_tracking_results(path)["G01"].outputs
    original = channels["G01"].outputs
    assert np.array_equal(
        original.code_phase_ms[original.valid], loaded.code_phase_ms[loaded.valid]
    )


def test_correlators_round_trip_including_phase(saved):
    path, channels = saved
    original = channels["G01"].outputs
    loaded = tracking_io.load_tracking_results(path)["G01"].outputs
    assert np.array_equal(original.corr[original.valid], loaded.corr[loaded.valid])
    assert loaded.corr.dtype == complex


def test_cn0_arrays_round_trip_on_their_own_cadence(saved):
    path, channels = saved
    original = channels["G01"].outputs
    loaded = tracking_io.load_tracking_results(path)["G01"].outputs
    assert loaded.cn0_index == original.cn0_index
    assert np.array_equal(
        original.cn0_dbhz[original.cn0_valid], loaded.cn0_dbhz[loaded.cn0_valid]
    )


def test_the_zero_tail_is_not_persisted(saved):
    """
    `SignalTrackingOutputs` pre-allocates and stops writing when full, so the tail
    is zeros.  Saving those would draw a spurious line back through (0, 0) in every
    plot and, far worse, feed zero code phases into a pseudorange.
    """
    path, channels = saved
    original = channels["G01"].outputs
    loaded = tracking_io.load_tracking_results(path)["G01"].outputs
    assert original.capacity > original.output_index, "fixture must have a zero tail"
    assert loaded.output_index == original.output_index
    assert loaded.capacity == original.output_index
    assert loaded.uptime_epoch_ms[-1] != 0.0


def test_tap_layout_round_trips_by_role(saved):
    path, channels = saved
    loaded = tracking_io.load_tracking_results(path)["G01"].outputs
    assert loaded.tap_layout.roles == channels["G01"].outputs.tap_layout.roles
    # And the role-addressed views still land on the right taps.
    assert np.array_equal(loaded.prompt_corr, loaded.tap(tracking_channel.PROMPT))


def test_acquisition_seed_round_trips(saved):
    path, _ = saved
    run = tracking_io.load_tracking_results(path)
    assert run["G01"].acquisition_doppler_hz == -2100.0
    assert run["G19"].acquisition_code_phase_ms == 0.81


def test_missing_acquisition_results_are_recorded_as_nan(tmp_path):
    path = tracking_io.save_tracking_results(
        tmp_path / "t.h5",
        {"G01": FakeChannel(make_outputs())},
        collect_id="c",
        signal_type_id="GPS_L5",
        samp_rate=22e6,
    )
    assert np.isnan(tracking_io.load_tracking_results(path)["G01"].acquisition_doppler_hz)


def test_accepts_a_bare_channel_or_an_adapter(tmp_path):
    """Notebook 01 holds adapters, tests hold channels; both must save."""

    class FakeAdapter:
        def __init__(self, channel):
            self.channel = channel

    outputs = make_outputs()
    for holder in (FakeChannel(outputs), FakeAdapter(FakeChannel(outputs))):
        path = tracking_io.save_tracking_results(
            tmp_path / f"{type(holder).__name__}.h5",
            {"G01": holder},
            collect_id="c",
            signal_type_id="GPS_L5",
            samp_rate=22e6,
        )
        assert len(tracking_io.load_tracking_results(path)) == 1


def test_duration_is_reported_per_channel_and_for_the_run(saved):
    path, _ = saved
    run = tracking_io.load_tracking_results(path)
    assert run["G01"].duration_ms == pytest.approx(390.0)  # 40 epochs, 10 ms apart
    assert run["G19"].duration_ms == pytest.approx(290.0)
    assert run.shortest_duration_ms == pytest.approx(290.0)


# ---------------------------------------------------------------------------
# Refusing the wrong file
# ---------------------------------------------------------------------------


def test_missing_file_says_what_to_run(tmp_path):
    with pytest.raises(FileNotFoundError, match="notebook 01"):
        tracking_io.load_tracking_results(tmp_path / "absent.h5")


def test_a_future_format_version_is_refused(saved):
    import h5py

    path, _ = saved
    with h5py.File(path, "a") as f:
        f.attrs["format_version"] = tracking_io.FORMAT_VERSION + 1
    with pytest.raises(ValueError, match="format version"):
        tracking_io.load_tracking_results(path)


def test_require_accepts_a_matching_run(saved):
    path, _ = saved
    run = tracking_io.require_tracking_run(
        path,
        collect_id="20230417_103222_BALLOON",
        signal_type_id="GPS_L5",
        minimum_signals=2,
        minimum_duration_ms=100.0,
    )
    assert len(run) == 2


def test_require_rejects_a_different_collect(saved):
    path, _ = saved
    with pytest.raises(ValueError, match="EXPERIMENT_INDEX"):
        tracking_io.require_tracking_run(path, collect_id="something_else")


def test_require_rejects_a_different_signal(saved):
    path, _ = saved
    with pytest.raises(ValueError, match="SIGNAL_ID"):
        tracking_io.require_tracking_run(path, signal_type_id="GPS_L1CA")


def test_require_rejects_too_few_satellites(saved):
    path, _ = saved
    with pytest.raises(ValueError, match="position fix"):
        tracking_io.require_tracking_run(path, minimum_signals=5)


def test_require_rejects_too_short_a_run(saved):
    path, _ = saved
    with pytest.raises(ValueError, match="TRACK_DURATION_MS"):
        tracking_io.require_tracking_run(path, minimum_duration_ms=60_000.0)


# ---------------------------------------------------------------------------
# A loaded result behaves like a tracked one
# ---------------------------------------------------------------------------


def test_a_loaded_result_feeds_the_symbol_extractor(saved):
    """
    The point of returning a real `SignalTrackingOutputs`: everything downstream
    works on a loaded run without knowing it came from a file.
    """
    from utils.nav import symbols as nav_symbols

    path, _ = saved
    outputs = tracking_io.load_tracking_results(path)["G01"].outputs
    stream = nav_symbols.extract(outputs, "GPS_L5")
    assert stream.symbol_period_ms == 10
    assert stream.epochs_per_symbol == 1
    assert len(stream) > 0


# ---------------------------------------------------------------------------
# The epoch breakdown both notebooks print
# ---------------------------------------------------------------------------


def _outputs_with(pattern):
    """`pattern` is a list of (epoch_duration_ms, pll_mode) per epoch."""
    outputs = tracking_channel.SignalTrackingOutputs(
        capacity=len(pattern) + 5,
        num_components=1,
        cn0_capacity=1,
        tap_layout=tracking_channel.epl_tap_layout(0.5),
    )
    for i, (duration, mode) in enumerate(pattern):
        outputs.uptime_epoch_ms[i] = float(i)
        outputs.epoch_duration_ms[i] = duration
        outputs.pll_mode[i] = mode
    outputs.output_index = len(pattern)
    return outputs


def test_epochs_are_crossed_by_length_and_by_loop_mode():
    """
    The two splits do not coincide, and that is the informative part: a channel
    sits at one interval *in PLL* for as long as the symbol boundary takes to find.
    Reporting length and mode separately would hide exactly that state.
    """
    outputs = _outputs_with([(1.0, False)] * 3 + [(1.0, True)] * 2 + [(5.0, True)] * 4)
    headers, counts = tracking_io.epoch_duration_mode_columns({"G01": outputs})
    assert headers == ["1 ms FLL", "1 ms PLL", "5 ms PLL"]
    assert counts["G01"] == ["3", "2", "4"]


def test_only_combinations_that_occur_get_a_column():
    """A channel that locked immediately has no `1 ms FLL` epochs, and a column of
    zeros for every signal is a column that says nothing."""
    outputs = _outputs_with([(1.0, True)] * 2 + [(20.0, True)] * 3)
    headers, _ = tracking_io.epoch_duration_mode_columns({"G01": outputs})
    assert headers == ["1 ms PLL", "20 ms PLL"]


def test_columns_span_every_signal_so_the_rows_stay_alignable():
    """One channel that never extended and one that did must still produce rows of
    the same width, or they cannot go in one table."""
    never_locked = _outputs_with([(1.0, False)] * 6)
    healthy = _outputs_with([(1.0, False)] * 2 + [(5.0, True)] * 4)
    headers, counts = tracking_io.epoch_duration_mode_columns(
        {"G01": never_locked, "G02": healthy}
    )
    assert headers == ["1 ms FLL", "5 ms PLL"]
    assert counts["G01"] == ["6", "0"]
    assert counts["G02"] == ["2", "4"]
    assert len(counts["G01"]) == len(counts["G02"]) == len(headers)


def test_the_zero_tail_is_excluded_from_the_counts():
    """`SignalTrackingOutputs` pre-allocates, so an unfilled tail is (0 ms, FLL).
    Counting it would invent a column and inflate every FLL total."""
    outputs = _outputs_with([(5.0, True)] * 4)
    headers, counts = tracking_io.epoch_duration_mode_columns({"G01": outputs})
    assert headers == ["5 ms PLL"]
    assert counts["G01"] == ["4"]
