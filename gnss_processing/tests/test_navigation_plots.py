"""
Smoke tests for the navigation plots.

These do not check that a plot looks right -- nothing automated can.  What they
check is that each helper survives the inputs it will actually meet in notebook
02, which for a navigation solution means a lot of NaN: epochs with too few
satellites, satellites below the horizon, channels that dropped lock.  A plotting
helper that raises on the first NaN fails at the end of a long notebook, which is
the most expensive place to find out.
"""

from __future__ import annotations

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402

from utils import plotting  # noqa: E402


@pytest.fixture
def fig():
    figure = plt.figure(figsize=(8, 5))
    yield figure
    plt.close(figure)


SAT_IDS = ["G03", "G07", "G19", "G26"]


def sky_tracks(num_points: int = 20):
    rng = np.random.default_rng(0)
    azimuth, elevation = {}, {}
    for i, sat_id in enumerate(SAT_IDS):
        azimuth[sat_id] = np.linspace(i * 80.0, i * 80.0 + 5.0, num_points)
        elevation[sat_id] = np.linspace(15.0 + i * 15.0, 18.0 + i * 15.0, num_points)
    return azimuth, elevation


# ---------------------------------------------------------------------------
# Skyplot
# ---------------------------------------------------------------------------


def test_skyplot_draws_tracks(fig):
    azimuth, elevation = sky_tracks()
    ax = plotting.plot_skyplot(fig, azimuth, elevation)
    assert ax.name == "polar"
    # Zenith at the centre: the radial axis runs 90 inward to 0 outward.
    assert ax.get_ylim() == (90, 0)


def test_skyplot_radial_labels_agree_with_the_radius_they_sit_on(fig):
    """
    The labels once said the opposite of where the satellites were.

    `set_rlim(90, 0)` reverses the axis, and the labels were reversed again on top
    of it, so the centre was labelled "0" while a satellite at 80 degrees was drawn
    on it.  Only the 45 degree ring came out right, which is exactly the kind of
    error that survives a glance at the figure.
    """
    azimuth, elevation = sky_tracks()
    ax = plotting.plot_skyplot(fig, azimuth, elevation)
    fig.canvas.draw()
    for radius, label in zip(ax.get_yticks(), ax.get_yticklabels()):
        assert float(label.get_text()) == pytest.approx(radius), (
            f"the ring at elevation {radius} is labelled {label.get_text()!r}"
        )


def test_skyplot_shades_below_the_mask_and_not_above_it(fig):
    """
    Red marks what the mask calls into question, which is the LOW band.

    Shading the other side tinted every satellite the mask accepts and left the low
    ones on clear ground -- backwards anywhere, and most misleading on the
    horizon-pointed collects, where the low satellites are the strong ones.
    """
    azimuth, elevation = sky_tracks()
    ax = plotting.plot_skyplot(fig, azimuth, elevation, mask_deg=10.0)

    shaded = [c for c in ax.collections if isinstance(c, PolyCollection)]
    assert len(shaded) == 1, "the mask band is the only filled region"
    radii = shaded[0].get_paths()[0].vertices[:, 1]
    assert radii.min() == pytest.approx(0.0)
    assert radii.max() == pytest.approx(10.0)


def test_skyplot_mask_can_be_turned_off(fig):
    azimuth, elevation = sky_tracks()
    ax = plotting.plot_skyplot(fig, azimuth, elevation, mask_deg=0.0)
    assert not [c for c in ax.collections if isinstance(c, PolyCollection)]


def test_skyplot_accepts_scalar_positions(fig):
    azimuth = {s: 45.0 * i for i, s in enumerate(SAT_IDS)}
    elevation = {s: 30.0 for s in SAT_IDS}
    plotting.plot_skyplot(fig, azimuth, elevation)


def test_skyplot_distinguishes_tracked_from_merely_visible(fig):
    azimuth, elevation = sky_tracks()
    ax = plotting.plot_skyplot(fig, azimuth, elevation, tracked=SAT_IDS[:2])
    labelled = [t.get_text() for t in ax.texts]
    assert set(SAT_IDS).issubset(set(labelled)), "every visible satellite gets a label"


def test_skyplot_colours_by_cn0_and_adds_a_colourbar(fig):
    azimuth, elevation = sky_tracks()
    cn0 = {s: 38.0 + 3 * i for i, s in enumerate(SAT_IDS)}
    plotting.plot_skyplot(fig, azimuth, elevation, cn0_dbhz=cn0, tracked=SAT_IDS)
    assert any(a.get_label() == "<colorbar>" for a in fig.axes)


