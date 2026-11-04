"""
Let the geometry of the satellites correct a satellite whose code phase is off by
whole code periods, instead of throwing that satellite away.

THE PROBLEM
-----------
A tracking channel's cumulative code phase is only known up to whole code
periods (1 ms for GPS L1 C/A, 20 ms for GPS L5).  `utils.tracking_supervisor`
and `utils.gap_consensus` settle this inside the tracker, and the navigation-
message anchor settles it once per satellite, but none of them can SEE a wrong
answer: they work from the code phase alone.  What a wrong answer does to a
position solve is large and one-sided -- one satellite whose pseudorange is off
by c x 20 ms = 6000 km.  `navigation.solve_epoch_with_raim` can only exclude
that satellite.  Excluding it costs redundancy, and when several satellites
are off together (two groups anchored a period apart) there may be nothing
left to exclude them against.

THE IDEA
--------
The other satellites' geometry already says what range each satellite SHOULD
have, to a few tens of metres.  An error of a whole period is thousands of
kilometres.  So for each satellite, ask which of `-m .. +m` whole periods added
to its code phase makes the whole set fit best.  Correct combinations leave
residuals of metres; every wrong one leaves residuals of hundreds of
kilometres.  This is the integer-ambiguity search of carrier-phase positioning,
at a far wider spacing.

HOW IT STAYS SAFE
-----------------
- Only the DIFFERENCES between satellites can be seen.  A shift every satellite
  shares is a receiver clock offset, which the position solve estimates anyway,
  so one satellite is held at zero and the rest are searched relative to it.
- A result is used only when it is both GOOD (the best fit's RMS residual is
  under `accept_rms_m`) and UNIQUE (the next-best combination is at least
  `min_margin` times worse).  Otherwise the run is left exactly as it was.
- It needs at least `min_satellites` (default 6): with four unknowns, six gives
  two degrees of redundancy, the least that can tell a wrong combination from
  a right one at all.
- Several epochs of each run are searched independently and must agree.
- The search runs once per RUN of epochs that all have the same satellites
  (so, between holes in any channel's record), not once per epoch, and the
  combinations are scored together as one matrix product rather than by
  solving each one.

THE LINEARISATION
-----------------
Scoring every combination with `navigation.solve_epoch` would take minutes.
Instead the geometry is linearised once at the a-priori position: the post-fit
residuals of any pseudorange vector `y` are `P y`, where `P` projects out the
four unknowns (position and clock).  A shift of `k` periods changes `y` by a
known constant per satellite, so every combination's residuals follow from one
product.  The approximation is good to a few tens of metres for an a-priori
within a kilometre or so, which is far inside the gap between a right and a
wrong combination.  The satellite positions are taken at the UNSHIFTED transmit
time, which is off by up to ~20 ms of satellite motion (a few metres of range)
for a shifted satellite; `apply_period_shifts` then moves the transmit time, so
the real solve downstream uses the exact positions.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import numpy as np

from . import navigation
from . import observables as obs
from .nav.ephemeris import SPEED_OF_LIGHT


@dataclass
class PeriodSearchConfig:
    """Tuning for the search.  `period_ms` has no default: it belongs to the signal."""

    period_ms: float

    # Search `-m .. +m` whole periods per satellite.  1 covers a satellite one
    # period either way, which is every case seen so far.
    max_shift_periods: int = 1

    # Fewest satellites in a run before it is searched.  Six is two degrees of
    # redundancy over the four unknowns; with fewer a wrong combination can fit
    # as well as the right one.
    min_satellites: int = 6

    # Epochs searched per run (spread across it).  All of them must pick the
    # same combination for the run to be changed.
    probes_per_run: int = 3

    # The best combination's RMS post-fit residual must be below this, in
    # metres.  Single-frequency pseudoranges fit to ~10-20 m; the linearisation
    # and the unshifted satellite positions add a few more.
    accept_rms_m: float = 60.0

    # The second-best combination's RMS must be at least this many times the best.
    min_margin: float = 3.0

    # Upper bound on combinations scored per epoch.  (2 m + 1) ** (satellites - 1)
    # grows quickly; a run that would exceed it is skipped, not truncated.
    max_combinations: int = 500_000


@dataclass
class EpochSearch:
    """The best combination for one epoch, and how convincing it is."""

    shifts: np.ndarray
    """(n,) whole periods to add to each satellite's code phase; the first is 0."""
    rms_m: float
    second_rms_m: float

    @property
    def margin(self) -> float:
        return self.second_rms_m / max(self.rms_m, 1e-9)


