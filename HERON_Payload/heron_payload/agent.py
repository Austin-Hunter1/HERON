"""Headless NUC service.

    python -m heron_payload.agent --connect /dev/serial/by-id/usb-FTDI_... --http 0.0.0.0:8080

* Talks plain MAVLink to the Cube on TELEM2 as component 191 (see protocol.py).
* Records with the UHD recorder workers (backends.py).
* Serves the Payload Setup web app for bench configuration over Ethernet (setup_app.py).
"""
import argparse
import collections
import json
import os
import signal
import threading
import time
from pathlib import Path

from . import protocol as P
from .backends import FakeBackend, UHDBackend
from .config import atomic_json
from .storage import SYSTEM_SSD


class Agent:
    """MAVLink side of the payload. `conn` is a pymavlink connection (anything with .mav and recv_match)."""

    def __init__(self, connection, controller, settings_path, system=1, clock=time.monotonic):
        self.conn, self.controller = connection, controller
        self.settings_path = Path(settings_path)
        self.system, self.clock = system, clock
        self.tx = threading.Lock()
        self.stopping = threading.Event()
        self.acks = collections.OrderedDict()          # (src sys, src comp, request id) -> (result, reason)
        self.last_event = None
        self.last_setup = None
        self.boot = clock()
        self._heartbeat_at = self._status_at = self._interval_at = -1e9
        self._last_boot_ms = None

    # ---- sending -------------------------------------------------------------------------
    def send(self, method, *args):
        with self.tx:
            getattr(self.conn.mav, method)(*args)

    def text(self, message, severity=6):
        self.send("statustext_send", severity, message[:50].encode())

    def boot_ms(self):
        return int((self.clock() - self.boot) * 1000) & 0xFFFFFFFF

    def send_status(self):
        snap = self.controller.snapshot()
        for name, value in P.status_values(snap).items():
            self.send("named_value_float_send", self.boot_ms(), name.encode(), value)
        setup = setup_label(snap)
        if setup != self.last_setup:
            self.last_setup = setup
            self.text(P.SETUP_PREFIX + (setup or "none"))
        return snap

    def announce(self):
        snap = self.controller.snapshot()
        self.text(P.SETUP_PREFIX + (setup_label(snap) or "none"))
        if snap["state"] == "FAULT" and snap.get("fault"):
            self.text("HERON fault: " + snap["fault"], 3)

    def periodic(self):
        now = self.clock()
        if now - self._heartbeat_at >= 1:
            # MAV_TYPE_ONBOARD_CONTROLLER (18), MAV_AUTOPILOT_INVALID (8)
            self.send("heartbeat_send", 18, 8, 0, 0, 4)
            self._heartbeat_at = now
        if now - self._interval_at >= 15:
            # Ask the Cube for MISSION_CURRENT at 2 Hz on this port (repeat in case the Cube rebooted).
            self.send("command_long_send", self.system, 1, 511, 0, 42, 500000, 0, 0, 0, 0, 0)
            self._interval_at = now
        self.controller.tick()
        events = list(self.controller.events)
        if events and events[-1] is not self.last_event:
            # Forward every new event as a STATUSTEXT, oldest first.
            new = events[events.index(self.last_event) + 1:] if self.last_event in events else events[-1:]
            for e in new:
                severity = 3 if self.controller.state == "FAULT" and e is events[-1] else 6
                self.text("HERON " + e["text"], severity)
            self.last_event = events[-1]
        if now - self._status_at >= 1:
            self.send_status()
            self._status_at = now

    # ---- receiving -----------------------------------------------------------------------
    def receive(self, m):
        kind = m.get_type()
        src = (m.get_srcSystem(), m.get_srcComponent())
        if kind == "COMMAND_LONG":
            if m.target_system == self.system and m.target_component == P.COMPONENT and m.command == P.COMMAND:
                self.command(m, src)
            return
        if src != (self.system, 1):            # only listen to the autopilot for flight state
            return
        if kind == "HEARTBEAT":
            if m.type in (6, 18, 26, 27):       # ignore GCS / companion heartbeats relayed with sys id 1
                return
            self.controller.cube_update(armed=bool(m.base_mode & 128), mode=P.COPTER_MODES.get(m.custom_mode, str(m.custom_mode)))
        elif kind == "MISSION_CURRENT":
            self.controller.cube_update(current=m.seq)
        elif kind == "MISSION_ITEM_REACHED":
            self.controller.cube_update(reached=m.seq)
        elif kind == "SYSTEM_TIME":
            # The Cube rebooted (its boot clock jumped back): any armed plan refers to an old mission.
            if self._last_boot_ms is not None and m.time_boot_ms + 5000 < self._last_boot_ms:
                self.controller.cube_update(changed=True)
            self._last_boot_ms = m.time_boot_ms

    def command(self, m, src):
        action = P.ACTIONS.get(int(round(m.param1)))
        request = int(round(m.param4))
        key = (src[0], src[1], request)
        if request and key in self.acks:              # a retry: answer again, do not act twice
            result, reason = self.acks[key]
        else:
            result, reason = P.RESULT_ACCEPTED, ""
            try:
                self.controller.ground_seen = self.clock()
                if action is None:
                    raise ValueError("unknown payload action %g" % m.param1)
                if action == "status":
                    self.announce()
                else:
                    data = {}
                    if action == "arm":
                        data = dict(start_seq=int(round(m.param2)), stop_seq=int(round(m.param3)))
                    self.controller.execute(action, data)
            except ValueError as exc:
                result, reason = P.RESULT_DENIED, str(exc)
            except Exception as exc:          # hardware failure while acting
                result, reason = P.RESULT_FAILED, str(exc)
            if request:
                self.acks[key] = (result, reason)
                while len(self.acks) > 64:
                    self.acks.popitem(last=False)
            if reason:
                self.text("HERON: " + reason, 4)
        self.send("command_ack_send", P.COMMAND, result, 100, request, src[0], src[1])
        self._status_at = -1e9                       # push fresh status right away

    # ---- main loop -----------------------------------------------------------------------
    def run(self):
        while not self.stopping.is_set():
            try:
                self.periodic()
            except Exception as exc:
                self.controller.event("Agent error: " + str(exc))
            m = self.conn.recv_match(blocking=True, timeout=0.1)
            if m is not None and m.get_type() != "BAD_DATA":
                try:
                    self.receive(m)
                except Exception as exc:
                    self.controller.event("Bad message: " + str(exc))
        try:
            self.controller.execute("stop")
        finally:
            self.controller.backend.close()


