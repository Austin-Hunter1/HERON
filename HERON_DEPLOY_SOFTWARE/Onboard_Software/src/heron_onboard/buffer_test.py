"""The NUC buffer-size test: find buffer settings that record without loss.

The recorder has four buffer knobs (D-078):

- ``spb``: samples per ``recv()`` call (the collect wizard's ``--spb``).
- ``num_recv_frames`` and ``recv_frame_size``: UHD's USB frame buffers
  (device args). These absorb a short disk stall in the direct mode.
- ``file_buffer_bytes``: the stdio buffer in front of each file.

and two write modes (``direct``, the wizard method, and ``threaded``).
The right values depend on the NUC, its SSD and the USB controller, so
they must be measured on the payload itself. This module runs the real
recorder for a short time with each combination from the
``[buffer_test]`` config table, reads the recorder's ``stopped``
summary line, and writes one CSV row per trial.

The recorder is launched through a ``Runner`` callable, so the tests
can replace the hardware with a fake.
"""

from __future__ import annotations

import csv
import itertools
import json
import logging
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from heron_onboard.capture.recorder_process import build_recorder_command, parse_status_line
from heron_onboard.config import BufferTestConfig, CaptureConfig, OnboardConfig, SdrConfig

log = logging.getLogger(__name__)

# A runner starts one recorder argv and returns (exit code, stdout lines).
Runner = Callable[[list[str], float], tuple[int, list[str]]]

CSV_COLUMNS = [
    "trial",
    "write_mode",
    "spb",
    "num_recv_frames",
    "recv_frame_size",
    "file_buffer_bytes",
    "exit_code",
    "elapsed_s",
    "samples",
    "expected_samples",
    "missing_fraction",
    "overflows",
    "host_drops",
    "timeouts",
    "bytes",
    "write_mbps",
    "clean",
    "error",
]


@dataclass(frozen=True)
class Trial:
    """One combination of buffer settings."""

    index: int
    write_mode: str
    spb: int
    num_recv_frames: int | None
    recv_frame_size: int | None
    file_buffer_bytes: int


@dataclass
class TrialResult:
    """What one trial measured. ``clean`` means no loss of any kind."""

    trial: Trial
    exit_code: int = -1
    elapsed_s: float = 0.0
    samples: int = 0
    expected_samples: int = 0
    overflows: int = 0
    host_drops: int = 0
    timeouts: int = 0
    bytes: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def write_mbps(self) -> float:
        """Average disk write rate over the streaming time, MB/s."""
        return self.bytes / self.elapsed_s / 1e6 if self.elapsed_s > 0 else 0.0

    @property
    def missing_fraction(self) -> float:
        """Share of the samples the sample rate promised that never arrived."""
        if self.expected_samples <= 0:
            return 1.0
        return max(0.0, 1.0 - self.samples / self.expected_samples)

    @property
    def clean(self) -> bool:
        """True when the trial ran to the end with no overflow and no drop."""
        return (
            self.exit_code == 0
            and not self.errors
            and self.overflows == 0
            and self.host_drops == 0
            and self.samples > 0
        )

    def csv_row(self) -> dict[str, object]:
        """One row for the results file, keyed by ``CSV_COLUMNS``."""
        t = asdict(self.trial)
        return {
            "trial": t["index"],
            "write_mode": t["write_mode"],
            "spb": t["spb"],
            "num_recv_frames": "" if t["num_recv_frames"] is None else t["num_recv_frames"],
            "recv_frame_size": "" if t["recv_frame_size"] is None else t["recv_frame_size"],
            "file_buffer_bytes": t["file_buffer_bytes"],
            "exit_code": self.exit_code,
            "elapsed_s": round(self.elapsed_s, 3),
            "samples": self.samples,
            "expected_samples": self.expected_samples,
            "missing_fraction": round(self.missing_fraction, 6),
            "overflows": self.overflows,
            "host_drops": self.host_drops,
            "timeouts": self.timeouts,
            "bytes": self.bytes,
            "write_mbps": round(self.write_mbps, 2),
            "clean": self.clean,
            "error": " | ".join(self.errors),
        }


def merge_device_args(base: str, overrides: dict[str, int | None]) -> str:
    """Return the UHD device-args string ``base`` with some keys replaced.

    A key with the value ``None`` keeps the value in ``base``. Keys keep
    their order, and new keys go at the end, so the command line stays
    easy to compare with the config.
    """
    items: list[tuple[str, str]] = []
    for part in base.split(","):
        part = part.strip()
        if not part:
            continue
        key, _, value = part.partition("=")
        items.append((key.strip(), value.strip()))
    for key, new in overrides.items():
        if new is None:
            continue
        for i, (k, _) in enumerate(items):
            if k == key:
                items[i] = (key, str(new))
                break
        else:
            items.append((key, str(new)))
    return ",".join(f"{k}={v}" if v else k for k, v in items)


def plan_trials(cfg: BufferTestConfig) -> list[Trial]:
    """Every combination of the configured lists, in a stable order."""
    frames: list[int | None] = list(cfg.num_recv_frames) or [None]
    sizes: list[int | None] = list(cfg.recv_frame_size) or [None]
    combos = itertools.product(cfg.write_modes, cfg.spb, frames, sizes, cfg.file_buffer_bytes)
    return [
        Trial(i, mode, spb, nrf, rfs, fbb)
        for i, (mode, spb, nrf, rfs, fbb) in enumerate(combos, start=1)
    ]


