"""
Tests for utils/transmitting_prns.py: which PRNs notebook 01 searches (D-104).

The RINEX 4 text here is synthetic but in the real column layout, so the parser is
tested on the format it will meet.  No test touches the network: the download is
replaced where a test needs the RINEX 4 path.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from utils import signal_interfaces
from utils import transmitting_prns as tp

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 11, 10)


def orbit_line(*values: float | None) -> str:
    """One RINEX 4 'broadcast orbit' line: 4 spaces, then four 19-character fields."""
    return "    " + "".join(" " * 19 if v is None else f"{v:19.12e}" for v in values)


def eph(sat: str, message: str, lines: int = 8, isc: tuple | None = None) -> list[str]:
    """One ephemeris record.  For CNAV, `isc` fills the 7th orbit line (ISC_L1CA..ISC_L5Q5)."""
    body = [f"{sat} 2026 11 10 00 00 00" + orbit_line(0.0, 0.0, 0.0)[4:]]
    for n in range(1, lines + 1):
        body.append(orbit_line(*(isc if (n == 7 and isc is not None) else (1.0, 1.0, 1.0, 1.0))))
    return [f"> EPH {sat} {message}", *body]


HEADER = [
    "     4.02           NAVIGATION DATA     M                   RINEX VERSION / TYPE",
    "                                                            END OF HEADER",
]
FILE = HEADER + [
    *eph("G04", "LNAV"), *eph("G04", "CNAV", 9, (1e-10, 2e-9, 3e-10, 4e-10)),
    *eph("G05", "LNAV"), *eph("G05", "CNAV", 9, (1e-10, 2e-9, 0.0, 0.0)),
    *eph("G06", "LNAV"), *eph("G06", "CNAV", 9, (1e-10, 2e-9, None, 5e-10)),
    *eph("G16", "LNAV"),
    "> STO GPUT",
    "    stray line of a system-time record",
    *eph("C13", "D1"), *eph("C13", "CNV1"), *eph("C13", "CNV2"),
    *eph("C40", "CNV2"),
    *eph("E08", "INAV"), *eph("E08", "FNAV"),
]


def base_raw() -> dict:
    """A small valid config, as `yaml.safe_load` would give it."""
    return {
        "enabled": True,
        "fallback_to_all_prns": True,
        "rinex4": {"directory": "/gnss/data/daily/{yyyy}/brdc/",
                   "filename": "BRD400DLR_S_{yyyy}{ddd}0000_01D_MN.rnx.gz"},
        "signals": {
            "GPS_L1CA": {"source": "rinex4", "system": "G", "message": "LNAV"},
            "GPS_L2C": {"source": "rinex4", "system": "G", "message": "CNAV"},
            "GPS_L5": {"source": "rinex4", "system": "G", "message": "CNAV", "requires_l5_isc": True},
            "GPS_L1C": {"source": "table"},
            "BDS_B1C": {"source": "rinex4", "system": "C", "message": "CNV1"},
        },
        "tables": {"GPS_L1C": [
            {"prn": 4, "valid_from": date(2019, 1, 9), "valid_to": date(2019, 7, 13)},
            {"prn": 4, "valid_from": date(2019, 10, 21), "valid_to": None},
            {"prn": 13, "valid_from": date(2026, 4, 25), "valid_to": None},
        ]},
    }


# --- RINEX 4 parsing -----------------------------------------------------------


def test_parse_rinex4_reads_the_message_of_every_record():
    messages = tp.parse_rinex4(FILE)
    assert messages.messages["G04"] == {"LNAV", "CNAV"}
    assert messages.messages["G16"] == {"LNAV"}
    assert messages.messages["C13"] == {"D1", "CNV1", "CNV2"}
    assert messages.satellites("C", "CNV2") == {"C13", "C40"}
    assert messages.satellites("E", "FNAV") == {"E08"}
    # The STO record is not an ephemeris and names no satellite.
    assert "GPUT" not in messages.messages


def test_l5_isc_separates_iir_m_from_l5_satellites():
    """Block IIR-M sends CNAV on L2C with both L5 ISC values exactly zero (D-104)."""
    messages = tp.parse_rinex4(FILE)
    assert messages.l5_isc_nonzero == {"G04", "G06"}  # G06: one blank, one non-zero
    l5 = tp.Rule("rinex4", "G", "CNAV", requires_l5_isc=True)
    l2c = tp.Rule("rinex4", "G", "CNAV")
    assert tp.prns_from_rinex4(messages, l5) == [4, 6]
    assert tp.prns_from_rinex4(messages, l2c) == [4, 5, 6]


def test_a_file_that_is_not_rinex4_gives_no_satellites():
    assert tp.parse_rinex4(["G04 2026 11 10 00 00 00 1.0", "     1.0"]).messages == {}


# --- Tables --------------------------------------------------------------------


def test_table_dates_include_the_first_day_and_exclude_valid_to():
    entries = tp.parse_config(base_raw()).tables["GPS_L1C"]
    assert tp.prns_from_table(entries, date(2019, 1, 9)) == [4]
    assert tp.prns_from_table(entries, date(2019, 7, 13)) == []  # valid_to is not included
    assert tp.prns_from_table(entries, date(2019, 10, 21)) == [4]
    assert tp.prns_from_table(entries, date(2026, 4, 24)) == [4]
    assert tp.prns_from_table(entries, DAY) == [4, 13]


# --- Config checking -----------------------------------------------------------


def test_parse_config_reports_every_problem_at_once():
    raw = base_raw()
    raw["enabled"] = "yes"
    raw["signals"]["GPS_L1CA"]["source"] = "almanac"
    raw["signals"]["BDS_B1C"]["system"] = "X"
    raw["signals"]["GAL_E5A"] = {"source": "table"}  # no tables.GAL_E5A
    raw["tables"]["GPS_L1C"].append({"prn": 18, "valid_from": "2020-13-40"})
    raw["tables"]["GPS_L1C"].append({"prn": 23, "valid_from": date(2021, 1, 1),
                                     "valid_to": date(2020, 1, 1)})
    with pytest.raises(tp.ConfigError) as error:
        tp.parse_config(raw)
    text = str(error.value)
    for expected in ("enabled", "signals.GPS_L1CA.source", "signals.BDS_B1C.system",
                     "tables.GAL_E5A is missing", "is not a date", "valid_to must be after"):
        assert expected in text


def test_missing_config_file_is_a_clear_error(tmp_path):
    with pytest.raises(tp.ConfigError, match="cannot read"):
        tp.load_config(tmp_path / "absent.yml")


def test_shipped_config_is_valid_and_names_real_signals():
    cfg = tp.load_config()
    known = {cls.signal_type_id for cls in vars(signal_interfaces).values()
             if isinstance(cls, type) and isinstance(getattr(cls, "signal_type_id", None), str)}
    assert set(cfg.rules) <= known, set(cfg.rules) - known


def test_shipped_l1c_table_holds_gps_iii_only_on_the_argo_date():
    """2026-11-10: G04 and G18 (GPS III) were tracked on L1C; G16 (Block IIR) was a false alarm."""
    prns = tp.prns_from_table(tp.load_config().tables["GPS_L1C"], DAY)
    assert prns == [1, 4, 11, 13, 14, 18, 20, 21, 23, 28]
    assert 16 not in prns


# --- Selection -----------------------------------------------------------------


def config(**changes) -> tp.PrnConfig:
    raw = copy.deepcopy(base_raw())
    raw.update(changes)
    return tp.parse_config(raw)


@pytest.fixture
def rinex4(monkeypatch):
    """Replace the download with the synthetic file."""
    monkeypatch.setattr(tp, "load_rinex4", lambda day, cfg, resources_dir=None: tp.parse_rinex4(FILE))


def test_select_from_rinex4_keeps_the_order_and_lists_the_skipped(rinex4):
    sel = tp.select_prns("GPS_L1CA", range(1, 33), DAY, config())
    assert sel.prns == [4, 5, 6, 16]
    assert sel.source == "rinex4" and sel.filtered
    assert sel.skipped == [p for p in range(1, 33) if p not in (4, 5, 6, 16)]
    assert "G04 G05 G06 G16" in sel.summary("G")


def test_select_from_table(rinex4):
    sel = tp.select_prns("GPS_L1C", range(1, 33), DAY, config())
    assert (sel.prns, sel.source) == ([4, 13], "table")


def test_select_is_limited_to_the_prns_with_a_code(rinex4):
    sel = tp.select_prns("BDS_B1C", range(1, 11), DAY, config())  # C13 has no code here
    assert sel.source == "fallback"
    assert "lists no PRN" in sel.note


def test_disabled_searches_every_prn():
    sel = tp.select_prns("GPS_L1C", [1, 2, 3], DAY, config(enabled=False))
    assert (sel.prns, sel.source, sel.filtered) == ([1, 2, 3], "disabled", False)


def test_download_failure_falls_back_with_the_reason(monkeypatch):
    def fail(day, cfg, resources_dir=None):
        raise RuntimeError("Downloading from CDDIS needs Earthdata credentials")

    monkeypatch.setattr(tp, "load_rinex4", fail)
    sel = tp.select_prns("GPS_L1CA", [1, 2], DAY, config())
    assert (sel.prns, sel.source) == ([1, 2], "fallback")
    assert "WARNING" in sel.note and "Earthdata" in sel.note


def test_failure_stops_the_run_when_the_fallback_is_off(monkeypatch):
    monkeypatch.setattr(tp, "load_rinex4", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        tp.select_prns("GPS_L1CA", [1, 2], DAY, config(fallback_to_all_prns=False))


def test_unknown_date_and_missing_rule_fall_back():
    assert tp.select_prns("GPS_L1CA", [1], None, config()).source == "fallback"
    sel = tp.select_prns("GAL_E5A", [1], DAY, config())
    assert sel.source == "fallback" and "no rule" in sel.note


def test_collect_day():
    assert tp.collect_day("20261110_191853_l1_a1_424s") == DAY
    assert tp.collect_day("RX7") is None


def test_rinex4_path_uses_the_day_of_year(tmp_path):
    url, path = tp.rinex4_url_and_path(DAY, config(), tmp_path)
    assert url.endswith("/gnss/data/daily/2026/brdc/BRD400DLR_S_20263140000_01D_MN.rnx.gz")
    assert path == tmp_path / "gnss/data/daily/2026/brdc/BRD400DLR_S_20263140000_01D_MN.rnx.gz"


def test_shipped_yaml_parses_dates_as_dates():
    raw = yaml.safe_load(tp.DEFAULT_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw["tables"]["GPS_L1C"][0]["valid_from"], date)


# --- Notebook 01 -----------------------------------------------------------------


def test_notebook_01_searches_the_selected_prns():
    nb = json.loads((ROOT / "notebooks" / "01-acquisition-and-tracking.ipynb").read_text(encoding="utf-8"))
    code = "".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert "transmitting_prns.select_prns(" in code
    assert "build_signals(SIGNAL_ID, prns=prn_selection.prns)" in code
    assert "build_signals(SIGNAL_ID)\n" not in code
