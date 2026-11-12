"""Simulate tab on real ArduCopter firmware (SITL), optionally with the real flight controller and NUC in the loop.

Two connection kinds, both flying the X6 MAX frame model (sitl/x6max.json) with the aircraft's tune
(sitl/x6max_sitl.parm) in ArduPilot's multicopter physics:

  sitl    ArduCopter SITL only. The payload strip uses the simulated payload.

  dryrun  Dry-run data capture. SITL flies the mission; the real flight controller stays disarmed on
          stock firmware and only carries MAVLink between SITL and the NUC on TELEM2:

            planner ──TCP──▶ SITL SERIAL0          (planner flies SITL exactly as it flies the drone)
                            SITL SERIAL1 ──TCP──▶ bridge ──USB──▶ real FC ──TELEM2──▶ NUC

          The bridge passes SITL's flight state (heartbeat, MISSION_CURRENT, MISSION_ITEM_REACHED,
          SYSTEM_TIME) and anything addressed to the payload (component 191) to the flight controller,
          and passes everything the NUC sends back to SITL. ArduPilot's own MAVLink routing does the
          rest, so planner payload commands and NUC status travel the same path they do in flight.

          SITL runs as a different system ID (default 2) because a flight controller drops packets
          that carry its own system and component ID. The NUC must follow that ID for the dry run:
          HERON_SYSTEM_ID=2 in /etc/heron/payload.env, then restart heron-payload. Set it back to 1
          before a real flight.

Nothing in the dry run is addressed to the real flight controller, so it cannot be armed from here.
"""
from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

from engine.flight import FlightError, FlightService, MavlinkLink

ROOT = Path(__file__).resolve().parents[1]
FRAME_JSON = ROOT / "sitl" / "x6max.json"
FRAME_PARM = ROOT / "sitl" / "x6max_sitl.parm"
RUN_DIR = ROOT / "output" / "sitl_sim"
DEFAULT_HOME_ALT = 1582.0          # Boulder Reservoir pads, metres MSL (same as scripts/sitl.py)
INSTANCE = 5                       # -I5: SERIAL0 on TCP 5810, SERIAL1 on 5812; leaves 5760 for scripts/sitl.py
PAYLOAD_COMPONENT = 191
DRYRUN_SYSTEM = 2
SITL_KINDS = {"sitl", "dryrun"}

# SITL -> flight controller: only what the NUC's agent listens to from the autopilot.
TO_FC_TYPES = {"HEARTBEAT", "MISSION_CURRENT", "MISSION_ITEM_REACHED", "SYSTEM_TIME"}

_running = set()


@atexit.register
def _kill_all():
    for proc in list(_running):
        proc.stop()


def ardupilot_dir() -> Path:
    return Path(os.environ.get("ARDUPILOT", Path.home() / "ardupilot")).expanduser()


def sitl_binary() -> Path:
    base = ardupilot_dir() / "build" / "sitl" / "bin"
    for name in ("arducopter", "arducopter.exe"):
        if (base / name).exists():
            return base / name
    return base / "arducopter"


def forward_to_fc(msg, system: int) -> bool:
    """SITL -> real flight controller."""
    if getattr(msg, "target_component", None) == PAYLOAD_COMPONENT and getattr(msg, "target_system", None) == system:
        return True                                    # planner commands to the NUC, SITL replies to the NUC
    return (msg.get_srcSystem(), msg.get_srcComponent()) == (system, 1) and msg.get_type() in TO_FC_TYPES


def forward_to_sitl(msg) -> bool:
    """Real flight controller -> SITL: everything the NUC sends, nothing from the flight controller itself."""
    return msg.get_srcComponent() == PAYLOAD_COMPONENT


def sim_params(frame: dict, system: int) -> dict:
    """Settings layered on top of the X6 MAX tune for this run."""
    out = {
        "SYSID_THISMAV": system,
        "SERIAL1_PROTOCOL": 2,                         # MAVLink2 on the bridge port
        "SIM_BATT_VOLTAGE": frame.get("maxVoltage", 25.2),   # 6S pack, so BATT_* failsafe volts apply unscaled
        "LOG_DISARMED": 0,
    }
    if frame.get("battCapacityAh"):
        out["SIM_BATT_CAP_AH"] = frame["battCapacityAh"]
    return out


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


