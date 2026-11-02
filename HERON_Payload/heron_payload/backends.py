"""Simulated and subprocess UHD recorders, behind the same controller interface."""
import copy
import json
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import storage
from .config import atomic_json, estimate, sync_mode


class FakeBackend:
    """No hardware. Behaves like the UHD backend; optionally writes small noise files so the
    Setup app's signal check has something real to analyse."""
    simulated = True

    def __init__(self, write_files=False, devices=None, storage_root=None, ssd_dir=None):
        self.storage_root_override = Path(storage_root) if storage_root else None
        self.ssd_dir = ssd_dir
        self.config = None
        self.recording = False
        self.last = time.monotonic()
        self.bytes = 0
        self.free = 900.0
        self.fault = None
        self.overflows = 0
        self.write_files = write_files
        self.session_dir = None
        self.devices = devices if devices is not None else [dict(serial="SIM-31C03F6", model="b210", name="B210 (simulated)")]

    def discover(self):
        return copy.deepcopy(self.devices)

    def prepare(self, config):
        self.config, self.recording, self.fault = copy.deepcopy(config), False, None
        self.bytes, self.overflows, self.last = 0, 0, time.monotonic()
        if self.write_files:
            self.storage_root(config).mkdir(parents=True, exist_ok=True)

    def start(self, session, plan=None):
        self.recording, self.last, self.bytes = True, time.monotonic(), 0
        self.session_dir = None
        if self.write_files and self.config:
            self.session_dir = self.storage_root(self.config) / session
            self._write_noise(self.session_dir, plan)

    def _write_noise(self, root, plan):
        import random
        root.mkdir(parents=True, exist_ok=True)
        atomic_json(root / "session.json", dict(config=self.config, mission=plan, simulated=True))
        peak = self.config.get("sc8_peak", 1.0)
        for r in self.config["radios"]:
            if not r["enabled"]:
                continue
            folder = root / r["id"]
            folder.mkdir()
            enabled = [c for c in r["channels"] if c["enabled"]]
            atomic_json(folder / "settings.json", dict(r, actual_channels=[dict(physical_channel=c["index"], label=c["label"],
                                                                                   gain_db=c["gain_db"]) for c in enabled]))
            for n, ch in enumerate(enabled):
                sigma = 2.2 * 10 ** ((ch["gain_db"] - 30) / 20) * (0.25 / peak)
                rnd = random.Random(hash((r["id"], ch["index"], ch["gain_db"])))
                lim = 127 if self.config["file_format"] == "sc8" else 32767
                scale = 1 if self.config["file_format"] == "sc8" else 256
                values = [max(-lim - 1, min(lim, int(round(rnd.gauss(0, sigma) * scale)))) for _ in range(200000)]
                if self.config["file_format"] == "sc8":
                    data = bytes(v & 0xFF for v in values)
                else:
                    import struct
                    data = struct.pack("<%dh" % len(values), *values)
                (folder / f"channel_{n}_000000.{self.config['file_format']}").write_bytes(data)

    def stop(self):
        self.status()
        self.recording = False
        if self.session_dir:
            atomic_json(self.session_dir / "completion.json", dict(complete=True, errors=[], simulated=True))

    def close(self):
        self.stop()
        self.config = None

    def status(self):
        now = time.monotonic()
        dt, self.last = now-self.last, now
        e = estimate(self.config) if self.config else {"disk_mbps": 0}
        if self.recording:
            size = dt * e["disk_mbps"] * 1e6
            self.bytes += size
            self.free = max(0, self.free-size/1e9)
        radios = []
        for r in (self.config or {}).get("radios", []):
            if not r["enabled"]:
                continue
            rate = r["sample_rate_msps"] * sum(c["enabled"] for c in r["channels"]) * (2 if self.config["file_format"] == "sc8" else 4)
            radios.append(dict(id=r["id"], name=r["name"], serial=r["serial"] or "SIMULATED", model=r["model"],
                state="RECORDING" if self.recording else "READY", rate_mbps=rate if self.recording else 0,
                bytes_written=int(self.bytes*rate/e["disk_mbps"]) if e["disk_mbps"] else 0,
                overflows=self.overflows, gaps=0, clipped=0,
                reference="simulated: " + sync_mode(self.config),
                labels=[c["label"] for c in r["channels"] if c["enabled"]]))
        return dict(radios=radios, disk_free_gb=round(self.free, 2), bytes_written=int(self.bytes),
                    rate_mbps=e["disk_mbps"] if self.recording else 0, backend_fault=self.fault,
                    storage=self.where(self.config) if self.config else None,
                    **({"fault": self.fault} if self.fault else {}))

    def session_path(self):
        return self.session_dir

    def where(self, config):
        if self.storage_root_override:
            return dict(path=str(self.storage_root_override), drive=storage.drive_label(self.storage_root_override), fallback=False,
                        configured=config["storage"]["path"], note=None)
        return storage.resolve(config, self.ssd_dir)

    def storage_root(self, config):
        return Path(self.where(config)["path"])


