"""
Make the channels of one recording agree on how much time its holes dropped.

THE PROBLEM
-----------
When samples go missing from the recording (a USRP overflow), every channel
tracking that file loses lock and is re-acquired by
`utils.tracking_supervisor`.  The re-acquisition measures the dropped time only
modulo the PRN code's repeat period A (1 ms for GPS L1 C/A, 20 ms for GPS L5),
and `try_reacquire` unwraps it into the window +/- A/2 on its own.  When a real
gap is near or past A/2, that window picks the wrong branch: the new segment's
code phase is off by a whole number of periods, which is c x A of pseudorange
(300 km on L1 C/A, 6000 km on L5).

WHY THE CHANNELS CAN FIX EACH OTHER
-----------------------------------
All channels read the SAME samples, so they all lost the same stretch of time.
If two channels bracket the same overflows, the dropped time they measure must
be the same number.  Channels do not bracket an overflow identically, though:
one may be re-acquired once after two overflows while another is re-acquired
between them.  The individual measurements then differ, but their SUMS over
the whole episode still agree modulo A, so the sums are what this module
compares.

WHAT IT CHANGES, AND WHY THAT IS SAFE
-------------------------------------
Only whole multiples of A.  A code phase is a label for where in the code the
signal is; adding a whole period to the label leaves the replica the loops track
unchanged, and the loops' fractional phase is never touched.  A shift that all
channels receive equally is a jump in the receiver clock bias, which the
position solve estimates anyway.  So the goal is only that the channels share
ONE branch, not that the branch is the physically true one.

Anything the vote cannot settle is left alone and reported, never guessed:

- a channel whose episode total disagrees with the majority modulo A (a
  different physical measurement, so no whole-period shift fixes it);
- a tie between two groups of equal size that do not agree modulo A;
- an episode with fewer than `min_votes` channels in agreement.

Segments that sit BETWEEN two holes of one episode (a channel that was
re-acquired in the middle of a burst of overflows) are dropped, but only for a
channel the vote had to move or could not place: the vote checks the episode's
total, so for such a channel their partial sums cannot be vouched for.  A
dropped segment is just a longer hole downstream.

The decision logic (`resolve_episode`, `find_episodes`) works on plain `Hole`
records so it can be tested without a tracking run; `holes_from_supervisors`
and `apply_to_supervisors` are the thin adapters to the live objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class GapConsensusConfig:
    """Tuning for the cross-channel vote."""

    # Two holes belong to the same episode when their time spans, each widened
    # by this much on both sides, overlap.  The spans of one overflow overlap
    # anyway (every channel is down at the same moment), so 0 is enough, and a
    # wide pad is harmful: it chains neighbouring overflows into one long
    # episode, and a long episode is compared only by its total.
    episode_pad_ms: float = 0.0

    # Two episode totals "agree" when they match modulo the code period to
    # within this many ms.  Real agreement is far tighter than this (a few
    # microseconds on the recorded collects); it only has to stay well under
    # one chip of code phase.
    agreement_tol_ms: float = 0.05

    # Fewest channels that must agree before any of them is shifted.  With one
    # voter there is nothing to compare against.
    min_votes: int = 2


@dataclass(frozen=True)
class Hole:
    """
    One re-acquisition of one channel.

    `segment_index` is the index of the segment that STARTS after the hole (so
    the hole sits between segment `segment_index - 1` and `segment_index`).
    `gap_ms` is the dropped time `try_reacquire` seeded, known modulo
    `ambiguity_ms`.  `start_ms` / `end_ms` bound the hole in stream time.
    """

    signal_id: str
    segment_index: int
    start_ms: float
    end_ms: float
    gap_ms: float
    ambiguity_ms: float


@dataclass
class EpisodeResult:
    """What the vote decided for one episode."""

    start_ms: float
    end_ms: float
    holes: list[Hole]
    # signal_id -> whole-period shift (ms) for every segment from
    # `first_shifted_segment[signal_id]` onward.
    shift_ms: dict[str, float] = field(default_factory=dict)
    first_shifted_segment: dict[str, int] = field(default_factory=dict)
    # signal_id -> segment indices to leave out of the merged record.
    dropped_segments: dict[str, list[int]] = field(default_factory=dict)
    # signal_ids whose total disagrees with the majority modulo A.
    outliers: list[str] = field(default_factory=list)
    # Why nothing was changed, when nothing was.  Empty when the vote ran.
    note: str = ""
    total_ms: dict[str, float] = field(default_factory=dict)


def find_episodes(holes: list[Hole], pad_ms: float) -> list[list[Hole]]:
    """
    Group holes into episodes: chains of holes whose (padded) time spans
    overlap, in time order.  Each episode is one burst of overflows as seen by
    every channel that lost lock in it.
    """
    ordered = sorted(holes, key=lambda h: (h.start_ms, h.signal_id))
    episodes: list[list[Hole]] = []
    reach = -np.inf
    for hole in ordered:
        if episodes and hole.start_ms - pad_ms <= reach:
            episodes[-1].append(hole)
            reach = max(reach, hole.end_ms + pad_ms)
        else:
            episodes.append([hole])
            reach = hole.end_ms + pad_ms
    return episodes


def _circular_distance(a: float, b: float, period: float) -> float:
    d = (a - b) % period
    return min(d, period - d)


def resolve_episode(episode: list[Hole], config: GapConsensusConfig) -> EpisodeResult:
    """
    Vote one episode.  See the module docstring for the rule and its limits.
    """
    result = EpisodeResult(
        start_ms=min(h.start_ms for h in episode),
        end_ms=max(h.end_ms for h in episode),
        holes=list(episode),
    )

    periods = {h.ambiguity_ms for h in episode}
    if len(periods) != 1:
        result.note = f"mixed code periods {sorted(periods)} ms; not compared"
        return result
    period = periods.pop()

    by_channel: dict[str, list[Hole]] = {}
    for hole in sorted(episode, key=lambda h: h.segment_index):
        by_channel.setdefault(hole.signal_id, []).append(hole)

    totals = {sid: sum(h.gap_ms for h in holes) for sid, holes in by_channel.items()}
    result.total_ms = dict(totals)
    if len(totals) < config.min_votes:
        result.note = f"only {len(totals)} channel(s) in this episode; nothing to compare"
        return result

    # Largest set of channels whose totals agree modulo the period.
    sids = sorted(totals)
    clusters = []
    for center in sids:
        members = [
            s for s in sids
            if _circular_distance(totals[s], totals[center], period) <= config.agreement_tol_ms
        ]
        clusters.append(frozenset(members))
    best_size = max(len(c) for c in clusters)
    winners = {c for c in clusters if len(c) == best_size}
    if len(winners) > 1:
        result.note = (
            f"{len(winners)} groups of {best_size} channel(s) disagree modulo "
            f"{period:g} ms; no majority"
        )
        return result
    members = sorted(next(iter(winners)))
    if len(members) < config.min_votes:
        result.note = f"no two channels agree modulo {period:g} ms"
        return result
    result.outliers = [s for s in sids if s not in members]

    # Members agree modulo the period, so they differ by a whole number of
    # periods.  Count how many sit on each branch and move the minority onto
    # the most common one.  A tie goes to the branch whose total is closest to
    # zero; which branch wins does not matter physically (see the docstring).
    reference = totals[members[0]]
    branch = {s: int(round((totals[s] - reference) / period)) for s in members}
    counts: dict[int, int] = {}
    for n in branch.values():
        counts[n] = counts.get(n, 0) + 1
    top = max(counts.values())
    candidates = [n for n, c in counts.items() if c == top]
    chosen = min(candidates, key=lambda n: abs(reference + n * period))
    for s in members:
        shift = (chosen - branch[s]) * period
        if shift != 0.0:
            result.shift_ms[s] = shift
            result.first_shifted_segment[s] = by_channel[s][-1].segment_index

    # A segment between two holes of one channel carries a partial sum the vote
    # does not check, only the total.  For a channel the vote had to move, or
    # could not place, that partial sum is suspect (the wrong wrap may sit in
    # either hole), so leave the segment out.  A channel that already agrees
    # keeps its in-between segments.
    for s in set(result.shift_ms) | set(result.outliers):
        holes = by_channel[s]
        if len(holes) > 1:
            result.dropped_segments[s] = list(
                range(holes[0].segment_index, holes[-1].segment_index)
            )
    return result


@dataclass
class ConsensusReport:
    """Everything `apply_to_supervisors` decided, for printing."""

    episodes: list[EpisodeResult]

    def shifted(self) -> dict[str, list[tuple[float, float]]]:
        """signal_id -> [(episode start ms, shift ms), ...]"""
        out: dict[str, list[tuple[float, float]]] = {}
        for ep in self.episodes:
            for sid, shift in ep.shift_ms.items():
                out.setdefault(sid, []).append((ep.start_ms, shift))
        return out

    def table_rows(self) -> list[list[str]]:
        rows = []
        for ep in self.episodes:
            channels = sorted({h.signal_id for h in ep.holes})
            shifts = ", ".join(f"{s} {v:+g}" for s, v in sorted(ep.shift_ms.items())) or "-"
            dropped = ", ".join(
                f"{s} x{len(v)}" for s, v in sorted(ep.dropped_segments.items())
            ) or "-"
            outliers = ", ".join(ep.outliers) or "-"
            rows.append([
                f"{ep.start_ms / 1e3:.2f}-{ep.end_ms / 1e3:.2f}",
                str(len(channels)),
                shifts,
                dropped,
                outliers,
                ep.note or "ok",
            ])
        return rows


def resolve(holes: list[Hole], config: GapConsensusConfig) -> list[EpisodeResult]:
    """Group `holes` into episodes and vote each one, in time order."""
    return [
        resolve_episode(ep, config)
        for ep in find_episodes(holes, config.episode_pad_ms)
    ]


# ---------------------------------------------------------------------------
# Adapters to the live supervisors
# ---------------------------------------------------------------------------


def holes_from_supervisors(supervisors: dict) -> list[Hole]:
    """
    One `Hole` per successful re-acquisition of every supervisor.

    A supervisor that never re-acquired (no hole) takes no part.  Neither does
    a hole whose bounds were not recorded.
    """
    holes = []
    for signal_id, sup in supervisors.items():
        if sup.ambiguity_ms is None:
            continue
        for k, (gap, bounds) in enumerate(
            zip(sup.implied_gap_ms_by_segment, sup.hole_bounds_ms_by_segment)
        ):
            holes.append(Hole(
                signal_id=signal_id,
                segment_index=k + 1,
                start_ms=float(bounds[0]),
                end_ms=float(bounds[1]),
                gap_ms=float(gap),
                ambiguity_ms=float(sup.ambiguity_ms),
            ))
    return holes


def apply_to_supervisors(
    supervisors: dict, config: GapConsensusConfig | None = None
) -> ConsensusReport:
    """
    Vote every episode and apply the result in place: add each decided
    whole-period shift to `code_phase_ms` of the channel's segments from the
    one after the episode onward, and mark dropped segments on the supervisor.

    Call once, after tracking, before `tracking_supervisor.build_merged_adapter`.
    """
    config = config or GapConsensusConfig()
    episodes = resolve(holes_from_supervisors(supervisors), config)
    for ep in episodes:
        for sid, shift in ep.shift_ms.items():
            sup = supervisors[sid]
            for index in range(ep.first_shifted_segment[sid], len(sup.segments)):
                outputs = sup.segments[index].outputs
                outputs.code_phase_ms[outputs.valid] += shift
        for sid, indices in ep.dropped_segments.items():
            supervisors[sid].dropped_segments.update(indices)
    return ConsensusReport(episodes)