class SitlProcess:
    """One headless ArduCopter SITL running the X6 MAX model."""

    def __init__(self, home, system=1, heading=0.0, mass_kg=None, instance=INSTANCE, run_dir=RUN_DIR):
        self.instance = instance
        self.port = 5760 + 10 * instance
        self.serial1 = self.port + 2
        self.run_dir = Path(run_dir)
        self.proc = None
        binary = sitl_binary()
        if not binary.exists():
            raise FlightError(
                f"ArduCopter SITL is not built ({binary} is missing). Build it once: "
                "git clone --branch Copter-4.6.3 --recurse-submodules https://github.com/ArduPilot/ardupilot ~/ardupilot, "
                "then cd ~/ardupilot && ./waf configure --board sitl && ./waf copter. "
                "Set ARDUPILOT if it lives somewhere else.")
        if _port_open(self.port):
            raise FlightError(f"TCP {self.port} is already in use. Another SITL from HERON may still be running; stop it first.")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        frame = json.loads(FRAME_JSON.read_text())
        frame.setdefault("num_motors", 6)              # hexa X; only silences SITL's 4-motor warning
        if mass_kg:
            frame["mass"] = float(mass_kg)
        # SITL opens the model relative to its working folder, so the frame file sits next to it.
        (self.run_dir / "x6max.json").write_text(json.dumps(frame, indent=2))
        extra = self.run_dir / "heron_sim.parm"
        extra.write_text("".join(f"{k} {v}\n" for k, v in sim_params(frame, system).items()))
        defaults = [ardupilot_dir() / "Tools" / "autotest" / "default_params" / "copter.parm", FRAME_PARM, extra]
        lat, lon = float(home["lat"]), float(home["lon"])
        alt = float(home.get("alt_msl") or DEFAULT_HOME_ALT)
        self.cmd = [str(binary), "--model", "hexax:x6max.json", "--speedup", "1",
                    "--home", f"{lat:.7f},{lon:.7f},{alt:g},{heading:g}",
                    "--defaults", ",".join(str(p) for p in defaults), "-w", "-I", str(instance)]
        self.log = self.run_dir / "sim.log"
        self.proc = subprocess.Popen(self.cmd, cwd=self.run_dir, stdout=open(self.log, "w"), stderr=subprocess.STDOUT)
        _running.add(self)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                tail = "\n".join(self.log.read_text(errors="replace").splitlines()[-8:])
                self.stop()
                raise FlightError(f"SITL exited (code {self.proc.returncode}). Last lines of {self.log}:\n{tail}")
            # Wait for SITL to listen, without connecting: SITL boots on its first SERIAL0 client.
            text = self.log.read_text(errors="replace")
            if "Waiting for connection" in text or "bind port" in text:
                return
            time.sleep(0.3)
        self.stop()
        raise FlightError(f"SITL never opened TCP {self.port}. See {self.log}.")

    def stop(self):
        _running.discard(self)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class Bridge:
    """Carries MAVLink between SITL's SERIAL1 and the real flight controller's USB port."""

    def __init__(self, sitl_port, device, baud, system, event, clock=time.monotonic):
        os.environ["MAVLINK20"] = "1"
        from pymavlink import mavutil
        self.system, self.event, self.clock = system, event, clock
        self.device = device
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.started = clock()
        self.fc_system = None
        self.fc_at = None
        self.nuc_at = None
        self.nuc_system = None
        self.counts = {"to_fc": 0, "to_sitl": 0}
        self._warned = {}
        try:
            self.fc = mavutil.mavlink_connection(device, baud=int(baud), source_system=255, source_component=195,
                                                 dialect="ardupilotmega", autoreconnect=False)
        except OSError as exc:
            raise FlightError(f"Could not open the flight controller on {device}: {exc}. "
                              "Close Mission Planner/QGroundControl and disconnect it on the Fly tab.") from exc
        try:
            self.sitl = mavutil.mavlink_connection(f"tcp:127.0.0.1:{sitl_port}", source_system=255,
                                                   source_component=195, dialect="ardupilotmega", retries=20)
        except Exception:
            self.fc.close()
            raise
        self.threads = [
            threading.Thread(target=self._pump, args=(self.sitl, self.fc, self._to_fc), daemon=True, name="dryrun-sitl-fc"),
            threading.Thread(target=self._pump, args=(self.fc, self.sitl, self._to_sitl), daemon=True, name="dryrun-fc-sitl"),
            threading.Thread(target=self._watch, daemon=True, name="dryrun-watch"),
        ]
        for t in self.threads:
            t.start()

    def _to_fc(self, msg):
        if forward_to_fc(msg, self.system):
            self.counts["to_fc"] += 1
            return True
        return False

    def _to_sitl(self, msg):
        now = self.clock()
        if msg.get_srcComponent() == 1 and msg.get_type() == "HEARTBEAT" and msg.get_srcSystem() != self.system:
            with self.lock:
                if self.fc_at is None:
                    self.event(f"Dry run: flight controller on {self.device} (system {msg.get_srcSystem()}) is passing data.")
                self.fc_system, self.fc_at = msg.get_srcSystem(), now
        if not forward_to_sitl(msg):
            if msg.get_srcSystem() == self.system and msg.get_srcComponent() == 1 and msg.get_type() == "HEARTBEAT":
                self._warn("fc_sysid", f"The flight controller on {self.device} also uses system {self.system}. "
                                       "Restart the dry run with a different system ID.")
            return False
        if msg.get_type() == "HEARTBEAT":
            with self.lock:
                first = self.nuc_at is None or self.nuc_system != msg.get_srcSystem()
                self.nuc_system, self.nuc_at = msg.get_srcSystem(), now
            if msg.get_srcSystem() != self.system:
                self._warn("nuc_sysid", f"The NUC answers as system {msg.get_srcSystem()}. For the dry run set "
                                        f"HERON_SYSTEM_ID={self.system} in /etc/heron/payload.env and restart heron-payload "
                                        "(set it back to 1 before flying).")
            elif first:
                self.event("Dry run: NUC heard through the flight controller's TELEM2.")
        self.counts["to_sitl"] += 1
        return True

    def _warn(self, key, text, every=30):
        now = self.clock()
        if now - self._warned.get(key, -1e9) >= every:
            self._warned[key] = now
            self.event(text, "heron", 4)

    def _pump(self, src, dst, keep):
        while not self.closed.is_set():
            try:
                msg = src.recv_match(blocking=True, timeout=0.2)
                if msg is None or msg.get_type() == "BAD_DATA":
                    continue
                if keep(msg):
                    dst.write(msg.get_msgbuf())
            except Exception as exc:
                if not self.closed.is_set():
                    self.event(f"Dry run link error: {exc}", "heron", 3)
                    self.closed.wait(1)

    def _watch(self):
        while not self.closed.wait(2):
            now = self.clock()
            if now - self.started > 10:
                if self.fc_at is None or now - self.fc_at > 5:
                    self._warn("fc", f"No heartbeat from the flight controller on {self.device}. Check the USB cable.")
                elif self.nuc_at is None or now - self.nuc_at > 5:
                    self._warn("nuc", "No payload heartbeat through the flight controller. Check the NUC is powered, "
                                      f"heron-payload is running with HERON_SYSTEM_ID={self.system}, and TELEM2 is wired.")

    def status(self):
        now = self.clock()
        with self.lock:
            return {
                "device": self.device,
                "fc_ok": self.fc_at is not None and now - self.fc_at < 5,
                "fc_system": self.fc_system,
                "nuc_ok": self.nuc_at is not None and now - self.nuc_at < 5 and self.nuc_system == self.system,
                "nuc_system": self.nuc_system,
                "system": self.system,
                **self.counts,
            }

    def close(self):
        self.closed.set()
        for t in self.threads:
            t.join(timeout=1)
        for conn in (self.fc, self.sitl):
            try:
                conn.close()
            except Exception:
                pass


