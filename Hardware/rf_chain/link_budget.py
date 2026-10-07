"""Gain and noise budget for the HERON RF front end.

The tool reads a TOML file that lists the bands, the parts, and the chains
(antenna -> bias tee -> SDR). It prints one Markdown report. It holds no
design values. Only physical constants live in the code. This way an engineer
changes a part, a gain, or a bandwidth in the config and runs the tool again.

Run (from HERON_DEPLOY_SOFTWARE/, so that pydantic is available):

    uv run python ../Hardware/rf_chain/link_budget.py ../Hardware/rf_chain/rf_chain.example.toml
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

BOLTZMANN_J_PER_K = 1.380649e-23  # Physical constant. Not a design value.


class _Strict(BaseModel):
    """Base model. Unknown keys are errors, so a typo in the config fails loudly."""

    model_config = ConfigDict(extra="forbid")


class Environment(_Strict):
    """Settings that apply to the whole budget."""

    reference_temperature_k: float = Field(gt=0)
    case: Literal["typical", "worst"]


class Band(_Strict):
    """One GNSS band: where it is, how wide we look, and how weak the signal is."""

    frequency_hz: float = Field(gt=0)
    noise_bandwidth_hz: float = Field(gt=0)
    min_signal_dbm: float


class Component(_Strict):
    """One part in a chain. Fields not used by a part stay empty."""

    description: str = ""
    gain_db: dict[str, float] = Field(default_factory=dict)
    gain_db_worst: dict[str, float] = Field(default_factory=dict)
    nf_db: dict[str, float] = Field(default_factory=dict)
    nf_db_worst: dict[str, float] = Field(default_factory=dict)
    provisional: bool = False
    note: str = ""
    # Supply data for an active antenna.
    supply_min_v: float | None = None
    supply_max_v: float | None = None
    supply_current_ma: float | None = None
    # Data for a bias tee.
    dc_resistance_ohm: float | None = None
    dc_max_v: float | None = None
    dc_max_ma: float | None = None
    # Data for the SDR input.
    max_input_dbm: float | None = None
    gain_range_db: tuple[float, float] | None = None


class Chain(_Strict):
    """One antenna path from the antenna to the SDR input."""

    name: str
    bands: list[str] = Field(min_length=1)
    antenna_gain_dbic: dict[str, float]
    stages: list[str] = Field(min_length=1)
    sdr: str
    sdr_gain_db: float
    bias_tee: str | None = None
    active_antenna: str | None = None
    supply_voltage_v: float | None = None


class Requirements(_Strict):
    """Targets for the required-gain calculation (optional section)."""

    target_cn0_dbhz: float = Field(gt=0)
    # Extra goals: the largest C/N0 loss (dB) that the parts after the first stage may cost.
    max_cn0_loss_db: list[float] = Field(default_factory=list)


class Config(_Strict):
    """The whole config file."""

    environment: Environment
    bands: dict[str, Band]
    components: dict[str, Component]
    chains: list[Chain] = Field(min_length=1)
    requirements: Requirements | None = None

    @model_validator(mode="after")
    def _check_references(self) -> Config:
        """Fail early, with a clear message, when the config is inconsistent."""
        for chain in self.chains:
            names = [*chain.stages, chain.sdr]
            for extra in (chain.bias_tee, chain.active_antenna):
                if extra is not None:
                    names.append(extra)
            for name in names:
                if name not in self.components:
                    raise ValueError(f"chain '{chain.name}': unknown component '{name}'")
            for band in chain.bands:
                if band not in self.bands:
                    raise ValueError(f"chain '{chain.name}': unknown band '{band}'")
                if band not in chain.antenna_gain_dbic:
                    raise ValueError(f"chain '{chain.name}': no antenna_gain_dbic for {band}")
                for name in chain.stages:
                    self._stage_values(name, band)  # raises when the data is missing
                self._stage_values(chain.sdr, band, is_sdr=True)
        return self

    def _stage_values(self, name: str, band: str, is_sdr: bool = False) -> tuple[float, float]:
        """Return (gain_db, nf_db) of a part in a band, for the selected case."""
        comp = self.components[name]
        worst = self.environment.case == "worst"
        gain_map = {**comp.gain_db, **(comp.gain_db_worst if worst else {})}
        nf_map = {**comp.nf_db, **(comp.nf_db_worst if worst else {})}
        if is_sdr:
            gain = 0.0  # The SDR gain is set per chain.
        elif band in gain_map:
            gain = gain_map[band]
        else:
            raise ValueError(f"component '{name}': no gain_db for band {band}")
        if band in nf_map:
            return gain, nf_map[band]
        if gain <= 0 and not is_sdr:
            return gain, -gain  # A pure loss (or a 0 dB link) has nf equal to its loss.
        raise ValueError(f"component '{name}': no nf_db for band {band}")

    def stage_values(self, name: str, band: str, is_sdr: bool = False) -> tuple[float, float]:
        """Public wrapper for the stage lookup."""
        return self._stage_values(name, band, is_sdr)


def lin(db: float) -> float:
    """Convert dB to a linear power ratio."""
    return 10.0 ** (db / 10.0)


def to_db(ratio: float) -> float:
    """Convert a linear power ratio to dB."""
    return 10.0 * math.log10(ratio)


def noise_dbm_per_hz(temperature_k: float) -> float:
    """Thermal noise density kT in dBm/Hz."""
    return to_db(BOLTZMANN_J_PER_K * temperature_k * 1000.0)


class StageRow(BaseModel):
    """One line of the budget table."""

    name: str
    gain_db: float
    nf_db: float
    cum_gain_db: float
    cum_nf_db: float
    signal_dbm: float
    noise_dbm: float
    provisional: bool = False


class BandResult(BaseModel):
    """Budget result of one chain in one band."""

    band: str
    rows: list[StageRow]
    gain_to_sdr_input_db: float
    nf_total_db: float
    cn0_dbhz: float
    signal_at_sdr_input_dbm: float
    noise_at_sdr_input_dbm: float
    headroom_db: float | None
    total_gain_db: float


def budget(cfg: Config, chain: Chain, band_name: str) -> BandResult:
    """Run the cascade for one chain and one band.

    The noise level after a stage is kTB plus the cumulative noise figure plus
    the cumulative gain. The signal level starts at the band's minimum level
    plus the antenna gain.
    """
    band = cfg.bands[band_name]
    kt = noise_dbm_per_hz(cfg.environment.reference_temperature_k)
    ktb = kt + to_db(band.noise_bandwidth_hz)
    p_sig = band.min_signal_dbm + chain.antenna_gain_dbic[band_name]
    steps = [(name, False) for name in chain.stages] + [(chain.sdr, True)]

    rows: list[StageRow] = [
        StageRow(name="Antenna terminal", gain_db=0.0, nf_db=0.0, cum_gain_db=0.0, cum_nf_db=0.0,
                 signal_dbm=p_sig, noise_dbm=ktb)
    ]
    f_total, g_total_lin = 1.0, 1.0
    gain_to_sdr = 0.0
    for name, is_sdr in steps:
        gain, nf = cfg.stage_values(name, band_name, is_sdr)
        if is_sdr:
            gain_to_sdr = to_db(g_total_lin)
            gain = chain.sdr_gain_db
        f_total += (lin(nf) - 1.0) / g_total_lin
        g_total_lin *= lin(gain)
        cum_gain, cum_nf = to_db(g_total_lin), to_db(f_total)
        rows.append(StageRow(
            name=name, gain_db=gain, nf_db=nf, cum_gain_db=cum_gain, cum_nf_db=cum_nf,
            signal_dbm=p_sig + cum_gain, noise_dbm=ktb + cum_nf + cum_gain,
            provisional=cfg.components[name].provisional,
        ))

    nf_total = to_db(f_total)
    noise_in = ktb + nf_total + gain_to_sdr
    max_in = cfg.components[chain.sdr].max_input_dbm
    return BandResult(
        band=band_name, rows=rows, gain_to_sdr_input_db=gain_to_sdr, nf_total_db=nf_total,
        cn0_dbhz=p_sig - kt - nf_total, signal_at_sdr_input_dbm=p_sig + gain_to_sdr,
        noise_at_sdr_input_dbm=noise_in,
        headroom_db=None if max_in is None else max_in - noise_in,
        total_gain_db=to_db(g_total_lin),
    )


class RequirementResult(BaseModel):
    """How much first-stage (LNA) gain a C/N0 target needs, for one chain and band."""

    band: str
    target_cn0_dbhz: float
    max_cn0_dbhz: float  # C/N0 of an ideal receiver (noise figure 0 dB).
    allowed_nf_db: float  # Largest cascade noise figure that still meets the target.
    current_nf_db: float
    first_stage_gain_db: float
    first_stage_nf_db: float
    required_gain_db: float | None  # None: the target cannot be met at any gain.
    margin_db: float | None
    status: str
    gain_for_loss_db: dict[str, float | None]  # key: allowed C/N0 loss in dB.


def _required_first_stage_gain(f_first: float, downstream: float, f_allowed: float) -> float | None:
    """Solve F_allowed = F1 + S/G1 for G1 (linear). None when F_allowed is not above F1."""
    if f_allowed <= f_first:
        return None
    return downstream / (f_allowed - f_first)


def requirement(cfg: Config, chain: Chain, band_name: str) -> RequirementResult:
    """Find the first-stage gain that a C/N0 target needs.

    The cascade noise factor is F = F1 + S/G1. F1 and G1 belong to the first stage
    (the LNA in the antenna). S sums the later stages. S does not depend on G1, so
    G1 = S / (F_allowed - F1). The SDR gain does not appear: it does not change C/N0.
    """
    assert cfg.requirements is not None
    req = cfg.requirements
    res = budget(cfg, chain, band_name)
    kt = noise_dbm_per_hz(cfg.environment.reference_temperature_k)
    gain1, nf1 = cfg.stage_values(chain.stages[0], band_name)
    downstream = (lin(res.nf_total_db) - lin(nf1)) * lin(gain1)
    max_cn0 = cfg.bands[band_name].min_signal_dbm + chain.antenna_gain_dbic[band_name] - kt
    allowed_nf = max_cn0 - req.target_cn0_dbhz
    required: float | None = None
    if allowed_nf > 0:
        g_lin = _required_first_stage_gain(lin(nf1), downstream, lin(allowed_nf))
        required = None if g_lin is None else to_db(g_lin)
    if allowed_nf <= 0:
        status = "target is above the C/N0 of an ideal receiver"
    elif required is None:
        status = "target needs a lower first-stage noise figure; no gain is enough"
    else:
        status = "meets the target" if gain1 >= required else "short of the target"
    losses: dict[str, float | None] = {}
    for loss in req.max_cn0_loss_db:
        g_lin = _required_first_stage_gain(lin(nf1), downstream, lin(nf1) * lin(loss))
        losses[f"{loss:g}"] = None if g_lin is None else to_db(g_lin)
    return RequirementResult(
        band=band_name, target_cn0_dbhz=req.target_cn0_dbhz, max_cn0_dbhz=max_cn0,
        allowed_nf_db=allowed_nf, current_nf_db=res.nf_total_db, first_stage_gain_db=gain1,
        first_stage_nf_db=nf1, required_gain_db=required,
        margin_db=None if required is None else gain1 - required, status=status,
        gain_for_loss_db=losses,
    )


def requirement_lines(res: RequirementResult) -> list[str]:
    """Markdown lines for one requirement result."""
    lines = [
        f"#### Gain requirement for {res.target_cn0_dbhz:g} dB-Hz",
        "",
        f"- Best possible C/N0 (ideal receiver): {res.max_cn0_dbhz:.1f} dB-Hz",
        f"- Largest cascade noise figure that meets the target: {res.allowed_nf_db:.2f} dB "
        f"(now {res.current_nf_db:.2f} dB)",
    ]
    if res.required_gain_db is None:
        lines.append(f"- Required first-stage (LNA) gain: **none** ({res.status})")
    else:
        lines.append(
            f"- Required first-stage (LNA) gain: at least **{res.required_gain_db:.1f} dB** "
            f"(first stage now {res.first_stage_gain_db:+.1f} dB, margin "
            f"{res.margin_db:+.1f} dB): {res.status}")
    for loss, gain in res.gain_for_loss_db.items():
        text = "none" if gain is None else f"{gain:.1f} dB"
        lines.append(f"- First-stage gain for at most {loss} dB of C/N0 loss from later "
                     f"stages: {text}")
    lines.append("- SDR gain: **TBD**. It does not change C/N0. It sets the ADC fill, which "
                 "needs the B210 ADC full-scale level.")
    lines.append("")
    return lines


def dc_report(cfg: Config, chain: Chain) -> list[str]:
    """Check the DC path of a chain: supply range, current limit, voltage at the antenna."""
    if chain.active_antenna is None:
        return ["- Antenna is passive: no DC check."]
    ant = cfg.components[chain.active_antenna]
    tee = cfg.components[chain.bias_tee] if chain.bias_tee else None
    if chain.supply_voltage_v is None:
        return ["- Supply voltage: **TBD** (set `supply_voltage_v` in the config to check it)."]
    volts = chain.supply_voltage_v
    lines = [f"- Supply voltage: {volts:g} V"]
    if ant.supply_min_v is not None and ant.supply_max_v is not None:
        ok = ant.supply_min_v <= volts <= ant.supply_max_v
        lines.append(f"- Antenna range {ant.supply_min_v:g} to {ant.supply_max_v:g} V: "
                     f"{'OK' if ok else '**OUT OF RANGE**'}")
    if tee is not None:
        if tee.dc_max_v is not None:
            lines.append(f"- Bias tee limit {tee.dc_max_v:g} V: "
                         f"{'OK' if volts <= tee.dc_max_v else '**EXCEEDED**'}")
        if ant.supply_current_ma is not None and tee.dc_max_ma is not None:
            lines.append(f"- Current {ant.supply_current_ma:g} mA, bias tee limit "
                         f"{tee.dc_max_ma:g} mA: "
                         f"{'OK' if ant.supply_current_ma <= tee.dc_max_ma else '**EXCEEDED**'}")
        if ant.supply_current_ma is not None and tee.dc_resistance_ohm is not None:
            drop = ant.supply_current_ma / 1000.0 * tee.dc_resistance_ohm
            lines.append(f"- Drop in the bias tee: {drop:.2f} V; "
                         f"at the antenna: {volts - drop:.2f} V")
    return lines


def render(cfg: Config) -> str:
    """Make the Markdown report for every chain and band."""
    env = cfg.environment
    out = [
        "# RF chain budget",
        "",
        f"Case: **{env.case}**. Reference temperature: {env.reference_temperature_k:g} K. "
        f"kT = {noise_dbm_per_hz(env.reference_temperature_k):.1f} dBm/Hz.",
        "",
    ]
    for chain in cfg.chains:
        out += [f"## {chain.name}", ""]
        for band_name in chain.bands:
            band = cfg.bands[band_name]
            res = budget(cfg, chain, band_name)
            out += [
                f"### {band_name} ({band.frequency_hz / 1e6:.2f} MHz, "
                f"{band.noise_bandwidth_hz / 1e6:.3f} MHz noise bandwidth)",
                "",
                "| Stage | Gain (dB) | NF (dB) | Cum. gain (dB) | Cum. NF (dB) "
                "| Signal (dBm) | Noise (dBm) |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for r in res.rows:
                tag = " (provisional)" if r.provisional else ""
                out.append(f"| {r.name}{tag} | {r.gain_db:+.1f} | {r.nf_db:.2f} | "
                           f"{r.cum_gain_db:+.1f} | {r.cum_nf_db:.2f} | "
                           f"{r.signal_dbm:.1f} | {r.noise_dbm:.1f} |")
            headroom = "n/a" if res.headroom_db is None else f"{res.headroom_db:.1f} dB"
            out += [
                "",
                f"- Gain, antenna to SDR input: {res.gain_to_sdr_input_db:+.1f} dB",
                f"- Cascade noise figure: {res.nf_total_db:.2f} dB",
                f"- C/N0 at the minimum signal: {res.cn0_dbhz:.1f} dB-Hz",
                f"- At the SDR input: signal {res.signal_at_sdr_input_dbm:.1f} dBm, "
                f"noise {res.noise_at_sdr_input_dbm:.1f} dBm "
                f"(headroom to the SDR input limit: {headroom})",
                f"- SDR gain setting {chain.sdr_gain_db:g} dB; total gain "
                f"{res.total_gain_db:+.1f} dB",
                "",
            ]
            if cfg.requirements is not None:
                out += requirement_lines(requirement(cfg, chain, band_name))
        out += ["### DC", "", *dc_report(cfg, chain), ""]
    return "\n".join(out)


def results_as_dict(cfg: Config) -> dict:
    """Return the config and every budget result as plain data (for the diagram tool)."""
    results = {
        chain.name: {b: budget(cfg, chain, b).model_dump() for b in chain.bands}
        for chain in cfg.chains
    }
    out: dict = {"config": cfg.model_dump(mode="json"), "results": results}
    if cfg.requirements is not None:
        out["requirements"] = {
            chain.name: {b: requirement(cfg, chain, b).model_dump() for b in chain.bands}
            for chain in cfg.chains
        }
    return out


def load_config(path: Path) -> Config:
    """Read and validate the TOML file. Raise ValueError with a clear text on a bad file."""
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        return Config.model_validate(raw)
    except FileNotFoundError as exc:
        raise ValueError(f"config file not found: {path}") from exc
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ValueError(f"bad config {path}:\n{exc}") from exc


def main(argv: list[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="HERON RF chain gain and noise budget.")
    parser.add_argument("config", type=Path, help="TOML file (see rf_chain.example.toml)")
    parser.add_argument("--case", choices=["typical", "worst"], help="override [environment].case")
    parser.add_argument("--output", type=Path, help="write the report to this file")
    parser.add_argument("--json", type=Path, help="also write config and results as JSON")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.case:
            cfg.environment.case = args.case
            cfg = Config.model_validate(cfg.model_dump())  # check the data of the new case
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    report = render(cfg)
    if args.json:
        args.json.write_text(json.dumps(results_as_dict(cfg), indent=2) + "\n", encoding="utf-8")
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
