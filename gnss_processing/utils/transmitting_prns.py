"""
Which PRNs actually transmit a signal on a given day (D-104).

Notebook 01 used to search every PRN code that exists for a signal (1-32 for GPS,
1-63 for BeiDou).  Many of those satellites do not send the signal: only GPS III
sends L1C, only BeiDou-3 sends B1C and B2a, and Block IIR sends no modern signal at
all.  A detection on such a PRN can only be a false alarm -- on the 2026-11-10
ARGO collect, channel A2 "acquired" G16 on L1C, and G16 is Block IIR.  So the
search list comes from what was really broadcast that day:

* the multi-GNSS RINEX 4 broadcast file of the collect date (CDDIS).  RINEX 4 marks
  each ephemeris record with the message that carried it (LNAV, CNAV, CNV1, ...),
  and a message is only sent on its own signal, so the satellites with a record of
  a message are the satellites that sent its signal;
* a dated table in the config file, where the RINEX 4 file has no record for the
  signal (GPS L1C on 2026-11-10: no GPS CNV2 records).

The rules and the tables are in `configs/transmitting_prns.yml`; nothing about a
signal is fixed here.  When no list can be made (no network, no login, nothing in
the file), the caller gets every PRN back with the reason, so a run never fails
silently and never searches an empty list.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

from . import cddis, environment_variables

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "configs" / "transmitting_prns.yml"

SOURCES = {"rinex4", "table"}
SYSTEMS = {"G", "C", "E", "R", "J", "I", "S"}

# RINEX 4.02, GPS CNAV record: the 7th "broadcast orbit" line holds ISC_L1CA,
# ISC_L2C, ISC_L5I5, ISC_L5Q5 (Table A33).  Fields are 19 characters wide after a
# 4-character indent.
_CNAV_ISC_LINE = 7
_FIELD_START, _FIELD_WIDTH, _FIELDS_PER_LINE = 4, 19, 4
_ISC_L5_FIELDS = (2, 3)


class ConfigError(ValueError):
    """The transmitting-PRN config has a bad or missing value."""


@dataclass(frozen=True)
class Rule:
    """How to find the PRNs that transmit one signal."""

    source: str
    system: str | None = None
    message: str | None = None
    requires_l5_isc: bool = False


@dataclass(frozen=True)
class TableEntry:
    """One PRN of a hand-kept list, valid from `valid_from` up to (not on) `valid_to`."""

    prn: int
    valid_from: date
    valid_to: date | None = None

    def valid_on(self, day: date) -> bool:
        """Whether the PRN is in the list on `day`."""
        return self.valid_from <= day and (self.valid_to is None or day < self.valid_to)


@dataclass(frozen=True)
class PrnConfig:
    """The checked content of `configs/transmitting_prns.yml`."""

    enabled: bool
    fallback_to_all_prns: bool
    rinex4_directory: str
    rinex4_filename: str
    rules: dict[str, Rule]
    tables: dict[str, list[TableEntry]]


def _as_date(value, where: str, problems: list[str]) -> date | None:
    """A YAML date (or ISO text) as a `date`; record a problem and return None if bad."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        problems.append(f"{where}: {value!r} is not a date (YYYY-MM-DD)")
        return None


def parse_config(raw: dict) -> PrnConfig:
    """
    Check a loaded config and return it as a `PrnConfig`.

    Every problem is collected first and reported in one `ConfigError`, so one run
    shows all the bad keys, not only the first.
    """
    problems: list[str] = []
    if not isinstance(raw, dict):
        raise ConfigError("the transmitting-PRN config is not a mapping")
    for key in ("enabled", "fallback_to_all_prns"):
        if not isinstance(raw.get(key), bool):
            problems.append(f"{key}: must be true or false")
    rinex4 = raw.get("rinex4")
    if not isinstance(rinex4, dict) or not all(isinstance(rinex4.get(k), str) and rinex4.get(k)
                                                for k in ("directory", "filename")):
        problems.append("rinex4: needs text values for 'directory' and 'filename'")
        rinex4 = {"directory": "", "filename": ""}

    rules: dict[str, Rule] = {}
    signals = raw.get("signals")
    if not isinstance(signals, dict) or not signals:
        problems.append("signals: must map each signal_type_id to a rule")
        signals = {}
    tables_raw = raw.get("tables") or {}
    if not isinstance(tables_raw, dict):
        problems.append("tables: must map a signal_type_id to a list of PRN entries")
        tables_raw = {}
    for signal, rule in signals.items():
        where = f"signals.{signal}"
        if not isinstance(rule, dict):
            problems.append(f"{where}: must be a mapping")
            continue
        source = rule.get("source")
        if source not in SOURCES:
            problems.append(f"{where}.source: {source!r} is not one of {sorted(SOURCES)}")
            continue
        if source == "rinex4":
            if rule.get("system") not in SYSTEMS:
                problems.append(f"{where}.system: {rule.get('system')!r} is not one of {sorted(SYSTEMS)}")
            if not isinstance(rule.get("message"), str) or not rule.get("message"):
                problems.append(f"{where}.message: needs a RINEX 4 message name, e.g. LNAV")
        elif signal not in tables_raw:
            problems.append(f"{where}: source is 'table' but tables.{signal} is missing")
        requires = rule.get("requires_l5_isc", False)
        if not isinstance(requires, bool):
            problems.append(f"{where}.requires_l5_isc: must be true or false")
        rules[signal] = Rule(source=source, system=rule.get("system"), message=rule.get("message"),
                             requires_l5_isc=bool(requires))

    tables: dict[str, list[TableEntry]] = {}
    for signal, entries in tables_raw.items():
        if not isinstance(entries, list) or not entries:
            problems.append(f"tables.{signal}: must be a non-empty list")
            continue
        tables[signal] = []
        for n, entry in enumerate(entries):
            where = f"tables.{signal}[{n}]"
            prn = entry.get("prn") if isinstance(entry, dict) else None
            if not isinstance(prn, int) or isinstance(prn, bool) or prn < 1:
                problems.append(f"{where}.prn: must be a positive whole number")
                continue
            start = _as_date(entry.get("valid_from"), f"{where}.valid_from", problems)
            end_raw = entry.get("valid_to")
            end = None if end_raw is None else _as_date(end_raw, f"{where}.valid_to", problems)
            if start is not None and end is not None and end <= start:
                problems.append(f"{where}: valid_to must be after valid_from")
            if start is not None:
                tables[signal].append(TableEntry(prn, start, end))
    if problems:
        raise ConfigError("bad transmitting-PRN config:\n  " + "\n  ".join(problems))
    return PrnConfig(raw["enabled"], raw["fallback_to_all_prns"], rinex4["directory"],
                     rinex4["filename"], rules, tables)