def search_epoch(
    prefit_m: np.ndarray,
    unit_vectors: np.ndarray,
    config: PeriodSearchConfig,
) -> EpochSearch | None:
    """
    Score every combination of whole-period shifts for one epoch.

    `prefit_m` is (n,) corrected pseudorange minus modelled range at the
    a-priori position; `unit_vectors` is (n, 3), receiver to satellite.  The
    first satellite is held at zero.  Returns None when the epoch has too few
    satellites, or the search would be too large.
    """
    n = len(prefit_m)
    if n < max(config.min_satellites, 6):
        return None
    m = config.max_shift_periods
    choices = 2 * m + 1
    if choices ** (n - 1) > config.max_combinations:
        return None

    geometry = np.hstack([-unit_vectors, np.ones((n, 1))])
    projector = np.eye(n) - geometry @ np.linalg.pinv(geometry)

    values = np.arange(-m, m + 1)
    grids = np.meshgrid(*([values] * (n - 1)), indexing="ij")
    free = np.stack([g.ravel() for g in grids], axis=1)
    combos = np.hstack([np.zeros((free.shape[0], 1), dtype=int), free])

    step_m = SPEED_OF_LIGHT * config.period_ms * 1e-3
    # Adding `k` periods to a satellite's code phase moves its transmit time
    # later and so makes its pseudorange SHORTER by k x step.
    residuals = (projector @ prefit_m)[None, :] - step_m * (combos @ projector)
    cost = np.einsum("ij,ij->i", residuals, residuals)

    two = np.argpartition(cost, 1)[:2]
    two = two[np.argsort(cost[two])]
    dof = n - 4
    return EpochSearch(
        shifts=combos[two[0]].copy(),
        rms_m=float(np.sqrt(cost[two[0]] / dof)),
        second_rms_m=float(np.sqrt(cost[two[1]] / dof)),
    )


@dataclass
class RunResult:
    """What happened to one run of epochs that share the same satellites."""

    first_epoch: int
    last_epoch: int
    sat_ids: list[str]
    shifts: dict[str, int] = field(default_factory=dict)
    rms_m: float = float("nan")
    margin: float = float("nan")
    status: str = ""  # "unchanged" | "shifted" | "unresolved"
    note: str = ""


@dataclass
class PeriodShiftResult:
    """Per-epoch, per-satellite whole-period shifts, plus a record of each run."""

    shifts: np.ndarray
    """(N, M) int; 0 everywhere the search did not change anything."""
    runs: list[RunResult]
    sat_ids: list[str]

    def table_rows(self, epoch_s: np.ndarray | None = None) -> list[list[str]]:
        rows = []
        for run in self.runs:
            if run.status == "unchanged":
                continue
            a, b = run.first_epoch, run.last_epoch
            span = (
                f"{epoch_s[a]:.1f}-{epoch_s[b]:.1f}" if epoch_s is not None else f"{a}-{b}"
            )
            moved = ", ".join(f"{s} {k:+d}" for s, k in sorted(run.shifts.items()) if k) or "-"
            rows.append([
                span, str(len(run.sat_ids)), moved,
                f"{run.rms_m:.1f}" if np.isfinite(run.rms_m) else "-",
                f"{run.margin:.1f}" if np.isfinite(run.margin) else "-",
                run.status + (f": {run.note}" if run.note else ""),
            ])
        return rows

    def count_shifted(self) -> int:
        return int(sum(1 for r in self.runs if r.status == "shifted"))


def _runs_of_equal_availability(mask: np.ndarray) -> list[tuple[int, int]]:
    """(first, last) epoch of every maximal run whose rows of `mask` are equal."""
    n = mask.shape[0]
    if n == 0:
        return []
    changes = np.nonzero(np.any(mask[1:] != mask[:-1], axis=1))[0] + 1
    starts = np.concatenate([[0], changes])
    stops = np.concatenate([changes, [n]]) - 1
    return list(zip(starts.tolist(), stops.tolist()))


def _probe_epochs(first: int, last: int, count: int) -> list[int]:
    """Up to `count` distinct epochs spread across [first, last], ends avoided."""
    length = last - first + 1
    count = min(count, length)
    fractions = (np.arange(count) + 1) / (count + 1)
    return sorted({first + int(round(f * (length - 1))) for f in fractions})


