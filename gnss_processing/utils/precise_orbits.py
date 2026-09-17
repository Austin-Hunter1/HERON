"""
IGS precise orbits (SP3), as an independent check on the broadcast ephemeris.

The broadcast ephemeris is what a receiver actually has, so it is what the
navigation solution uses.  Precise orbits are what the truth turned out to be --
a few centimetres, computed after the fact from a global station network -- so the
difference between them is the part of the position error that arrived with the
signal rather than being made by the receiver.

`gnss_tools.orbits.parse_sp3` reads the file correctly and that half is used here.
Two other halves of the upstream module are not:

  * Its *download* cannot work.  `download_and_decompress_sp3_file` calls
    `http_download` with no credentials at all, and CDDIS has required an Earthdata
    login for years.  So this module fetches the file itself -- through
    `utils.cddis`, which handles the redirect that strips them -- and places it at
    exactly the path `sp3_utils` looks in, so the upstream parser then finds it
    already present and skips its own download.

  * Its *interpolation* is a smoothing spline that does not pass through the SP3
    nodes -- 1.7 m RMS away from them on a real file, which is the size of the
    broadcast error one would use SP3 to measure.  `interpolate_position_m` below
    uses Lagrange instead, and `interpolate_clock_s` uses linear, for reasons each
    states.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from gnss_tools.misc.data_utils import decompress, format_filepath

from . import cddis, environment_variables

# Two products, in preference order.  CODE's MGEX solution covers every
# constellation at 5-minute sampling; the IGS operational solution is
# GPS+GLONASS at 15 minutes and is a tenth the size.  Either is far better than
# broadcast for this purpose.
_PRODUCTS = (
    "gnss/products/{wwww}/COD0MGXFIN_{yyyy}{ddd}0000_01D_05M_ORB.SP3.gz",
    "gnss/products/{wwww}/IGS0OPSFIN_{yyyy}{ddd}0000_01D_15M_ORB.SP3.gz",
)

# Tried only after both finals have been refused, so a collect younger than the
# final latency still gets a check instead of nothing.  Rapid lags the observation
# by about a day against 12-21 days for a final, and costs roughly 2.5 cm against a
# final's few cm -- immaterial next to the 1-2 m of broadcast error this is used to
# measure, and the reason it is a fallback rather than a preference is only that a
# final exists for every date eventually while the ordering here does not change.
#
# Ultra-rapid is deliberately absent: its name carries an hour field and spans 02D,
# so it does not fit this one-day template, and its second half is prediction rather
# than estimate -- which is not truth to measure a prediction against.
_RAPID_PRODUCTS = (
    "gnss/products/{wwww}/IGS0OPSRAP_{yyyy}{ddd}0000_01D_15M_ORB.SP3.gz",
)

# Where `gnss_tools.orbits.sp3_utils` expects to find its cache.  Matching this
# exactly is the whole trick: put the file here and the upstream loader will not
# try to download it.
_SP3_CACHE_SUBDIR = "cddis"


def sp3_paths(day: datetime, resources_dir: str | Path, template: str) -> tuple[str, Path, Path]:
    """`(url, compressed_path, decompressed_path)` for one product on one day."""
    relative = format_filepath(template, day)
    # sp3_utils builds its own paths from a "gps/products/..." template rooted at
    # <resources>/cddis/, so mirror that layout whatever URL prefix is used.
    cache_relative = relative.replace("gnss/products", "gps/products", 1)
    compressed = Path(resources_dir) / _SP3_CACHE_SUBDIR / cache_relative
    return (
        f"{cddis.CDDIS_ARCHIVE_URL}/{relative}",
        compressed,
        compressed.with_suffix(""),
    )


def download_sp3(
    day: datetime,
    resources_dir: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """
    Fetch and decompress one day of precise orbits, returning the local path.

    Tries each product in turn, finals first and rapid last.  A given day may be
    missing several -- CODE and IGS publish on different latencies, older weeks use
    different naming, and no final exists at all until a couple of weeks after the
    observation -- so failing over is what makes a recent collect checkable.
    """
    if resources_dir is None:
        resources_dir = environment_variables.get_resources_path()

    failures = []
    # Finals first, then rapid: a date old enough to have a final gets it, and a
    # recent one falls through to the product that exists rather than raising.
    for template in _PRODUCTS + _RAPID_PRODUCTS:
        url, compressed, decompressed = sp3_paths(day, resources_dir, template)

        # A cache hit has to be a *usable* file, not merely a present one.  Two
        # kinds of residue get left behind by a failed Earthdata login, and both
        # otherwise fail forever with a gzip error that names no cause: an HTML
        # page written where the archive was expected, and the empty file that
        # decompressing it produces.  Treat either as a miss.
        if decompressed.exists() and decompressed.stat().st_size > 0 and not overwrite:
            return decompressed
        poisoned = compressed.exists() and compressed.read_bytes()[:2] != b"\x1f\x8b"
        if poisoned:
            decompressed.unlink(missing_ok=True)

        try:
            cddis.download(url, compressed, overwrite=overwrite or poisoned)
            decompress(str(compressed), str(decompressed))
            if decompressed.exists() and decompressed.stat().st_size > 0:
                return decompressed
            failures.append(f"{compressed.name}: decompressed to an empty file")
        except Exception as exc:
            failures.append(f"{compressed.name}: {type(exc).__name__}: {exc}")

    raise RuntimeError(
        f"No precise orbit product available for {day.date()}. Tried:\n  "
        + "\n  ".join(failures)
    )


def availability(
    day: datetime,
    resources_dir: str | Path | None = None,
    *,
    include_rapid: bool = True,
) -> list[cddis.Availability]:
    """
    What CDDIS holds for `day`, without downloading any of it.

    Exists because `download_sp3` can only report a failure after trying, and for a
    recent collect the failure is the *expected* answer -- final products lag the
    observation by a couple of weeks.  Asking first turns "the notebook threw" into
    "the finals are not out yet, come back in three weeks", which is a different
    thing to read at the bottom of a cell.

    A cached file is reported as available without touching the network, so a
    second run of a notebook cell is free.  `include_rapid` covers the rapid
    fallback as well as the finals, which is what `download_sp3` itself tries;
    turning it off asks the narrower question of whether a final exists yet.
    """
    if resources_dir is None:
        resources_dir = environment_variables.get_resources_path()

    out: list[cddis.Availability] = []
    for template, used in [(t, True) for t in _PRODUCTS] + (
        [(t, True) for t in _RAPID_PRODUCTS] if include_rapid else []
    ):
        url, compressed, decompressed = sp3_paths(day, resources_dir, template)
        label = Path(template).name.split("_")[0]
        if decompressed.exists() and decompressed.stat().st_size > 0:
            # The *compressed* size, because that is what a probe of the archive
            # reports -- a caller comparing two days must not have one of them
            # measured before decompression and the other after.
            size = compressed.stat().st_size if compressed.exists() else None
            out.append(
                cddis.Availability(label, url, True, "cached locally", size, used)
            )
            continue
        available, detail, size = cddis.exists(url)
        out.append(cddis.Availability(label, url, available, detail, size, used))
    return out


def load_sp3(day: datetime, resources_dir: str | Path | None = None):
    """
    `(epochs, records)` for one day, via `gnss_tools.orbits.parse_sp3`.

    The file is fetched here first so the upstream loader finds it cached.
    """
    from gnss_tools.orbits.parse_sp3 import parse_sp3_file

    path = download_sp3(day, resources_dir)
    return parse_sp3_file(str(path))


# --------------------------------------------------------------------------
# Interpolating an SP3 file onto the epochs a receiver actually measured at.
# --------------------------------------------------------------------------

# SP3 states a position every 5 or 15 minutes; a receiver wants one at an
# arbitrary instant between two of them.  Order 10 over the nodes bracketing that
# instant is the long-standing convention (IGS recommends 9-11), and the reason it
# works is that an orbit over an hour is very nearly a polynomial -- there is no
# manoeuvre and no drag to speak of at 20,200 km.
LAGRANGE_ORDER = 10


def interpolate_position_m(sp3, sat_id: str, times_gps_s, *, order: int = LAGRANGE_ORDER):
    """
    Precise position, `(N, 3)` metres in ECEF, at `times_gps_s`.

    Lagrange rather than the smoothing spline `sp3_utils.compute_splines_from_sp3_dict`
    builds, and the difference is not academic.  That spline is
    `UnivariateSpline(..., k=5)` with scipy's default smoothing factor, fitted
    across a whole day at once -- so it does not pass through the SP3 nodes at all.
    Measured on one day of a 5-minute MGEX file it misses the very points it was
    fitted to by 1.7 m RMS and up to 5 m, which is the same size as the broadcast
    ephemeris error anyone would be using it to measure.  Lagrange over a local
    window reproduces those nodes to 0.013 m RMS, and its error away from them is
    the interpolation error of a polynomial through an orbit -- centimetres.

    Returns NaN where the requested time has no window of nodes around it, which is
    what `compute_array_lagrange_interpolation` does at the ends of the file.
    """
    import numpy as np
    from gnss_tools.orbits.array_lagrange_interpolation import (
        compute_array_lagrange_interpolation,
    )

    times = np.atleast_1d(np.asarray(times_gps_s, dtype=float))
    nodes = np.asarray(sp3.epochs, dtype=float)
    positions = np.asarray(sp3.position[sat_id], dtype=float)
    # `compute_array_lagrange_interpolation` drops to a 0-d array for a single
    # time, so each axis is reshaped rather than stacked as it comes back: one
    # instant and a hundred should differ in length, not in rank.
    axes = [
        np.reshape(
            compute_array_lagrange_interpolation(times, nodes, positions[:, axis], order),
            len(times),
        )
        for axis in range(3)
    ]
    return np.stack(axes).T


def interpolate_clock_s(sp3, sat_id: str, times_gps_s):
    """
    Precise satellite clock offset, `(N,)` seconds, at `times_gps_s`.

    Linear, deliberately, where the position is a degree-10 polynomial.  An orbit
    is smooth because physics makes it smooth; a satellite clock is a steered
    oscillator that is occasionally jumped, and a high-order fit through a step
    rings on both sides of it -- turning one bad epoch into several.

    SP3 states the clock in microseconds and marks a missing one as 999999.999999,
    which the parser has already turned into NaN.  Those are dropped before
    interpolating rather than propagated, so one absent epoch costs accuracy near
    itself instead of erasing every value that touches it.  A satellite with fewer
    than two usable clock samples returns all-NaN.
    """
    import numpy as np

    times = np.atleast_1d(np.asarray(times_gps_s, dtype=float))
    samples = sp3.clock.get(sat_id)
    if samples is None:
        return np.full(len(times), np.nan)
    samples = np.asarray(samples, dtype=float)
    usable = np.isfinite(samples)
    if usable.sum() < 2:
        return np.full(len(times), np.nan)
    nodes = np.asarray(sp3.epochs, dtype=float)[usable]
    return np.interp(times, nodes, samples[usable]) * 1e-6