def load_config(path: str | Path = DEFAULT_PATH) -> PrnConfig:
    """Read and check the config file; a missing file or bad value is a `ConfigError`."""
    path = Path(path)
    try:
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    return parse_config(raw)


# ---------------------------------------------------------------------------
# RINEX 4
# ---------------------------------------------------------------------------


@dataclass
class Rinex4Messages:
    """What a RINEX 4 navigation file says each satellite broadcast."""

    messages: dict[str, set[str]] = field(default_factory=dict)
    """Satellite id (e.g. 'G04') -> the message types of its ephemeris records."""

    l5_isc_nonzero: set[str] = field(default_factory=set)
    """GPS satellites with a CNAV record whose ISC_L5I5 or ISC_L5Q5 is not zero."""

    def satellites(self, system: str, message: str) -> set[str]:
        """The satellites of `system` with at least one record of `message`."""
        return {sat for sat, kinds in self.messages.items() if sat[0] == system and message in kinds}


def _fields(line: str) -> list[float | None]:
    """The four 19-character numbers of one 'broadcast orbit' line (None where blank)."""
    values: list[float | None] = []
    for k in range(_FIELDS_PER_LINE):
        start = _FIELD_START + k * _FIELD_WIDTH
        text = line[start:start + _FIELD_WIDTH].strip().replace("D", "E")
        values.append(float(text) if text else None)
    return values


def parse_rinex4(lines: Iterable[str]) -> Rinex4Messages:
    """
    Read the ephemeris records of a RINEX 4 navigation file.

    Each record starts with a line '> EPH <sat> <message>'.  Only the record type
    is needed, and for GPS CNAV the L5 inter-signal corrections.  A file that is not
    RINEX 4 has no such lines, and the result is empty.
    """
    result = Rinex4Messages()
    record: list[str] = []
    sat = kind = ""

    def close() -> None:
        if kind == "CNAV" and sat.startswith("G") and len(record) > _CNAV_ISC_LINE:
            isc = _fields(record[_CNAV_ISC_LINE])
            if any(isc[i] not in (None, 0.0) for i in _ISC_L5_FIELDS):
                result.l5_isc_nonzero.add(sat)

    for line in lines:
        if line.startswith(">"):
            close()
            parts = line.split()
            record, sat, kind = [], "", ""
            if len(parts) >= 4 and parts[1] == "EPH":
                sat, kind = parts[2], parts[3]
                result.messages.setdefault(sat, set()).add(kind)
        elif sat:
            record.append(line.rstrip("\n"))
    close()
    return result


def rinex4_url_and_path(day: date, cfg: PrnConfig, resources_dir: str | Path) -> tuple[str, Path]:
    """`(url, local path)` of the RINEX 4 file of one day, in the CDDIS layout."""
    fields = {"yyyy": f"{day:%Y}", "ddd": f"{day.timetuple().tm_yday:03d}"}
    relative = (cfg.rinex4_directory + cfg.rinex4_filename).format(**fields)
    return cddis.CDDIS_ARCHIVE_URL + relative, Path(resources_dir) / relative.lstrip("/")