def test_skyplot_skips_satellites_below_the_horizon(fig):
    azimuth = {"G01": np.array([10.0, 20.0]), "G02": np.array([100.0, 110.0])}
    elevation = {"G01": np.array([-5.0, -2.0]), "G02": np.array([40.0, 41.0])}
    ax = plotting.plot_skyplot(fig, azimuth, elevation)
    assert [t.get_text() for t in ax.texts] == ["G02"]


def test_skyplot_handles_missing_cn0_for_some_satellites(fig):
    azimuth, elevation = sky_tracks()
    cn0 = {SAT_IDS[0]: 42.0, SAT_IDS[1]: np.nan}
    plotting.plot_skyplot(fig, azimuth, elevation, cn0_dbhz=cn0, tracked=SAT_IDS)


def test_skyplot_with_no_visible_satellites_does_not_raise(fig):
    plotting.plot_skyplot(fig, {"G01": 10.0}, {"G01": -30.0})


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


def enu_series(n: int = 100, *, nan_rows: int = 0):
    rng = np.random.default_rng(1)
    enu = np.column_stack([
        rng.normal(0.0, 3.0, n),
        rng.normal(0.0, 4.0, n),
        rng.normal(0.0, 8.0, n),
    ])
    if nan_rows:
        enu[:nan_rows] = np.nan
    return enu


def test_position_plot_draws_scatter_and_time_series(fig):
    ax = plotting.plot_position_enu(fig, enu_series(), np.arange(100) * 0.1)
    assert ax.get_xlabel() == "East [m]"
    assert len(fig.axes) == 2


def test_position_plot_tolerates_epochs_without_a_fix(fig):
    """
    The realistic case.  With five satellites, one dropping out leaves an epoch
    with no fix at all, and those rows come through as NaN.
    """
    plotting.plot_position_enu(fig, enu_series(nan_rows=20), np.arange(100) * 0.1)


def test_position_plot_with_no_fixes_at_all(fig):
    plotting.plot_position_enu(fig, np.full((10, 3), np.nan))


def test_position_plot_with_too_few_points_for_an_ellipse(fig):
    """Two fixes cannot define a covariance; the helper must still draw."""
    plotting.plot_position_enu(fig, enu_series(n=2))


def test_position_plot_without_a_time_axis(fig):
    plotting.plot_position_enu(fig, enu_series())


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


def test_clock_plot_reports_drift(fig):
    t = np.arange(200) * 0.1
    # 2 ppm of drift on top of a 1 km offset.
    bias = 1000.0 + 2e-6 * 2.998e8 * t + np.random.default_rng(2).normal(0, 0.5, len(t))
    ax = plotting.plot_clock_solution(fig, t, bias)
    annotations = " ".join(a.get_text() for a in ax.texts)
    assert "ppm" in annotations
    assert "+2.0" in annotations or "+1.9" in annotations or "+2.1" in annotations


def test_clock_plot_tolerates_gaps(fig):
    t = np.arange(50) * 0.1
    bias = 1000.0 + 5.0 * t
    bias[10:20] = np.nan
    plotting.plot_clock_solution(fig, t, bias)


def test_clock_plot_with_a_single_fix(fig):
    """One point cannot support a line fit, and must not raise."""
    plotting.plot_clock_solution(fig, np.array([0.0]), np.array([1000.0]))


def test_clock_plot_with_no_fixes(fig):
    plotting.plot_clock_solution(fig, np.arange(5.0), np.full(5, np.nan))


# ---------------------------------------------------------------------------
# Residuals
# ---------------------------------------------------------------------------


def test_residual_plot_reports_rms_in_the_title(fig):
    rng = np.random.default_rng(3)
    residuals = rng.normal(0.0, 2.0, (50, len(SAT_IDS)))
    ax = plotting.plot_pseudorange_residuals(fig, np.arange(50) * 0.1, residuals, SAT_IDS)
    assert "RMS" in ax.get_title()


def test_residual_plot_tolerates_a_satellite_with_no_data(fig):
    residuals = np.random.default_rng(4).normal(0.0, 2.0, (50, len(SAT_IDS)))
    residuals[:, 1] = np.nan
    plotting.plot_pseudorange_residuals(fig, np.arange(50) * 0.1, residuals, SAT_IDS)


def test_residual_plot_with_nothing_at_all(fig):
    ax = plotting.plot_pseudorange_residuals(
        fig, np.arange(5.0), np.full((5, 2), np.nan), ["G01", "G02"]
    )
    assert "RMS" not in ax.get_title()


# ---------------------------------------------------------------------------
# Correction magnitudes
# ---------------------------------------------------------------------------