def resolve_period_shifts(
    observables: obs.Observables,
    ephemerides: dict,
    *,
    signal_type_id: str,
    a_priori_ecef_m: np.ndarray,
    config: PeriodSearchConfig,
    settings: navigation.CorrectionSettings | None = None,
    iono_alpha: tuple[float, float, float, float] | None = None,
    iono_beta: tuple[float, float, float, float] | None = None,
) -> PeriodShiftResult:
    """
    Find, for every run of epochs sharing the same satellites, the whole-period
    shifts that make the satellites agree.  See the module docstring.

    `a_priori_ecef_m` should be good to a kilometre or so (the notebook's
    reference site is).  Nothing is changed here; `apply_period_shifts` does that.
    """
    settings = settings or navigation.CorrectionSettings()
    mask = observables.valid_mask()
    n_epochs, n_sats = mask.shape
    a_priori = np.asarray(a_priori_ecef_m, dtype=float)

    runs = _runs_of_equal_availability(mask)
    result = PeriodShiftResult(
        shifts=np.zeros((n_epochs, n_sats), dtype=int),
        runs=[],
        sat_ids=list(observables.sat_ids),
    )

    # One pass of the (slow) corrections over just the probed epochs.
    probes: dict[int, list[int]] = {}
    for first, last in runs:
        if mask[first].sum() >= config.min_satellites:
            probes[first] = _probe_epochs(first, last, config.probes_per_run)
    all_probe_epochs = sorted({e for p in probes.values() for e in p})
    prefit: dict[int, np.ndarray] = {}
    units: dict[int, np.ndarray] = {}
    if all_probe_epochs:
        index = np.array(all_probe_epochs)
        sub = dataclasses.replace(
            observables,
            epoch_uptime_ms=observables.epoch_uptime_ms[index],
            receive_time_s=observables.receive_time_s[index],
            transmit_time_s=observables.transmit_time_s[index],
            pseudorange_m=observables.pseudorange_m[index],
        )
        corrected, _ = navigation.correct_pseudoranges(
            sub, ephemerides, signal_type_id=signal_type_id, settings=settings,
            receiver_position_ecef_m=a_priori, iono_alpha=iono_alpha, iono_beta=iono_beta,
        )
        positions = navigation.satellite_positions(
            sub, ephemerides, apply_sagnac=settings.sagnac,
            receiver_position_ecef_m=a_priori,
        )
        for row, epoch in enumerate(all_probe_epochs):
            vectors = positions[row] - a_priori
            ranges = np.linalg.norm(vectors, axis=1)
            prefit[epoch] = corrected[row] - ranges
            units[epoch] = vectors / ranges[:, None]

    for first, last in runs:
        used = np.nonzero(mask[first])[0]
        run = RunResult(first, last, [observables.sat_ids[j] for j in used])
        result.runs.append(run)
        if first not in probes:
            run.status = "unchanged"
            run.note = f"only {len(used)} satellite(s)"
            continue

        searches = []
        for epoch in probes[first]:
            finite = np.isfinite(prefit[epoch][used]) & np.isfinite(units[epoch][used, 0])
            if not finite.all():
                continue
            found = search_epoch(prefit[epoch][used], units[epoch][used], config)
            if found is not None:
                searches.append(found)
        if len(searches) < len(probes[first]):
            run.status = "unchanged"
            run.note = "search not possible (too many combinations or bad geometry)"
            continue

        best = searches[0]
        run.rms_m = max(s.rms_m for s in searches)
        run.margin = min(s.margin for s in searches)
        agree = all(np.array_equal(s.shifts, best.shifts) for s in searches)
        good = run.rms_m <= config.accept_rms_m
        unique = run.margin >= config.min_margin
        if not agree:
            run.status = "unresolved"
            run.note = "epochs disagree"
        elif not good:
            run.status = "unresolved"
            run.note = f"best fit {run.rms_m:.0f} m is over {config.accept_rms_m:g} m"
        elif not unique:
            run.status = "unresolved"
            run.note = f"margin {run.margin:.1f} is under {config.min_margin:g}"
        elif not best.shifts.any():
            run.status = "unchanged"
        else:
            run.status = "shifted"
            run.shifts = {observables.sat_ids[j]: int(k) for j, k in zip(used, best.shifts)}
            result.shifts[first : last + 1, used] = best.shifts[None, :]
    return result


def apply_period_shifts(
    observables: obs.Observables, shifts: np.ndarray, period_ms: float
) -> obs.Observables:
    """
    A copy of `observables` with `shifts[i, j]` whole periods added to satellite
    `j`'s code phase at epoch `i`: its transmit time moves later by that many
    periods, and its pseudorange shorter by the same time at the speed of light.
    """
    shifts = np.asarray(shifts)
    if shifts.shape != observables.pseudorange_m.shape:
        raise ValueError(
            f"shifts is {shifts.shape}, observables are {observables.pseudorange_m.shape}"
        )
    delta_s = shifts * period_ms * 1e-3
    return dataclasses.replace(
        observables,
        transmit_time_s=observables.transmit_time_s + delta_s,
        pseudorange_m=observables.pseudorange_m - SPEED_OF_LIGHT * delta_s,
    )