def load_rinex4(day: date, cfg: PrnConfig, resources_dir: str | Path | None = None) -> Rinex4Messages:
    """
    The RINEX 4 messages of one day, downloaded once and then read from the cache.

    A cached file that is not gzip (for example a saved login page) is downloaded
    again, as the other CDDIS readers do.
    """
    if resources_dir is None:
        resources_dir = environment_variables.get_resources_path()
    url, path = rinex4_url_and_path(day, cfg, resources_dir)
    poisoned = path.exists() and path.read_bytes()[:2] != b"\x1f\x8b"
    if not path.exists() or poisoned:
        cddis.download(url, path, overwrite=poisoned)
    with gzip.open(path, "rt", encoding="ascii", errors="replace") as handle:
        return parse_rinex4(handle)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def prns_from_rinex4(messages: Rinex4Messages, rule: Rule) -> list[int]:
    """The PRNs that `rule` selects from one day's RINEX 4 messages."""
    assert rule.system is not None and rule.message is not None
    sats = messages.satellites(rule.system, rule.message)
    if rule.requires_l5_isc:
        sats &= messages.l5_isc_nonzero
    return sorted(int(sat[1:]) for sat in sats)


def prns_from_table(entries: list[TableEntry], day: date) -> list[int]:
    """The PRNs of a hand-kept table that are valid on `day`."""
    return sorted({entry.prn for entry in entries if entry.valid_on(day)})


@dataclass(frozen=True)
class PrnSelection:
    """The PRNs to search, where they came from, and why."""

    prns: list[int]
    source: str
    """'rinex4', 'table', 'disabled', or 'fallback'."""
    note: str
    skipped: list[int] = field(default_factory=list)
    """PRNs with a code that the list left out."""

    @property
    def filtered(self) -> bool:
        """Whether the search list is narrower than every PRN with a code."""
        return self.source in ("rinex4", "table")

    def summary(self, system_letter: str) -> str:
        """Two or three lines for the notebook output."""
        names = " ".join(f"{system_letter}{p:02d}" for p in self.prns)
        lines = [f"PRNs searched ({len(self.prns)}, {self.source}): {names}", f"  {self.note}"]
        if self.skipped:
            lines.append(f"  left out ({len(self.skipped)}, not transmitting this signal): "
                         + " ".join(f"{system_letter}{p:02d}" for p in self.skipped))
        return "\n".join(lines)


def collect_day(collect_id: str) -> date | None:
    """
    The UTC date of a collect from its id `YYYYMMDD_HHMMSS_...` (D-030), or None.

    None, not an error: an old collect id without a start time still runs, and
    `select_prns` then searches every PRN and says why.
    """
    try:
        return datetime.strptime("_".join(collect_id.split("_")[:2]), "%Y%m%d_%H%M%S").date()
    except ValueError:
        return None


def select_prns(
    signal_type_id: str,
    default_prns: Iterable[int],
    day: date | None,
    cfg: PrnConfig | None = None,
    resources_dir: str | Path | None = None,
) -> PrnSelection:
    """
    The PRNs that transmit `signal_type_id` on `day`, limited to `default_prns`.

    `default_prns` are the PRNs this code can build (the signal's own list).  The
    result keeps their order.  When the list cannot be made (also when `day` is
    None), the result is every default PRN with source 'fallback' and the reason --
    or a RuntimeError, when the config turns the fallback off.
    """
    cfg = load_config() if cfg is None else cfg
    default = list(default_prns)
    if not cfg.enabled:
        return PrnSelection(default, "disabled", "the transmitting-PRN check is off (enabled: false)")

    def fallback(reason: str) -> PrnSelection:
        if not cfg.fallback_to_all_prns:
            raise RuntimeError(f"no transmitting-PRN list for {signal_type_id} on {day}: {reason}")
        return PrnSelection(default, "fallback", f"WARNING: searching every PRN: {reason}")

    if day is None:
        return fallback("the collect date is not known (the collect id has no YYYYMMDD_HHMMSS)")
    rule = cfg.rules.get(signal_type_id)
    if rule is None:
        return fallback(f"configs/transmitting_prns.yml has no rule for {signal_type_id}")
    if rule.source == "table":
        found = prns_from_table(cfg.tables[signal_type_id], day)
        note = f"from the table tables.{signal_type_id} in configs/transmitting_prns.yml, valid on {day}"
    else:
        try:
            messages = load_rinex4(day, cfg, resources_dir)
        except Exception as error:  # noqa: BLE001 -- any failure here means "no list"
            return fallback(f"the RINEX 4 file of {day} could not be read ({type(error).__name__}: {error})")
        found = prns_from_rinex4(messages, rule)
        isc = ", L5 ISC not zero" if rule.requires_l5_isc else ""
        note = f"from the RINEX 4 broadcast file of {day}: {rule.system} satellites with {rule.message} records{isc}"
    keep = set(found)
    prns = [p for p in default if p in keep]
    if not prns:
        return fallback(f"the {rule.source} source lists no PRN for {signal_type_id} on {day}")
    skipped = [p for p in default if p not in keep]
    return PrnSelection(prns, rule.source, note, skipped)