def test_correction_plot_orders_by_magnitude(fig):
    terms = {
        "satellite_clock": np.full((10, 4), 30000.0),
        "troposphere": np.full((10, 4), -3.0),
        "ionosphere": np.full((10, 4), -8.0),
    }
    ax = plotting.plot_correction_magnitudes(fig, terms, SAT_IDS)
    # barh draws bottom-up, so the largest ends up last in tick order.
    labels = [t.get_text() for t in ax.get_yticklabels()]
    assert labels[-1] == "satellite clock"
    assert ax.get_xscale() == "log"


def test_correction_plot_ignores_geometry_entries(fig):
    """`correct_pseudoranges` returns elevation and azimuth alongside the range
    corrections; they are not corrections and must not be plotted as metres."""
    terms = {
        "satellite_clock": np.full((5, 2), 1000.0),
        "elevation_deg": np.full((5, 2), 45.0),
        "azimuth_deg": np.full((5, 2), 180.0),
    }
    ax = plotting.plot_correction_magnitudes(fig, terms, ["G01", "G02"])
    labels = [t.get_text() for t in ax.get_yticklabels()]
    assert labels == ["satellite clock"]


def test_correction_plot_with_no_terms(fig):
    plotting.plot_correction_magnitudes(fig, {}, [])


def test_correction_plot_ignores_all_nan_terms(fig):
    terms = {"satellite_clock": np.full((5, 2), np.nan), "troposphere": np.full((5, 2), 2.0)}
    ax = plotting.plot_correction_magnitudes(fig, terms, ["G01", "G02"])
    assert [t.get_text() for t in ax.get_yticklabels()] == ["troposphere"]


def orbit_clock_differences(num_epochs: int = 30):
    """A day either side of a collect, on the GPS time-of-week axis the plot uses."""
    rng = np.random.default_rng(1)
    position = np.abs(rng.normal(2.0, 0.3, (num_epochs, len(SAT_IDS))))
    clock = np.abs(rng.normal(0.5, 0.1, (num_epochs, len(SAT_IDS))))
    return np.linspace(432_000.0, 518_400.0, num_epochs), position, clock


def drawn(ax):
    """Labelled satellite traces, excluding the marker at the collect time."""
    return [line for line in ax.lines if not str(line.get_label()).startswith("_")]


def test_orbit_and_clock_differences_draws_both_panels(fig):
    time_h, position, clock = orbit_clock_differences()
    top, bottom = plotting.plot_orbit_and_clock_differences(
        fig, time_h, position, clock, SAT_IDS
    )
    assert len(drawn(top)) == len(SAT_IDS)
    assert len(drawn(bottom)) == len(SAT_IDS)
    # Magnitudes, so neither panel should invite a reader below zero.
    assert top.get_ylim()[0] == 0.0 and bottom.get_ylim()[0] == 0.0


def test_the_collect_time_is_marked_only_when_it_is_on_the_axis(fig):
    """The collect is the one instant a fix actually used -- but a caller plotting
    a window that excludes it should not get a line at the edge of the axis."""
    _, position, clock = orbit_clock_differences()
    tow = np.linspace(432_000.0, 518_400.0, len(position))
    top, _ = plotting.plot_orbit_and_clock_differences(
        fig, tow, position, clock, SAT_IDS, mark_time=475_818.0
    )
    assert len(top.lines) == len(SAT_IDS) + 1

    other = plt.figure()
    try:
        ax, _ = plotting.plot_orbit_and_clock_differences(
            other, tow, position, clock, SAT_IDS, mark_time=100_000.0
        )
        assert len(ax.lines) == len(SAT_IDS)
    finally:
        plt.close(other)


def test_a_toe_is_marked_on_the_trace_it_belongs_to(fig):
    """The circle says where a data set's fit is centred, so it is only readable if
    it lands on that satellite's own line and in its own colour."""
    tow, position, clock = orbit_clock_differences()
    changes = np.zeros(position.shape, dtype=bool)
    changes[5, 1] = changes[17, 1] = True
    top, _ = plotting.plot_orbit_and_clock_differences(
        fig, tow, position, clock, SAT_IDS, ephemeris_toe_marks=changes
    )
    marks = [line for line in top.lines if line.get_linestyle() == "None"]
    assert len(marks) == 1
    assert len(marks[0].get_xdata()) == 2
    traces = [line for line in top.lines if line.get_label() == SAT_IDS[1]]
    assert marks[0].get_color() == traces[0].get_color()


