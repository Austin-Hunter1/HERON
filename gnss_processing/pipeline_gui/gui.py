"""
The desktop window (tkinter) of the notebook pipeline.

The window only collects choices and shows progress.  All work is in
`pipeline.run_and_report`, which runs in a worker thread so the window stays
responsive.  The worker sends log lines to the window through a queue, because
tkinter must be called only from its own thread.
"""

from __future__ import annotations

import dataclasses
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .config import PipelineConfig
from .pipeline import (
    available_signals,
    default_collects_dir,
    list_channels,
    outputs_root,
    run_and_report,
)
from .plan import (
    AssistRequest,
    RunRequest,
    assist_request_from_text,
    build_steps,
    default_track_duration_ms,
    parse_p_fa_total,
    parse_reference_geo,
    parse_threshold_db,
)

POLL_MS = 200


def open_path(path: Path) -> None:
    """Open a file or folder with the program the OS uses for it."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class PipelineWindow:
    """Main window: choose a channel, choose signals, run, read the log."""

    def __init__(self, root: tk.Tk, cfg: PipelineConfig) -> None:
        self.root = root
        self.cfg = cfg
        self.channels: list = []
        self.signal_vars: dict[str, tk.BooleanVar] = {}
        # Threshold fields, one pair per signal name.  Kept across channel changes,
        # so a value typed for GpsL1CA is still there after another L1 row is clicked.
        self.p_fa_vars: dict[str, tk.StringVar] = {}
        self.threshold_db_vars: dict[str, tk.StringVar] = {}
        self.available = available_signals(
            [name for names in cfg.signals.values() for name in names]
        )
        self.messages: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.started_at = 0.0
        self.last_report: Path | None = None
        self.step_text = ""

        root.title("HERON GNSS notebook pipeline (00, 01, 02)")
        root.geometry("1100x800")
        self._build()
        collects = default_collects_dir(cfg)
        if collects is not None:
            self.collects_var.set(str(collects))
            self.reload_channels()
        root.after(POLL_MS, self._poll)

    # ----- Layout ------------------------------------------------------------

    def _build(self) -> None:
        pad = {"padx": 6, "pady": 3}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Collects folder:").pack(side="left")
        self.collects_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.collects_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(top, text="Browse...", command=self.browse_collects).pack(side="left")
        ttk.Button(top, text="Reload", command=self.reload_channels).pack(side="left", padx=4)

        columns = ("exp", "experiment", "ch", "collect", "band", "rate", "duration")
        widths = (40, 230, 40, 330, 50, 80, 90)
        table = ttk.Frame(self.root)
        table.pack(fill="both", expand=True, **pad)
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=10, selectmode="browse")
        for col, width in zip(columns, widths, strict=True):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, anchor="w", stretch=col in ("experiment", "collect"))
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.on_select())

        opts = ttk.LabelFrame(self.root, text="Run settings")
        opts.pack(fill="x", **pad)

        row = ttk.Frame(opts)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Notebooks:", width=16).pack(side="left")
        self.notebook_vars = {nb: tk.BooleanVar(value=True) for nb in ("00", "01", "02")}
        for nb, var in self.notebook_vars.items():
            ttk.Checkbutton(row, text=nb, variable=var).pack(side="left", padx=4)
        # Stops notebook 01 after its acquisition plots and skips notebook 02, so a
        # threshold can be tried in about a minute (D-085).
        self.acquisition_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Acquisition only (no tracking, no notebook 02)",
                        variable=self.acquisition_only_var).pack(side="left", padx=(24, 4))

        row = ttk.Frame(opts)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Signals:", width=16).pack(side="left", anchor="n")
        self.signals_frame = ttk.Frame(row)
        self.signals_frame.pack(side="left", fill="x")
        ttk.Label(self.signals_frame, text="(select a channel)").pack(side="left")

        row = ttk.Frame(opts)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Start offset (ms):", width=16).pack(side="left")
        self.offset_var = tk.StringVar(value=f"{self.cfg.start_offset_ms:g}")
        ttk.Entry(row, textvariable=self.offset_var, width=10).pack(side="left")
        ttk.Label(row, text="Track duration (s):").pack(side="left", padx=(16, 0))
        self.duration_var = tk.StringVar(
            value="" if self.cfg.track_duration_s is None else f"{self.cfg.track_duration_s:g}"
        )
        ttk.Entry(row, textvariable=self.duration_var, width=10).pack(side="left", padx=4)
        self.duration_hint = ttk.Label(row, text="empty = to the end of the collect", foreground="gray")
        self.duration_hint.pack(side="left")

        row = ttk.Frame(opts)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Reference position:", width=16).pack(side="left")
        self.reference_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.reference_var, width=36).pack(side="left")
        ttk.Label(row, text="lat, lon, height m  (empty = notebook 02 default; "
                            "needed for assisted acquisition)",
                  foreground="gray").pack(side="left", padx=4)

        self._build_assisted(opts, pad)

        row = ttk.Frame(opts)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Reports folder:", width=16).pack(side="left")
        self.reports_var = tk.StringVar(value=self._reports_root_text())
        ttk.Entry(row, textvariable=self.reports_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self.browse_reports).pack(side="left", padx=4)

        buttons = ttk.Frame(self.root)
        buttons.pack(fill="x", **pad)
        self.run_button = ttk.Button(buttons, text="Run", command=self.start_run)
        self.run_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop_run, state="disabled")
        self.stop_button.pack(side="left", padx=4)
        self.open_report_button = ttk.Button(
            buttons, text="Open report", state="disabled",
            command=lambda: self.last_report and open_path(self.last_report),
        )
        self.open_report_button.pack(side="left", padx=4)
        self.open_folder_button = ttk.Button(
            buttons, text="Open report folder", state="disabled",
            command=lambda: self.last_report and open_path(self.last_report.parent),
        )
        self.open_folder_button.pack(side="left")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(buttons, textvariable=self.status_var).pack(side="left", padx=12)

        self.log_box = ScrolledText(self.root, height=16, font=("Consolas", 9), state="disabled")
        self.log_box.pack(fill="both", expand=True, **pad)

    def _build_assisted(self, parent, pad) -> None:
        """
        The assisted-acquisition box (D-086).  The sky mask starts as the entire sky:
        minimum elevation from the config (0) and no blocked sector.
        """
        defaults = self.cfg.assisted
        box = ttk.LabelFrame(parent, text="Assisted acquisition (notebook 01, for a weak sky)")
        box.pack(fill="x", **pad)

        row = ttk.Frame(box)
        row.pack(fill="x", **pad)
        self.assisted_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Use assisted acquisition", variable=self.assisted_var).pack(side="left")
        ttk.Label(row, text="Time UTC:").pack(side="left", padx=(16, 0))
        self.assist_time_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.assist_time_var, width=20).pack(side="left", padx=4)
        ttk.Label(row, text="YYYY-MM-DD HH:MM:SS at the file start (empty = from the collect name)",
                  foreground="gray").pack(side="left")

        row = ttk.Frame(box)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Min. elevation (deg):").pack(side="left")
        self.assist_min_el_var = tk.StringVar(value=f"{defaults.min_elevation_deg:g}")
        ttk.Entry(row, textvariable=self.assist_min_el_var, width=6).pack(side="left", padx=4)
        ttk.Label(row, text="Blocked sectors:").pack(side="left", padx=(12, 0))
        self.assist_sectors_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.assist_sectors_var, width=30).pack(side="left", padx=4)
        ttk.Label(row, text="az_from-az_to:up_to_el; ...  e.g. 300-60:60  (empty = entire sky)",
                  foreground="gray").pack(side="left")

        row = ttk.Frame(box)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Dwell multiplier:").pack(side="left")
        self.assist_dwell_var = tk.StringVar(value=str(defaults.dwell_multiplier))
        ttk.Entry(row, textvariable=self.assist_dwell_var, width=6).pack(side="left", padx=4)
        ttk.Label(row, text="Stage 1 satellites:").pack(side="left", padx=(12, 0))
        self.assist_stage1_var = tk.StringVar(value=str(defaults.stage1_satellites))
        ttk.Entry(row, textvariable=self.assist_stage1_var, width=6).pack(side="left", padx=4)
        ttk.Label(row, text="Stage 2 window +/- (Hz):").pack(side="left", padx=(12, 0))
        self.assist_halfwidth_var = tk.StringVar(value=f"{defaults.doppler_halfwidth_hz:g}")
        ttk.Entry(row, textvariable=self.assist_halfwidth_var, width=8).pack(side="left", padx=4)

    def build_assist(self) -> AssistRequest | None:
        """Read the assisted-acquisition fields.  None when the box is off."""
        if not self.assisted_var.get():
            return None
        return assist_request_from_text(
            time_utc=self.assist_time_var.get(),
            min_elevation=self.assist_min_el_var.get(),
            sectors=self.assist_sectors_var.get(),
            dwell_multiplier=self.assist_dwell_var.get(),
            stage1_satellites=self.assist_stage1_var.get(),
            doppler_halfwidth=self.assist_halfwidth_var.get(),
        )

    def _reports_root_text(self) -> str:
        try:
            return str(outputs_root(self.cfg) / self.cfg.reports_subdir)
        except Exception:
            return ""

    # ----- Channel list ------------------------------------------------------

    def browse_collects(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.collects_var.get() or None,
                                         title="Folder of experiments (each with metadata.yml)")
        if folder:
            self.collects_var.set(folder)
            self.reload_channels()

    def browse_reports(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.reports_var.get() or None,
                                         title="Folder for the report folders")
        if folder:
            self.reports_var.set(folder)

    def reload_channels(self) -> None:
        """Read every metadata.yml under the collects folder and fill the table."""
        self.tree.delete(*self.tree.get_children())
        folder = Path(self.collects_var.get().strip())
        if not folder.is_dir():
            self.log(f"Collects folder not found: {folder}")
            return
        try:
            self.channels = list_channels(folder)
        except Exception as exc:
            self.channels = []
            self.log(f"Cannot read the collects folder: {exc}")
            return
        for i, c in enumerate(self.channels):
            self.tree.insert("", "end", iid=str(i), values=(
                c.experiment_index, c.experiment_name, c.channel_index, c.collect_id,
                c.band_id, f"{c.samp_rate / 1e6:.1f} Msps", f"{c.duration_s:,.1f} s",
            ))
        self.log(f"{len(self.channels)} channel(s) in {folder}. "
                 "A folder with no readable metadata.yml is not listed.")

    def selected_channel(self):
        selection = self.tree.selection()
        return self.channels[int(selection[0])] if selection else None

    def on_select(self) -> None:
        """Show one checkbox for each signal that the selected band carries."""
        channel = self.selected_channel()
        for child in self.signals_frame.winfo_children():
            child.destroy()
        self.signal_vars = {}
        if channel is None:
            return
        names = self.cfg.signals.get(channel.band_id, [])
        if not names:
            ttk.Label(self.signals_frame,
                      text=f"no signals for band {channel.band_id} in the config").grid(row=0, column=0)
            return
        # One row per signal: the checkbox, then the two acquisition threshold
        # fields of notebook 01 (D-085).  Empty fields keep the notebook default.
        frame = self.signals_frame
        ttk.Label(frame, text="acq. p_fa total").grid(row=0, column=1, padx=4)
        ttk.Label(frame, text="acq. threshold [dB]").grid(row=0, column=2, padx=4)
        ttk.Label(frame, text="empty = notebook default; dB wins when both are set",
                  foreground="gray").grid(row=0, column=3, padx=4, sticky="w")
        for r, name in enumerate(names, start=1):
            usable = name in self.available
            var = tk.BooleanVar(value=usable and name not in self.cfg.signals_off_by_default)
            self.signal_vars[name] = var
            text = name if usable else f"{name} (not available)"
            box = ttk.Checkbutton(frame, text=text, variable=var)
            if not usable:
                box.state(["disabled"])
            box.grid(row=r, column=0, sticky="w", padx=4)
            p_fa = self.p_fa_vars.setdefault(name, tk.StringVar())
            db = self.threshold_db_vars.setdefault(name, tk.StringVar())
            for column, field_var in ((1, p_fa), (2, db)):
                entry = ttk.Entry(frame, textvariable=field_var, width=12)
                if not usable:
                    entry.state(["disabled"])
                entry.grid(row=r, column=column, padx=4, pady=1)
        try:
            default_s = default_track_duration_ms(channel.duration_s, float(self.offset_var.get() or 0)) / 1e3
            self.duration_hint.configure(text=f"empty = {default_s:,.0f} s (to the end of the collect)")
        except ValueError:
            self.duration_hint.configure(text="empty = to the end of the collect")

    # ----- Run -----------------------------------------------------------------

    def build_request(self, channel) -> RunRequest:
        """Read and check the fields.  A bad field raises ValueError with the reason."""
        try:
            offset_ms = float(self.offset_var.get().strip() or 0)
        except ValueError:
            raise ValueError("the start offset must be a number of milliseconds.") from None
        text = self.duration_var.get().strip()
        if text:
            try:
                duration_ms = int(round(float(text))) * 1000
            except ValueError:
                raise ValueError("the track duration must be a number of seconds.") from None
        else:
            duration_ms = default_track_duration_ms(channel.duration_s, offset_ms)
        signals = tuple(n for n, v in self.signal_vars.items() if v.get())
        p_fa, threshold_db = {}, {}
        for name in signals:
            try:
                value = parse_p_fa_total(self.p_fa_vars[name].get())
                if value is not None:
                    p_fa[name] = value
                value = parse_threshold_db(self.threshold_db_vars[name].get())
                if value is not None:
                    threshold_db[name] = value
            except ValueError as exc:
                raise ValueError(f"{name}: {exc}") from None
        return RunRequest(
            experiment_index=channel.experiment_index,
            channel_index=channel.channel_index,
            signals=signals,
            notebooks=tuple(nb for nb, v in self.notebook_vars.items() if v.get()),
            start_offset_ms=offset_ms,
            track_duration_ms=duration_ms,
            reference_geo=parse_reference_geo(self.reference_var.get()),
            acq_p_fa_total=p_fa,
            acq_threshold_db=threshold_db,
            acquisition_only=self.acquisition_only_var.get(),
            assist=self.build_assist(),
        )

    def start_run(self) -> None:
        channel = self.selected_channel()
        if channel is None:
            messagebox.showwarning("No channel", "Select a channel in the table first.")
            return
        try:
            request = self.build_request(channel)
            # Check the whole plan now, so a missing value (for example the position
            # that assisted acquisition needs) is a message here, not a failed run.
            build_steps(request, self.cfg.navigation_unsupported, self.cfg.acquisition_only_stop_heading)
        except ValueError as exc:
            messagebox.showerror("Bad setting", str(exc))
            return

        cfg = self.cfg
        reports = self.reports_var.get().strip()
        if reports:
            # The window shows <outputs>/<subdir>; keep that folder as typed.
            cfg = dataclasses.replace(cfg, outputs_dir=Path(reports), reports_subdir=".")
        collects_dir = Path(self.collects_var.get().strip())

        self.stop_event.clear()
        self.run_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.started_at = time.monotonic()
        self.step_text = "Starting"

        def work() -> None:
            try:
                outcome = run_and_report(
                    cfg, collects_dir, channel, request,
                    log=lambda line: self.messages.put(("log", line)),
                    should_stop=self.stop_event.is_set,
                    on_step=lambda i, n, s: self.messages.put(("step", f"Step {i + 1} of {n}: {s.title}")),
                )
                self.messages.put(("done", outcome))
            except Exception as exc:  # show it; never leave the window stuck
                self.messages.put(("failed", f"{type(exc).__name__}: {exc}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop_run(self) -> None:
        self.stop_event.set()
        self.step_text = "Stopping"
        self.log("Stop requested. The running notebook is stopped; the report is still written.")

    # ----- Messages from the worker ------------------------------------------

    def log(self, line: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _poll(self) -> None:
        """Move worker messages into the window.  Runs on the tkinter thread."""
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "step":
                    self.step_text = payload
                elif kind == "done":
                    self._finish(payload)
                elif kind == "failed":
                    self.log(f"Run failed: {payload}")
                    self._finish(None)
        except queue.Empty:
            pass
        if self.worker is not None and self.worker.is_alive():
            minutes, seconds = divmod(int(time.monotonic() - self.started_at), 60)
            self.status_var.set(f"{self.step_text}  ({minutes:02d}:{seconds:02d})")
        self.root.after(POLL_MS, self._poll)

    def _finish(self, outcome) -> None:
        self.run_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.worker = None
        if outcome is None:
            self.status_var.set("Run failed. See the log.")
            return
        self.last_report = outcome.report_pdf
        self.open_report_button.configure(state="normal")
        self.open_folder_button.configure(state="normal")
        passed = sum(r.status == "passed" for r in outcome.results)
        self.status_var.set(f"Done: {passed} of {len(outcome.results)} step(s) passed.")


def main(cfg: PipelineConfig) -> None:
    root = tk.Tk()
    PipelineWindow(root, cfg)
    root.mainloop()