class Worker:
    def __init__(self, binary, config_path):
        self.process = subprocess.Popen([str(binary), str(config_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, bufsize=1)
        self.events, self.latest, self.errors = queue.Queue(), {"received_at": time.monotonic()}, []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._errors, daemon=True).start()

    def _errors(self):
        for line in self.process.stderr:
            self.errors = (self.errors + [line.strip()])[-12:]

    def _read(self):
        for line in self.process.stdout:
            try:
                item = json.loads(line)
                if item.get("event") == "status":
                    self.latest = dict(item, received_at=time.monotonic())
                else:
                    if item.get("event") == "stopped":
                        self.latest = dict(self.latest, **item, received_at=time.monotonic())
                    self.events.put(item)
            except ValueError:
                self.errors = (self.errors + [line.strip()])[-12:]

    def send(self, command):
        if self.process.poll() is not None:
            raise RuntimeError("Recorder exited: " + "; ".join(self.errors[-3:]))
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def wait(self, event, timeout=15):
        until = time.monotonic()+timeout
        while time.monotonic() < until:
            try:
                item = self.events.get(timeout=.1)
            except queue.Empty:
                if self.process.poll() is not None:
                    raise RuntimeError("Recorder exited: " + "; ".join(self.errors[-3:]))
                continue
            if item.get("event") == "error":
                raise RuntimeError(item.get("message", "Recorder error"))
            if item.get("event") == event:
                return item
        raise RuntimeError("Recorder timed out waiting for " + event)

    def close(self):
        if self.process.poll() is None:
            try:
                self.send("QUIT")
                self.process.wait(timeout=10)
            except Exception:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()


class UHDBackend:
    simulated = False

    def __init__(self, binary="/opt/heron/bin/heron-recorder", runtime="/var/lib/heron/runtime", ssd_dir=None):
        self.binary, self.runtime = Path(binary), Path(runtime)
        self.ssd_dir = ssd_dir
        self.where = None
        self.config, self.workers, self.session = None, {}, None
        self.recording = False

    def discover(self):
        result = subprocess.run([str(self.binary), "--discover"], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError(result.stderr[-1000:] or "UHD discovery failed")
        for line in reversed(result.stdout.splitlines()):
            try:
                return json.loads(line)["devices"]
            except (ValueError, KeyError):
                pass
        raise RuntimeError("Recorder did not return discovered radios.")

    def prepare(self, config):
        self.close()
        self.config = copy.deepcopy(config)
        # The data drive if it is mounted, otherwise ~/heron_recordings on the NUC's own SSD.
        self.where = storage.resolve(config, self.ssd_dir)
        root = Path(self.where["path"])
        root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(root).free/1e9 <= config["storage"]["reserve_gb"]:
            raise ValueError(f"The {self.where['drive']} has less than the {config['storage']['reserve_gb']:g} GB "
                             "reserve free. Free space or lower 'Keep free on disk'.")
        self.runtime.mkdir(parents=True, exist_ok=True)
        try:
            for r in config["radios"]:
                if not r["enabled"]:
                    continue
                settings = dict(r, clock_source=config["clock_source"], time_source=config["time_source"],
                                file_format=config["file_format"],
                                sc8_peak=config.get("sc8_peak", 1.0), int_n=True,
                                write_mode=config.get("write_mode", "direct"), spb=config.get("spb", 10000),
                                file_buffer_bytes=config.get("file_buffer_bytes", 0),
                                device_args=config.get("device_args", ""),
                                segment_seconds=config["storage"]["segment_seconds"])
                p = self.runtime/(r["id"]+".json")
                atomic_json(p, settings)
                self.workers[r["id"]] = Worker(self.binary, p)
            for w in self.workers.values():
                w.wait("configured", 30)
            if config["time_source"] == "external":
                # PPS in use: observe a recent common PPS edge before issuing set_time_next_pps on every radio.
                first = next(iter(self.workers.values()))
                until = time.monotonic()+5
                while True:
                    first.send("PHASE")
                    phase = first.wait("phase", 2)["age"]
                    if .05 <= phase <= .20:
                        break
                    if time.monotonic() > until:
                        raise RuntimeError("No usable PPS edge. Check clock-board connections.")
                    time.sleep(.025)
                for w in self.workers.values():
                    w.send("SYNC")
                for w in self.workers.values():
                    w.wait("synced", 5)
            for w in self.workers.values():
                w.send("STREAM")
            for w in self.workers.values():
                w.wait("ready", 5)
        except Exception:
            self.close()
            raise

    def start(self, session, plan=None):
        root = Path(self.where["path"])/session
        root.mkdir(exist_ok=False)
        self.session = root
        atomic_json(root/"session.json", dict(config=self.config, mission=plan, state="starting", time_basis="shared PPS epoch; not certified UTC"))
        times = []
        for w in self.workers.values():
            w.send("TIME")
        for w in self.workers.values():
            times.append(w.wait("time", 3)["time"])
        if self.config["time_source"] == "external" and max(times)-min(times) > .1:
            raise RuntimeError("Device clocks disagree; prepare synchronization again.")
        # Every worker receives the same timestamp after shared-PPS preparation.
        target = max(times)+.5
        for ident, w in self.workers.items():
            folder = root/ident
            folder.mkdir()
            w.send("START " + json.dumps(dict(path=str(folder), time=target if self.config["time_source"] == "external" else -1)))
        for w in self.workers.values():
            w.wait("recording", 8)
        self.recording = True
        atomic_json(root/"session.json", dict(config=self.config, mission=plan, state="recording", start_device_time=target,
                                              time_basis="shared PPS epoch; not certified UTC"))

    def stop(self):
        errors = []
        for w in self.workers.values():
            try:
                w.send("STOP")
            except Exception as e:
                errors.append(str(e))
        for w in self.workers.values():
            try:
                w.wait("stopped", 15)
            except Exception as e:
                errors.append(str(e))
        self.recording = False
        if self.session:
            atomic_json(self.session/"completion.json", dict(complete=not errors, errors=errors, status=self.status()))
        if errors:
            raise RuntimeError("; ".join(errors))

    def close(self):
        for w in self.workers.values():
            w.close()
        self.workers = {}
        self.recording = False

    def session_path(self):
        return self.session

    def storage_root(self, config):
        if self.config is not None and self.where and config == self.config:
            return Path(self.where["path"])
        return Path(storage.resolve(config, self.ssd_dir)["path"])

    def status(self):
        root = Path(self.where["path"]) if self.config and self.where else self.runtime
        while not root.exists() and root != root.parent:
            root = root.parent
        rows, fault = [], None
        for ident, w in self.workers.items():
            r = next(r for r in self.config["radios"] if r["id"] == ident)
            status = dict(w.latest)
            age = time.monotonic()-status.pop("received_at", time.monotonic())
            if w.process.poll() is not None:
                fault = r["name"] + " recorder exited: " + "; ".join(w.errors[-2:])
            elif age > 5:
                fault = r["name"] + " recorder status expired"
            rows.append(dict(id=ident, name=r["name"], serial=r["serial"], model=r["model"],
                             labels=[c["label"] for c in r["channels"] if c["enabled"]], **status))
        result = dict(radios=rows, disk_free_gb=round(shutil.disk_usage(root).free/1e9, 2),
                      storage=dict(self.where) if self.config and self.where else None,
                      bytes_written=sum(r.get("bytes_written", 0) for r in rows),
                      rate_mbps=sum(r.get("rate_mbps", 0) for r in rows))
        if fault:
            result["fault"] = fault
        return result