def setup_label(snap):
    """Setup name for the planner strip, marked when recordings go to the NUC's own SSD."""
    setup = snap.get("setup")
    if setup and (snap.get("storage") or {}).get("drive") == SYSTEM_SSD:
        return setup + " (NUC SSD)"
    return setup


def persist_active(controller, path):
    if controller.config:
        atomic_json(path, controller.config)


def main():
    parser = argparse.ArgumentParser(description="HERON payload service (NUC)")
    parser.add_argument("--connect", required=True,
                        help="Cube link: /dev/serial/by-id/..., or tcp:127.0.0.1:5762 / udpout:host:port for SITL")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--system-id", type=int, default=1)
    parser.add_argument("--backend", choices=["uhd", "fake"], default="uhd")
    parser.add_argument("--state-dir", default="/var/lib/heron", help="Active setup and saved setups live here")
    parser.add_argument("--recorder", default="/opt/heron/bin/heron-recorder")
    parser.add_argument("--ssd-dir", default=None,
                        help="Recordings folder on the NUC's own SSD, used when the data drive is not mounted "
                             "(default ~/heron_recordings)")
    parser.add_argument("--http", default="0.0.0.0:8080", help="Payload Setup web app address, or 'off'")
    args = parser.parse_args()
    if not 1 <= args.system_id <= 254:
        parser.error("system-id must be 1-254")
    os.environ["MAVLINK20"] = "1"
    from pymavlink import mavutil
    from .controller import Controller

    state = Path(args.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    active = state / "active.json"
    if args.backend == "fake":
        backend = FakeBackend(write_files=True, storage_root=state / "recordings")
    else:
        backend = UHDBackend(args.recorder, state / "runtime", ssd_dir=args.ssd_dir)
    controller = Controller(backend)
    if active.exists():
        # Boot loads the last setup; it never resumes an old recording or an armed plan.
        try:
            controller.execute("prepare", dict(config=json.loads(active.read_text())))
            controller.ground_seen = None
        except Exception as exc:
            controller.event("Boot setup failed: " + str(exc))

    if args.http != "off":
        from .setup_app import serve
        host, _, port = args.http.rpartition(":")
        serve(controller, state, host or "0.0.0.0", int(port), lambda: persist_active(controller, active),
              ssd_dir=args.ssd_dir)

    stopping = threading.Event()

    def stop(*_):
        stopping.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    # Keep the Setup app usable even if the TELEM2 adapter is missing: retry the Cube link.
    connection, warned = None, False
    while connection is None and not stopping.is_set():
        try:
            connection = mavutil.mavlink_connection(args.connect, baud=args.baud, source_system=args.system_id,
                                                    source_component=P.COMPONENT, dialect="ardupilotmega",
                                                    autoreconnect=True)
        except Exception as exc:
            if not warned:
                controller.event(f"Cannot open Cube link {args.connect}: {exc}. Retrying every 5 s.")
                warned = True
            stopping.wait(5)
    if connection is None:
        return
    agent = Agent(connection, controller, active, args.system_id)
    agent.stopping = stopping
    agent.run()


if __name__ == "__main__":
    main()