def select_sdr(config: OnboardConfig) -> SdrConfig:
    """The SDR named in ``[buffer_test]``, or the first SDR."""
    wanted = config.buffer_test.sdr_id
    for sdr in config.sdr:
        if not wanted or sdr.id == wanted:
            return sdr
    raise ValueError(f"no [[sdr]] with id {wanted!r}")


def trial_capture(capture: CaptureConfig, trial: Trial) -> CaptureConfig:
    """The capture settings for one trial (the rest stays as configured)."""
    return capture.model_copy(
        update={
            "write_mode": trial.write_mode,
            "spb": trial.spb,
            "file_buffer_bytes": trial.file_buffer_bytes,
        }
    )


def trial_command(config: OnboardConfig, sdr: SdrConfig, trial: Trial, outdir: Path) -> list[str]:
    """The recorder argv for one trial. It stops by itself (``--duration``)."""
    uhd_args = merge_device_args(
        sdr.uhd_args,
        {"num_recv_frames": trial.num_recv_frames, "recv_frame_size": trial.recv_frame_size},
    )
    return build_recorder_command(
        config.capture.recorder_binary,
        sdr,
        trial_capture(config.capture, trial),
        outdir,
        f"buffertest_{trial.index:03d}",
        sync_epoch=0.0,
        start_time=0.0,
        duration_s=config.buffer_test.duration_s,
        uhd_args=uhd_args,
    )


def summarize(trial: Trial, exit_code: int, lines: Iterable[str], rate_hz: float) -> TrialResult:
    """Read the recorder's JSON lines and fill one ``TrialResult``.

    The ``stopped`` line has the totals. When it is missing (a crash),
    the last ``status`` line gives the best partial numbers.
    """
    result = TrialResult(trial=trial, exit_code=exit_code)
    last_status: dict | None = None
    stopped: dict | None = None
    for line in lines:
        event = parse_status_line(line)
        if event is None:
            continue
        kind = event.get("event")
        if kind == "status":
            last_status = event
        elif kind == "stopped":
            stopped = event
        elif kind == "error":
            result.errors.append(str(event.get("message", "recorder error")))
    source = stopped or last_status or {}
    result.samples = int(source.get("samples", 0))
    result.overflows = int(source.get("overflows", 0))
    result.host_drops = int(source.get("host_drops", 0))
    result.timeouts = int(source.get("timeouts", 0))
    result.bytes = int(source.get("bytes", 0))
    result.elapsed_s = float(source.get("elapsed_s", 0.0))
    result.expected_samples = int(round(rate_hz * result.elapsed_s))
    if stopped is None:
        result.errors.append("no stopped summary from the recorder")
    return result


def subprocess_runner(argv: list[str], timeout_s: float) -> tuple[int, list[str]]:
    """Run the recorder, wait for it, and return its exit code and lines."""
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        lines = out.splitlines()
        lines.append(json.dumps({"event": "error", "message": f"timeout after {timeout_s:.0f} s"}))
        return -1, lines
    except OSError as exc:
        return -1, [json.dumps({"event": "error", "message": f"cannot launch: {exc}"})]
    return proc.returncode, proc.stdout.splitlines()


def write_csv(path: Path, results: list[TrialResult]) -> None:
    """Write all results, one row per trial. Called after every trial,
    so an interrupted test still leaves the rows it finished."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in results:
            writer.writerow(r.csv_row())


def run_buffer_test(
    config: OnboardConfig,
    runner: Runner = subprocess_runner,
    sleep: Callable[[float], None] = time.sleep,
    report: Callable[[str], None] = print,
) -> list[TrialResult]:
    """Run every trial on the NUC and write the CSV. Return the results."""
    cfg = config.buffer_test
    sdr = select_sdr(config)
    trials = plan_trials(cfg)
    # The recorder needs time to open the device and lock before the
    # streaming time starts, and time to close files after it.
    timeout_s = cfg.duration_s + config.capture.ready_timeout_s + config.capture.stop_timeout_s
    results: list[TrialResult] = []
    report(f"Buffer test on {sdr.id}: {len(trials)} trial(s) of {cfg.duration_s:.0f} s")
    for trial in trials:
        trial_dir = cfg.outdir / f"trial_{trial.index:03d}"
        argv = trial_command(config, sdr, trial, trial_dir)
        log.info("trial %d: %s", trial.index, " ".join(argv))
        code, lines = runner(argv, timeout_s)
        result = summarize(trial, code, lines, sdr.sample_rate_hz)
        results.append(result)
        write_csv(cfg.results_csv, results)
        report(describe(result))
        if not cfg.keep_files and trial_dir.exists():
            shutil.rmtree(trial_dir, ignore_errors=True)
        if trial is not trials[-1]:
            sleep(cfg.settle_s)
    clean = [r for r in results if r.clean]
    report(f"{len(clean)} of {len(results)} trial(s) clean. Results: {cfg.results_csv}")
    return results


def describe(result: TrialResult) -> str:
    """One readable line for the console."""
    t = result.trial
    frames = "-" if t.num_recv_frames is None else str(t.num_recv_frames)
    size = "-" if t.recv_frame_size is None else str(t.recv_frame_size)
    verdict = "CLEAN" if result.clean else "LOSS"
    text = (
        f"#{t.index:03d} {t.write_mode:8s} spb={t.spb:<7d} frames={frames:<5s} "
        f"frame_size={size:<6s} file_buf={t.file_buffer_bytes:<8d} -> {verdict} "
        f"ovf={result.overflows} drops={result.host_drops} "
        f"missing={result.missing_fraction:.4%} {result.write_mbps:.1f} MB/s"
    )
    if result.errors:
        text += " ERROR: " + " | ".join(result.errors)
    return text
