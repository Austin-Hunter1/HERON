#!/usr/bin/env python3
"""
Compare the BeiDou B2a or B1C ephemerides decoded from a tracking run with precise orbits.

Run this on a machine that can reach CDDIS and has an Earthdata login in `.env`
(D-054).  The cloud session that wrote it could not, so this script is exercised by
unit tests on synthetic files but has not been run against real archive files.

    python scripts/compare_bds_orbits.py TRACKING_FILE.h5 --date 2026-10-14
    python scripts/compare_bds_orbits.py TRACKING_FILE.h5 --date 2026-10-14 --rinex BRDC.rnx

What it does, per satellite in the tracking file:

1. decodes the B-CNAV2 (B2a) or B-CNAV1 (B1C) frames and assembles the ephemeris
   (D-055 for B2a);
2. compares that orbit with the SP3 orbit, every 5 minutes within +/- SPAN hours of
   its `toe`;
3. if a RINEX 3 broadcast file is given or downloadable, picks that file's record for
   the same time and compares it with the SP3 orbit and with the decoded one.

All times are converted from BDT to GPST for SP3 (`utils.bds_orbit_check`).  The
output is a table of metres: the value at `toe`, the RMS over the span, and the maximum.
A broadcast ephemeris is good to a few metres near `toe`.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils import bds_orbit_check, precise_orbits, rinex3_nav, tracking_io  # noqa: E402
from utils import environment_variables  # noqa: E402
from utils.nav import bcnav1, bcnav2, symbols  # noqa: E402

# The decoder module of each BeiDou signal.  Both have `decode` and `assemble_ephemeris`.
DECODERS = {"BDS_B2A": bcnav2, "BDS_B1C": bcnav1}


def decoded_ephemerides(tracking_file: Path):
    """`{sat_id: BdsEphemeris}` for every satellite in the tracking file that decodes."""
    run = tracking_io.load_tracking_results(tracking_file)
    decoder = DECODERS.get(run.signal_type_id)
    if decoder is None:
        raise SystemExit(
            f"{tracking_file} holds {run.signal_type_id} tracking, not one of {sorted(DECODERS)}"
        )
    out = {}
    for sat_id, result in sorted(run.results.items()):
        stream = symbols.extract(result.outputs, run.signal_type_id)
        decoded = decoder.decode(stream.soft, expected_prn=int(sat_id[1:]))
        ephemeris = decoder.assemble_ephemeris(decoded)
        if ephemeris is None:
            print(f"{sat_id}: no complete ephemeris decoded ({len(decoded.frames)} frames)")
            continue
        out[sat_id] = ephemeris
    return out


def load_sp3(day: datetime, first_gps_s: float, last_gps_s: float):
    """The SP3 arrays covering a window, fetched the way notebook 02 fetches them."""
    from gnss_tools.orbits.sp3_utils import download_and_parse_sp3_data

    for offset in (-1, 0, 1):
        try:
            precise_orbits.download_sp3(day + timedelta(days=offset))
        except Exception as error:  # a missing neighbour day is not fatal
            print(f"SP3 for {day + timedelta(days=offset):%Y-%m-%d}: {error}")
    resources = str(environment_variables.get_resources_path())
    return download_and_parse_sp3_data(first_gps_s, last_gps_s, resources)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tracking_file", type=Path)
    parser.add_argument("--date", required=True, help="collect date, YYYY-MM-DD (UTC)")
    parser.add_argument("--span-hours", type=float, default=1.0, help="compare within +/- this of toe")
    parser.add_argument("--step-s", type=float, default=300.0)
    parser.add_argument("--rinex", type=Path, help="RINEX 3 broadcast file; omit to download the day's")
    parser.add_argument("--no-rinex", action="store_true")
    args = parser.parse_args()

    day = datetime.strptime(args.date, "%Y-%m-%d")
    ephemerides = decoded_ephemerides(args.tracking_file)
    if not ephemerides:
        raise SystemExit("no ephemeris could be decoded")

    offsets = np.arange(-args.span_hours * 3600.0, args.span_hours * 3600.0 + 1.0, args.step_s)
    first = min(bds_orbit_check.to_gps_seconds(e.week, e.toe) for e in ephemerides.values())
    sp3 = load_sp3(day, first + offsets[0] - 600.0, first + offsets[-1] + 600.0)

    rinex = {}
    if not args.no_rinex:
        try:
            rinex, skipped = (
                rinex3_nav.load_bds_ephemerides(args.rinex) if args.rinex else rinex3_nav.load_day(day)
            )
            print(f"RINEX: {sum(len(v) for v in rinex.values())} BeiDou records, GEO skipped: {len(skipped)}")
        except Exception as error:
            print(f"RINEX not available ({error}); comparing with SP3 only")

    header = f"{'sat':4} {'toe s':>7} | decoded-SP3 (m): {'at toe':>7} {'rms':>7} {'max':>7}"
    if rinex:
        header += f" | RINEX-SP3 rms | decoded-RINEX at toe, rms"
    print(header)
    for sat_id, eph in ephemerides.items():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            versus_sp3 = bds_orbit_check.compare_with_sp3(eph, sp3, offsets)
        line = f"{sat_id:4} {eph.toe:7.0f} | {'':16} {versus_sp3.at_toe_m:7.2f} {versus_sp3.rms_m:7.2f} {versus_sp3.max_m:7.2f}"
        record = rinex3_nav.select_ephemeris(rinex.get(sat_id, []), eph.week, eph.toe) if rinex else None
        if rinex and record is not None:
            rinex_vs_sp3 = bds_orbit_check.compare_with_sp3(record, sp3, offsets)
            decoded_vs_rinex = bds_orbit_check.compare_ephemerides(eph, record, offsets)
            line += f" | {rinex_vs_sp3.rms_m:12.2f} | {decoded_vs_rinex.at_toe_m:7.2f} {decoded_vs_rinex.rms_m:7.2f}"
        elif rinex:
            line += " | (no RINEX record near toe)"
        print(line)


if __name__ == "__main__":
    main()
