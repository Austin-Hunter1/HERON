"""The HERON ground station terminal UI (Textual).

Shows the O10 health set, the link state with telemetry age (B3), the
GNSS receiver state, and an event log. Keys send START/STOP (after a
confirm dialog), STATUS, and PING (B2).

The app owns no protocol logic. It polls ``LinkClient`` on a timer and
renders what it finds. Replace this file to change the display.
"""

from __future__ import annotations

import time

from heron_common.protocol import CommandName, Telemetry
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Label, Log, Static

from heron_base.config import BaseConfig
from heron_base.gnss import GnssReceiver
from heron_base.link_client import LinkClient
from heron_base.link_health import LinkHealth

HEALTH_STYLE = {
    LinkHealth.CONNECTED: "bold white on dark_green",
    LinkHealth.DEGRADED: "bold black on yellow",
    LinkHealth.LOST: "bold white on dark_red",
    LinkHealth.NO_DATA: "bold white on grey30",
}


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No dialog before a START or STOP goes out."""

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    #dialog { grid-size: 2; grid-gutter: 1 2; padding: 1 2; width: 60; height: 9;
              border: thick $primary; background: $surface; }
    #question { column-span: 2; content-align: center middle; height: 3; }
    Button { width: 100%; }
    """

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Grid(id="dialog"):
            yield Label(self._question, id="question")
            yield Button("Yes", variant="error", id="yes")
            yield Button("No", variant="primary", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class HeronBaseApp(App[None]):
    """Live health display and start/stop control."""

    TITLE = "HERON ground station"
    CSS = """
    #link { height: 3; padding: 0 1; content-align: center middle; }
    #payload, #gnss { height: auto; border: round $secondary; padding: 0 1; }
    #sdrs { height: auto; max-height: 12; border: round $secondary; }
    #events { height: 1fr; border: round $secondary; }
    Vertical > Static { margin-bottom: 0; }
    """
    BINDINGS = [
        Binding("s", "start", "START recording"),
        Binding("x", "stop", "STOP recording"),
        Binding("t", "status", "Request status"),
        Binding("p", "ping", "Ping"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self, config: BaseConfig, client: LinkClient, gnss: GnssReceiver | None = None
    ) -> None:
        super().__init__()
        self._cfg = config
        self._client = client
        self._gnss = gnss
        self._events_shown = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield Static("link", id="link")
            with Horizontal():
                yield Static("payload", id="payload")
                yield Static("gnss", id="gnss")
            yield DataTable(id="sdrs")
            yield Log(id="events", max_lines=self._cfg.display.event_lines)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#sdrs", DataTable)
        table.add_columns(
            "SDR", "present", "stream", "ref", "overflow", "drops", "seg", "MB/s", "fault"
        )
        table.cursor_type = "none"
        self._client.open()
        if self._gnss is not None:
            self._gnss.start()
        self.set_interval(self._cfg.link_health.poll_s, self._poll)
        self.set_interval(1.0 / self._cfg.display.refresh_hz, self._refresh)

    # ----- data ---------------------------------------------------------------------

    def _poll(self) -> None:
        self._client.poll()

    def _refresh(self) -> None:
        now = time.time()
        health = self._client.health(now)
        age = self._client.telemetry_age(now)
        age_text = "never" if age is None else f"{age:.1f} s"
        link = self.query_one("#link", Static)
        link.update(
            f"LINK {health}   telemetry age {age_text}   frames ok/bad "
            f"{self._client.telemetry_count}/{self._client.bad_frames}"
        )
        link.styles.background = self._health_colour(health)
        link.styles.color = "white"
        link.styles.text_style = "bold"

        tlm = self._client.latest
        self.query_one("#payload", Static).update(self._payload_text(tlm, now))
        self.query_one("#gnss", Static).update(self._gnss_text(now))
        self._fill_table(tlm)

        log = self.query_one("#events", Log)
        events = list(self._client.events)
        if len(events) < self._events_shown:
            self._events_shown = 0
            log.clear()
        for line in events[self._events_shown :]:
            log.write_line(line)
        self._events_shown = len(events)

    @staticmethod
    def _health_colour(health: LinkHealth) -> str:
        return {
            LinkHealth.CONNECTED: "darkgreen",
            LinkHealth.DEGRADED: "darkgoldenrod",
            LinkHealth.LOST: "darkred",
            LinkHealth.NO_DATA: "grey",
        }[health]

    def _payload_text(self, tlm: Telemetry | None, now: float) -> str:
        if tlm is None:
            return "PAYLOAD: no telemetry yet"
        temps = " ".join(f"{k}={v:.0f}C" for k, v in sorted(tlm.temps_c.items())[:4])
        fault = f"\n[b red]FAULT: {tlm.fault}[/]" if tlm.fault else ""
        disk = f"{tlm.disk_free_gb:.1f} GB ({tlm.disk_free_pct:.0f}%)"
        if tlm.disk_alert:
            disk = f"[b red]{disk} LOW[/]"
        pending = self._client.pending
        pend = f"\npending: {pending.summary()}" if pending and not pending.done else ""
        return (
            f"PAYLOAD  state [b]{tlm.state}[/]  flight {tlm.flight_id or '-'}  "
            f"rec {tlm.recording_s:.0f} s\n"
            f"rate {tlm.data_rate_mbps:.1f} MB/s  disk {disk}  cpu {tlm.cpu_pct:.0f}%  {temps}\n"
            f"uptime {tlm.uptime_s:.0f} s  version {tlm.version}  last cmd seq {tlm.last_cmd_seq}"
            f"{fault}{pend}"
        )

    def _gnss_text(self, now: float) -> str:
        if self._gnss is None or not self._cfg.gnss.enabled:
            return "GNSS: disabled in config"
        st = self._gnss.status()
        port = "open" if st.port_open else f"closed ({st.error or 'retrying'})"
        rtcm = ", ".join(f"{k}:{v}" for k, v in sorted(st.rtcm_types.items())[:6])
        return (
            f"GNSS  port {port}\n"
            f"fix {st.fix_text(now, self._cfg.gnss.stale_fix_s)}\n"
            f"raw {st.bytes_logged / 1e6:.2f} MB  RTCM {st.rtcm_frames} [{rtcm}]"
        )

    def _fill_table(self, tlm: Telemetry | None) -> None:
        table = self.query_one("#sdrs", DataTable)
        table.clear()
        if tlm is None:
            return
        for s in tlm.sdrs:
            ref = "-" if s.ref_locked is None else ("LOCK" if s.ref_locked else "[b red]NO LOCK[/]")
            fault = f"[b red]{s.fault}[/]" if s.fault else ""
            ovf = f"[b red]{s.overflows}[/]" if s.overflows else "0"
            table.add_row(
                s.id,
                "yes" if s.present else "[b red]NO[/]",
                "RUN" if s.streaming else "-",
                ref,
                ovf,
                str(s.host_drops),
                str(s.segment),
                f"{s.rate_mbps:.1f}",
                fault,
            )

    # ----- actions --------------------------------------------------------------------

    def action_start(self) -> None:
        def go(confirmed: bool | None) -> None:
            if confirmed:
                self._client.send_command(CommandName.START)

        self.push_screen(ConfirmScreen("Send START recording to the payload?"), go)

    def action_stop(self) -> None:
        def go(confirmed: bool | None) -> None:
            if confirmed:
                self._client.send_command(CommandName.STOP)

        self.push_screen(ConfirmScreen("Send STOP recording to the payload?"), go)

    def action_status(self) -> None:
        self._client.send_command(CommandName.STATUS)

    def action_ping(self) -> None:
        self._client.send_command(CommandName.PING)

    async def action_quit(self) -> None:
        if self._gnss is not None:
            self._gnss.stop()
        self._client.close()
        self.exit()