class SitlLink(MavlinkLink):
    """MavlinkLink to a SITL this link starts and stops; with a bridge for the dry run."""

    def __init__(self, options, event, peer=None):
        kind = options.get("transport")
        if kind not in SITL_KINDS:
            raise FlightError("Unknown simulator kind.")
        home = options.get("home") or {}
        if "lat" not in home or "lon" not in home:
            raise FlightError("Build a mission (or place the launch pad) first; SITL starts on the launch pad.")
        self.kind = kind
        self.uses_real_payload = kind == "dryrun"
        system = int(options.get("system_id") or (DRYRUN_SYSTEM if kind == "dryrun" else 1))
        device = options.get("device") if kind == "dryrun" else None
        self.peer, self.device, self.bridge, self.process = peer, device, None, None
        if kind == "dryrun":
            if not device:
                raise FlightError("Pick the flight controller's USB port for the dry run.")
            self._claim_port(device)
        try:
            self.process = SitlProcess(home, system=system, heading=float(options.get("heading") or 0),
                                       mass_kg=options.get("mass_kg"))
            super().__init__({"transport": "tcp", "host": "127.0.0.1", "port": self.process.port,
                              "system_id": system}, event)
            self.s["transport"] = kind
            if kind == "dryrun":
                self.bridge = Bridge(self.process.serial1, device, options.get("baud") or 115200, system, event)
                event(f"Dry run: SITL is system {system}. Real flight controller stays disarmed; it only relays to the NUC.")
        except Exception:
            self._teardown()
            raise

    def _claim_port(self, device):
        peer = self.peer
        if peer is None:
            return
        with peer.lock:
            info = peer.link_info or {}
            if peer.link is not None and info.get("device") == device:
                tel = peer.link.snapshot()
                if peer.job.get("state") == "running" or tel.get("armed"):
                    raise FlightError(f"The Fly tab is using {device} with the aircraft armed or busy.")
                fly_owned = True
            else:
                fly_owned = False
            peer.reserved_ports.add(device)
        if fly_owned:
            peer.disconnect(manual=True)
            peer.event(f"Released {device} for the Simulate tab's dry run.")

    def _update(self, kind, m, now):
        if kind == "MISSION_ITEM_REACHED":
            self.s["mission_reached"] = m.seq
        super()._update(kind, m, now)

    def snapshot(self):
        s = super().snapshot()
        s["simulator"] = self.kind
        if self.bridge:
            s["dryrun"] = self.bridge.status()
        return s

    def _teardown(self):
        if self.bridge:
            self.bridge.close()
            self.bridge = None
        if self.process:
            self.process.stop()
        if self.device and self.peer is not None:
            self.peer.reserved_ports.discard(self.device)

    def close(self):
        try:
            super().close()
        finally:
            self._teardown()


class SimService(FlightService):
    """The Simulate tab's flight service: the quick demo plus ArduCopter SITL and the dry run."""

    def __init__(self, *args, peer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.peer = peer

    def connect(self, options):
        kind = options.get("transport", "demo")
        if kind not in SITL_KINDS:
            return super().connect(options)
        with self.operation_lock:
            if self.link is not None:
                raise FlightError("Stop the current simulator first.")
            self.cancel.clear()
            self.link = SitlLink(options, self.event, peer=self.peer)
            self.link_info = {"transport": kind, "device": None, "baud": None}
            self.verified = None
            self.onboard = None
            self.event("ArduCopter SITL (X6 MAX model) connected." if kind == "sitl"
                       else "Dry run started: ArduCopter SITL flies, the real flight controller relays to the NUC.")
            self.job = {"state": "idle", "action": None,
                        "message": "SITL is booting. Wait for GPS and EKF, then refresh checks."}
            return self.snapshot()