def test_toe_ticks_show_where_the_data_sets_are_centred(fig):
    """A merged rug of every `toe`, for callers that want them beside the
    per-satellite circles rather than instead of them."""
    tow, position, clock = orbit_clock_differences()
    toes = np.array([tow[0], tow[len(tow) // 2], tow[-1], tow[-1] + 9_999.0])
    top, _ = plotting.plot_orbit_and_clock_differences(
        fig, tow, position, clock, SAT_IDS, ephemeris_toes=toes
    )
    ticks = [line for line in top.lines if line.get_label() == "ephemeris toe"]
    assert len(ticks) == 1
    assert len(ticks[0].get_xdata()) == 3      # the fourth is off the axis


def test_sp3_nodes_are_drawn_only_where_they_fall_on_the_axis(fig):
    tow, position, clock = orbit_clock_differences()
    nodes = np.concatenate([tow[::5], [tow[0] - 5_000.0, tow[-1] + 5_000.0]])
    top, _ = plotting.plot_orbit_and_clock_differences(
        fig, tow, position, clock, SAT_IDS, sp3_epochs=nodes
    )
    rug = [line for line in top.lines if line.get_label() == "SP3 nodes"]
    assert len(rug) == 1
    assert len(rug[0].get_xdata()) == len(tow[::5])


def test_orbit_and_clock_differences_tolerates_a_satellite_with_no_precise_clock(fig):
    """A satellite absent from the SP3 clock records is all-NaN in one panel and
    real in the other; it must still draw, and not take the other panel with it."""
    time_h, position, clock = orbit_clock_differences()
    clock[:, 1] = np.nan
    top, bottom = plotting.plot_orbit_and_clock_differences(
        fig, time_h, position, clock, SAT_IDS
    )
    assert len(drawn(top)) == len(SAT_IDS)
    assert len(drawn(bottom)) == len(SAT_IDS) - 1


def test_orbit_and_clock_differences_with_nothing_to_draw(fig):
    time_s, position, clock = orbit_clock_differences()
    plotting.plot_orbit_and_clock_differences(
        fig, time_s, np.full_like(position, np.nan), np.full_like(clock, np.nan), SAT_IDS
    )


def test_a_window_of_hours_is_ticked_every_hour(fig):
    """A `toe` lands on a whole hour, so the axis has to show whole hours for the
    circles to be readable against it."""
    tow, position, clock = orbit_clock_differences()
    top, _ = plotting.plot_orbit_and_clock_differences(
        fig, np.linspace(0.0, 24.0, len(position)), position, clock, SAT_IDS
    )
    ticks = top.get_xticks()
    assert np.allclose(np.diff(ticks), 1.0)


def test_a_very_wide_window_is_left_to_matplotlib(fig):
    """Hourly ticks over a week would be a smear, so the locator backs off."""
    tow, position, clock = orbit_clock_differences()
    top, _ = plotting.plot_orbit_and_clock_differences(
        fig, np.linspace(0.0, 168.0, len(position)), position, clock, SAT_IDS
    )
    assert not np.allclose(np.diff(top.get_xticks()), 1.0)


def prefit_residuals(num_epochs: int = 40):
    """A common clock ramp with a per-satellite offset on top -- the two things the
    plot exists to separate."""
    time_s = np.linspace(0.0, 60.0, num_epochs)
    clock = -1_800_000.0 + 22.0 * time_s               # bias, drifting at ~0.07 ppm
    offsets = np.array([-38.0, -12.0, 9.0, 41.0])      # a-priori position error
    return time_s, clock[:, None] + offsets[None, :]


def test_prefit_plot_draws_one_line_per_satellite(fig):
    time_s, residual = prefit_residuals()
    ax = plotting.plot_prefit_residuals(fig, time_s, residual, SAT_IDS)
    assert len(ax.lines) == len(SAT_IDS)
    # In metres, and carrying the per-satellite offsets rather than a common
    # value: the lines must not be collapsed onto one another by the plotting.
    at_start = sorted(line.get_ydata()[0] for line in ax.lines)
    assert at_start[-1] - at_start[0] == pytest.approx(79.0)


def test_prefit_plot_keeps_the_clock_ramp_that_dwarfs_the_spread(fig):
    """The bundle travels 22 m/s here against 79 m of spread, so the axis is set by
    the clock. That ratio is the physics, not a plotting failure -- section 9 is
    where the spread gets an axis of its own."""
    time_s, residual = prefit_residuals()
    ax = plotting.plot_prefit_residuals(fig, time_s, residual, SAT_IDS)
    lo, hi = ax.get_ylim()
    assert (hi - lo) > 1_000.0


def test_prefit_plot_tolerates_a_satellite_that_drops_out(fig):
    time_s, residual = prefit_residuals()
    residual[20:, 2] = np.nan
    ax = plotting.plot_prefit_residuals(fig, time_s, residual, SAT_IDS)
    assert len(ax.lines) == len(SAT_IDS)
    assert len(ax.lines[2].get_xdata()) == 20


def test_prefit_plot_with_nothing_measured(fig):
    time_s, residual = prefit_residuals()
    plotting.plot_prefit_residuals(fig, time_s, np.full_like(residual, np.nan), SAT_IDS)
