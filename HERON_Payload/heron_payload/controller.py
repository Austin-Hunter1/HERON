"""One recording state machine for simulation and the NUC."""
import copy
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from . import health
from .config import digest, estimate, validate

TEMP_EVERY_S = 2.0          # how often the NUC temperatures are read
HEALTH_LOG_EVERY_S = 5.0    # nuc_health.csv row interval while recording


class Controller:
    def __init__(self, backend, clock=time.monotonic, read_temps=None):
        self.backend, self.clock = backend, clock
        # NUC temperatures: read every TEMP_EVERY_S, sent with the status, and written to
        # nuc_health.csv in the recording folder every HEALTH_LOG_EVERY_S while recording.
        self.read_temps = read_temps or health.read_temps
        self.temps = {"cpu_c": None, "ssd_c": None}
        self.temps_at = None
        self.health_logged_at = None
        self.hot = set()
        self.lock = threading.RLock()
        self.state = "IDLE"
        self.config = None
        self.plan = None
        self.fault = None
        self.events = deque(maxlen=40)
        self.session = None
        self.started = None
        self.origin = None
        self.cube = {"connected": False, "armed": False, "mode": None, "current": None}
        self.cube_seen = None
        self.ground_seen = None
        self.prepared_at = None
        self.fallback_suppressed = False
        self.event("Payload service started")

    def event(self, text):
        self.events.append(dict(time=datetime.now(timezone.utc).isoformat(), text=str(text)[:240]))

    def execute(self, action, data=None):
        data = data or {}
        with self.lock:
            self.ground_seen = self.clock()
            if action == "prepare":
                if self.state in ("RECORDING", "FINALIZING", "AUTO_ARMED"):
                    raise ValueError("Stop or cancel automatic recording before changing setup.")
                self.state = "PREPARING"
                self.plan = None
                self.fault = None
                try:
                    self.config = validate(data["config"], hardware=not self.backend.simulated)
                    self.backend.prepare(self.config)
                    self.state = "READY"
                    self.prepared_at = self.clock()
                    self.fallback_suppressed = False
                    self.event("Setup loaded: " + self.config["name"])
                    where = (self.backend.status() or {}).get("storage") or {}
                    if where.get("fallback"):
                        self.event(where["note"])
                except Exception as exc:
                    self.state, self.fault = "FAULT", str(exc)
                    self.backend.close()
                    self.event(self.fault)
                    raise
            elif action == "start":
                self.plan = None
                self.fallback_suppressed = True
                self._start(data.get("origin", "manual"))
            elif action == "stop":
                if self.state not in ("RECORDING", "AUTO_ARMED"):
                    self.plan = None
                    self.fallback_suppressed = True
                    return self.snapshot()
                self._stop("REC STOP (operator)", suppress=True)
            elif action == "cancel_auto":
                if self.state == "RECORDING":
                    raise ValueError("Recording in progress; use Stop.")
                self.plan = None
                self.fallback_suppressed = True
                if self.state == "AUTO_ARMED":
                    self.state = "READY"
                    self.event("Auto-record cancelled")
            elif action == "arm":
                if self.state == "AUTO_ARMED" and self.plan and \
                        (self.plan["start_seq"], self.plan["stop_seq"]) == (data.get("start_seq"), data.get("stop_seq")):
                    return self.snapshot()
                if self.state != "READY":
                    raise ValueError({"IDLE": "No setup loaded on the payload.",
                                      "RECORDING": "Already recording; stop first.",
                                      "FAULT": "Payload fault: " + str(self.fault)}.get(self.state, "Payload is not ready."))
                start, stop = data.get("start_seq"), data.get("stop_seq")
                if type(start) is not int or type(stop) is not int or not 1 <= start < stop <= 999:
                    raise ValueError("Start waypoint must come before the stop waypoint.")
                if self.cube["current"] is not None and self.cube["current"] > start and self.cube["mode"] == "AUTO":
                    raise ValueError(f"Aircraft is already past mission item {start}. Use Record now.")
                self.plan = dict(start_seq=start, stop_seq=stop)
                self.state = "AUTO_ARMED"
                self.event(f"Auto-record armed: mission items {start} to {stop}")
            elif action == "arm_segments":
                segs = check_segments(data.get("segments"))
                if self.state == "AUTO_ARMED" and self.plan and self.plan.get("segments") == segs:
                    return self.snapshot()
                if self.state != "READY":
                    raise ValueError({"IDLE": "No setup loaded on the payload.",
                                      "RECORDING": "Already recording; stop first.",
                                      "FAULT": "Payload fault: " + str(self.fault)}.get(self.state, "Payload is not ready."))
                index = 0
                cur = self.cube["current"] if self.cube["mode"] == "AUTO" else None
                if cur is not None:
                    while index < len(segs) and segs[index][1] < cur:
                        index += 1
                    if index == len(segs):
                        raise ValueError(f"Aircraft is already past mission item {segs[-1][1]}. Use Record now.")
                self.plan = dict(segments=segs, index=index, start_seq=segs[index][0], stop_seq=segs[index][1])
                self.state = "AUTO_ARMED"
                self.event("Auto-record armed per phase: items " + ", ".join(f"{a}-{b}" for a, b in segs))
            elif action == "reset":
                if self.state in ("RECORDING", "FINALIZING"):
                    raise ValueError("Stop recording first.")
                self.backend.close()
                self.state, self.fault, self.plan = "IDLE", None, None
                self.fallback_suppressed = True
            elif action == "status":
                pass
            else:
                raise ValueError("Unknown payload action.")
            return self.snapshot()

    def _start(self, origin):
        if self.state == "RECORDING":
            return
        if self.state not in ("READY", "AUTO_ARMED"):
            raise ValueError({"IDLE": "No setup loaded on the payload.",
                              "FAULT": "Payload fault: " + str(self.fault)}.get(self.state, "Payload is busy (" + self.state + ")."))
        health = self.backend.status()
        if health["disk_free_gb"] <= self.config["storage"]["reserve_gb"]:
            raise ValueError("Disk reserve reached. Free space before recording.")
        self.session = datetime.now(timezone.utc).strftime("HERON_%Y%m%d_%H%M%S_%f")
        try:
            self.backend.start(self.session, self.plan)
        except Exception as exc:
            self.backend.stop()
            self.state, self.fault = "FAULT", str(exc)
            self.event(self.fault)
            raise
        self.started, self.origin = self.clock(), origin
        self.state = "RECORDING"
        self.health_logged_at = None
        where = f" at item {self.plan['start_seq']}" if origin == "mission" and self.plan else ""
        if origin == "mission" and self.plan and "segments" in self.plan:
            where += f", phase {self.plan['index'] + 1} of {len(self.plan['segments'])}"
        self.event(f"REC START{where} ({origin})")

    def _stop(self, reason, suppress=False):
        if self.state == "RECORDING":
            self.state = "FINALIZING"
            try:
                self.backend.stop()
                self.state = "READY"
            except Exception as exc:
                self.state, self.fault = "FAULT", str(exc)
                self.event(self.fault)
        elif self.state == "AUTO_ARMED":
            self.state = "READY"
        self.plan = None
        self.fallback_suppressed = self.fallback_suppressed or suppress
        self.event(reason)

    def cube_update(self, armed=None, mode=None, current=None, reached=None, changed=False):
        """Feed one observation from the Cube (HEARTBEAT, MISSION_CURRENT, MISSION_ITEM_REACHED)."""
        with self.lock:
            was_armed = self.cube["armed"]
            self.cube_seen = self.clock()
            self.cube["connected"] = True
            if armed is not None:
                self.cube["armed"] = bool(armed)
            if mode is not None:
                self.cube["mode"] = str(mode)
            if current is not None:
                self.cube["current"] = int(current)
            b = self.config["behavior"] if self.config else {}
            disarmed = was_armed and not self.cube["armed"]
            if self.plan and changed:
                self._stop("Mission changed; auto-record cancelled", suppress=True)
                return
            if self.state == "RECORDING":
                if disarmed and b.get("stop_on_disarm"):
                    self._stop("REC STOP: disarmed", suppress=True)
                    return
                if self.origin == "mission" and mode in ("RTL", "LAND", "SMART_RTL", "AUTO_RTL"):
                    self._stop("REC STOP: " + mode, suppress=True)
                    return
                if self.origin == "mission" and mode is not None and mode != "AUTO" and b.get("stop_on_exit_auto"):
                    self._stop("REC STOP: left AUTO (" + mode + ")", suppress=True)
                    return
            if self.state == "AUTO_ARMED" and (disarmed or mode in ("RTL", "LAND", "SMART_RTL", "AUTO_RTL")):
                self._stop("Auto-record cancelled: " + (mode if mode in ("RTL", "LAND", "SMART_RTL", "AUTO_RTL") else "disarmed"),
                           suppress=True)
                return
            if not self.plan:
                return
            start, stop = self.plan["start_seq"], self.plan["stop_seq"]
            seq = self.cube["current"]
            if "segments" in self.plan:
                self._follow_segments(reached, current, seq)
                return
            if self.state == "RECORDING" and (reached == stop or (current is not None and current > stop)):
                self._stop(f"REC STOP at item {stop} (survey done)", suppress=True)
                return
            if self.state != "AUTO_ARMED" or not (self.cube["armed"] and self.cube["mode"] == "AUTO"):
                return
            if reached == start:
                self._start("mission")
            elif reached is None and seq is not None and start < seq <= stop:
                # Arrival message missed (or operator jumped ahead inside the survey): record late rather than never.
                self._start("mission")
                self.event(f"Late start: arrival at item {start} not seen, now at item {seq}")
            elif seq is not None and seq > stop:
                self._stop(f"Auto-record cancelled: already past item {stop}", suppress=True)

    def _follow_segments(self, reached, current, seq):
        """Per-phase auto-record: record each [start, stop] segment, re-arm for the next one in between."""
        plan = self.plan
        segs = plan["segments"]
        start, stop = plan["start_seq"], plan["stop_seq"]
        n, i = len(segs), plan["index"]
        if self.state == "RECORDING":
            # A segment ends when its last item is finished (current moves past it), keeping a hover's final hold.
            if current is not None and current > stop:
                self._stop(f"REC STOP after item {stop} (phase {i + 1} of {n} done)", suppress=True)
                if self.state == "READY" and i + 1 < n:
                    self._arm_segment(plan, i + 1)
            return
        if self.state != "AUTO_ARMED" or not (self.cube["armed"] and self.cube["mode"] == "AUTO"):
            return
        if seq is not None and seq > stop:
            j = i
            while j < n and segs[j][1] < seq:
                j += 1
            if j == n:
                self._stop(f"Auto-record cancelled: already past item {stop}", suppress=True)
                return
            self.event(f"Skipped phase {i + 1} of {n}: aircraft already at item {seq}")
            self._arm_segment(plan, j)
            start, stop, i = plan["start_seq"], plan["stop_seq"], j
        if reached == start:
            self._start("mission")
        elif reached is None and seq is not None and start < seq <= stop:
            self._start("mission")
            self.event(f"Late start: arrival at item {start} not seen, now at item {seq}")

    def _arm_segment(self, plan, index):
        plan["index"] = index
        plan["start_seq"], plan["stop_seq"] = plan["segments"][index]
        self.plan = plan
        self.state = "AUTO_ARMED"
        self.event(f"Waiting for items {plan["start_seq"]}-{plan["stop_seq"]} (phase {index + 1} of {len(plan['segments'])})")

    def _health(self, now):
        """Read the temperatures, warn once per heat-up, and log them while recording."""
        if self.temps_at is None or now - self.temps_at >= TEMP_EVERY_S:
            self.temps_at = now
            try:
                self.temps = self.read_temps()
            except Exception:
                self.temps = {"cpu_c": None, "ssd_c": None}
            for key, name, warn in (("cpu_c", "CPU", health.CPU_WARN_C), ("ssd_c", "SSD", health.SSD_WARN_C)):
                c = self.temps.get(key)
                if c is None:
                    continue
                if c >= warn and key not in self.hot:
                    self.hot.add(key)
                    self.event(f"NUC {name} hot: {c:.0f} C")
                elif c < warn - 5 and key in self.hot:
                    self.hot.discard(key)
                    self.event(f"NUC {name} cooled to {c:.0f} C")
        if self.state == "RECORDING" and (self.health_logged_at is None or now - self.health_logged_at >= HEALTH_LOG_EVERY_S):
            self.health_logged_at = now
            try:
                folder = self.backend.session_path()
                if folder:
                    path = Path(folder) / "nuc_health.csv"
                    h = self.backend.status()
                    overflows = sum(int(r.get("overflows", 0)) + int(r.get("gaps", 0)) for r in h.get("radios", []))
                    new = not path.exists()
                    with path.open("a") as f:
                        if new:
                            f.write("utc,elapsed_s,cpu_c,ssd_c,rate_mbps,disk_free_gb,dropped,mission_item\n")
                        cell = lambda v: "" if v is None else v
                        f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')},"
                                f"{round(now - self.started, 1) if self.started else ''},"
                                f"{cell(self.temps.get('cpu_c'))},{cell(self.temps.get('ssd_c'))},"
                                f"{round(h.get('rate_mbps', 0), 2)},{h.get('disk_free_gb', '')},{overflows},"
                                f"{cell(self.cube.get('current'))}\n")
            except Exception:
                pass                                    # logging must never disturb the recording

    def tick(self):
        with self.lock:
            now = self.clock()
            self._health(now)
            if self.cube_seen is not None and now - self.cube_seen > 5 and self.cube["connected"]:
                self.cube["connected"] = False
                self.event("Cube link lost")
                if self.config and self.config["behavior"]["cube_loss"] == "stop":
                    self._stop("Recording stopped: Cube link lost", suppress=True)
            if self.state in ("READY", "AUTO_ARMED", "RECORDING"):
                h = self.backend.status()
                if h.get("fault") or h["disk_free_gb"] <= self.config["storage"]["reserve_gb"]:
                    fault = h.get("fault") or "Disk reserve reached"
                    self._stop(fault, suppress=True)
                    self.state, self.fault = "FAULT", fault
                    return
            if self.config and self.state == "READY" and not self.fallback_suppressed:
                b = self.config["behavior"]
                if b["fallback"] and self.prepared_at is not None and now - self.prepared_at >= b["grace_seconds"] and self.ground_seen is None:
                    self.fallback_suppressed = True
                    self._start("no-ground fallback")

    def snapshot(self):
        with self.lock:
            h = self.backend.status()
            health_fault = h.pop("fault", None)
            e = estimate(self.config) if self.config else dict(radios=0, channels=0, disk_mbps=0, usb_mbps=0, gb_per_min=0)
            reserve = self.config["storage"]["reserve_gb"] if self.config else 0
            return dict(state=self.state, simulated=self.backend.simulated, fault=self.fault or health_fault,
                        setup=self.config["name"] if self.config else None,
                        config_hash=digest(self.config) if self.config else None,
                        estimate=e, session=self.session, plan=copy.deepcopy(self.plan),
                        elapsed_s=round(self.clock()-self.started, 1) if self.state == "RECORDING" and self.started else 0,
                        remaining_minutes=round(max(0, h["disk_free_gb"]-reserve)/e["gb_per_min"], 1) if e["gb_per_min"] else None,
                        cube=copy.deepcopy(self.cube), events=list(self.events)[-15:],
                        temps=dict(self.temps), **h)


def check_segments(segments):
    """[[start, stop], ...] in mission order, 1..MAX_SEGMENTS of them, not overlapping."""
    from .protocol import MAX_SEGMENTS
    try:
        segs = [[s[0], s[1]] for s in segments]
    except (TypeError, IndexError, KeyError):
        raise ValueError("Auto-record segments must be [start, stop] pairs.")
    if not 1 <= len(segs) <= MAX_SEGMENTS:
        raise ValueError(f"Auto-record takes 1 to {MAX_SEGMENTS} phases.")
    prev = 0
    for a, b in segs:
        if type(a) is not int or type(b) is not int or not 1 <= a <= b <= 999 or a <= prev:
            raise ValueError("Each phase must start after the previous one ends, and before it stops.")
        prev = b
    return segs
