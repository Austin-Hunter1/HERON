"""
Interpolating an SP3 file onto the instants a receiver measured at.

The position path exists because the upstream smoothing spline does not pass
through the SP3 nodes -- measured at 1.7 m RMS on a real file, which is the size
of the broadcast error it would be used to measure.  So the first test is that a
polynomial comes back exactly: an orbit over an hour is very nearly one, and an
interpolator that cannot reproduce a cubic will not reproduce an orbit either.

The clock path is linear on purpose and the tests pin the two things that makes
easy to get wrong: the microsecond-to-second scaling SP3 states its clocks in, and
the missing samples that must be dropped rather than propagated.
"""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from utils import broadcast_ephemeris, precise_orbits


def sp3_like(nodes, position, clock):
    return SimpleNamespace(epochs=np.asarray(nodes, float),
                           position={"G01": np.asarray(position, float)},
                           clock={"G01": np.asarray(clock, float)})


def test_position_is_exact_through_a_polynomial():
    nodes = np.arange(0.0, 30) * 300.0
    poly = lambda t: np.stack([1e3 + 2*t + 1e-4*t**2, -5e3 + t, 3e-7*t**3], axis=-1)
    sp3 = sp3_like(nodes, poly(nodes), np.zeros(len(nodes)))
    times = nodes[10:20] + 137.0
    got = precise_orbits.interpolate_position_m(sp3, "G01", times)
    assert np.allclose(got, poly(times), atol=1e-6)


def test_clock_is_linear_and_in_seconds():
    nodes = np.arange(0.0, 10) * 300.0
    micro = np.arange(10, dtype=float) * 2.0
    sp3 = sp3_like(nodes, np.zeros((10, 3)), micro)
    got = precise_orbits.interpolate_clock_s(sp3, "G01", np.array([150.0]))
    assert got[0] == pytest.approx(1.0e-6)


def test_a_missing_clock_sample_is_dropped_not_propagated():
    nodes = np.arange(0.0, 10) * 300.0
    micro = np.arange(10, dtype=float)
    micro[5] = np.nan
    sp3 = sp3_like(nodes, np.zeros((10, 3)), micro)
    got = precise_orbits.interpolate_clock_s(sp3, "G01", np.array([1500.0]))
    assert np.isfinite(got[0]) and got[0] == pytest.approx(5.0e-6, abs=1e-9)


def test_a_satellite_with_no_clock_gives_nan():
    nodes = np.arange(0.0, 10) * 300.0
    sp3 = sp3_like(nodes, np.zeros((10, 3)), np.full(10, np.nan))
    assert np.all(np.isnan(precise_orbits.interpolate_clock_s(sp3, "G01", nodes)))
    sp3.clock.clear()
    assert np.all(np.isnan(precise_orbits.interpolate_clock_s(sp3, "G01", nodes)))


def test_scalar_time_still_returns_arrays():
    nodes = np.arange(0.0, 20) * 300.0
    sp3 = sp3_like(nodes, np.zeros((20, 3)), np.zeros(20))
    assert precise_orbits.interpolate_position_m(sp3, "G01", 1500.0).shape == (1, 3)
    assert precise_orbits.interpolate_clock_s(sp3, "G01", 1500.0).shape == (1,)


# ---------------------------------------------------------------------------
# Asking what the archive has, before downloading any of it
# ---------------------------------------------------------------------------


def test_availability_reports_the_same_products_download_would_try(monkeypatch, tmp_path):
    """
    `availability` is only useful if it answers for the products `download_sp3`
    actually attempts, in the same order -- otherwise it can report a product as
    present that the download would never reach, or miss the one that saves a
    recent collect.
    """
    monkeypatch.setattr(precise_orbits.cddis, "exists", lambda url: (True, "1.0 MB", 10**6))
    statuses = precise_orbits.availability(datetime(2021, 6, 15), tmp_path)

    assert [s.label for s in statuses] == ["COD0MGXFIN", "IGS0OPSFIN", "IGS0OPSRAP"]
    assert all(s.used and s.available and s.size_bytes == 10**6 for s in statuses)
    # Finals first, rapid last: a date old enough to have a final must get the
    # final, not the coarser product that also happens to exist.
    tried = precise_orbits._PRODUCTS + precise_orbits._RAPID_PRODUCTS
    assert [Path(t).name.split("_")[0] for t in tried] == [s.label for s in statuses]
    assert all("FIN" in t for t in precise_orbits._PRODUCTS)


def test_availability_does_not_probe_what_is_already_cached(monkeypatch, tmp_path):
    """
    A cached file is reported without a request, and its size is the *compressed*
    one -- a caller comparing two days must not have one measured before
    decompression and the other after.
    """
    probed = []

    def record(url):
        probed.append(url)
        return False, "not published for this date", None

    monkeypatch.setattr(precise_orbits.cddis, "exists", record)
    day = datetime(2021, 6, 15)
    url, compressed, decompressed = precise_orbits.sp3_paths(
        day, tmp_path, precise_orbits._PRODUCTS[0]
    )
    decompressed.parent.mkdir(parents=True, exist_ok=True)
    decompressed.write_text("* an SP3 file\n")
    compressed.write_bytes(b"\x1f\x8b" + b"\0" * 998)

    first = precise_orbits.availability(day, tmp_path, include_rapid=False)[0]
    assert first.available and first.detail == "cached locally"
    assert first.size_bytes == 1000 != decompressed.stat().st_size
    # The other products are still reported, so the cached one is the only URL
    # that must be absent from the probes.
    assert url not in probed and probed


def test_a_short_daily_broadcast_file_is_visible_as_a_size(monkeypatch, tmp_path):
    """
    The `brdc` merge for a day still in progress is present but short, and nothing
    else announces it: the records simply stop before the collect epoch.  So the
    size has to reach the caller as a number, not only inside a display string.
    """
    monkeypatch.setattr(
        broadcast_ephemeris.cddis, "exists", lambda url: (True, "39 kB", 38_929)
    )
    status = broadcast_ephemeris.availability(datetime(2026, 9, 2), tmp_path)
    assert status.available and status.size_bytes == 38_929
    assert status.label == "brdc 2026-09-02"
