"""GNSS receiver reader: raw log, fix status, RTCM forwarding.

A thread reads the serial port. Every byte goes to a raw log file
(``<log_dir>/gnss_<stamp>.ubx``) for post-processing backup (B4).
NMEA GGA sentences update the fix status for the display. RTCM3
frames go to the correction sink.

``process_bytes`` is separate from the thread so a test can feed
bytes without a serial port.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import IO

from heron_common.timeutil import utc_stamp

from heron_base.config import GnssConfig
from heron_base.gnss.correction import CorrectionSink, NullSink
from heron_base.gnss.nmea import GgaFix, NmeaExtractor, parse_gga
from heron_base.gnss.rtcm import Rtcm3Splitter, rtcm_message_type

log = logging.getLogger(__name__)

OPEN_RETRY_S = 5.0


@dataclass
class GnssStatus:
    port_open: bool = False
    fix: GgaFix | None = None
    last_fix_time: float | None = None
    bytes_logged: int = 0
    rtcm_frames: int = 0
    rtcm_types: dict[int, int] = field(default_factory=dict)
    error: str | None = None
    log_path: str | None = None

    def fix_text(self, now: float, stale_s: float) -> str:
        if self.fix is None or self.last_fix_time is None:
            return "no GGA yet"
        if now - self.last_fix_time > stale_s:
            return f"STALE ({now - self.last_fix_time:.0f} s)"
        f = self.fix
        pos = ""
        if f.latitude is not None and f.longitude is not None:
            pos = f" {f.latitude:.6f},{f.longitude:.6f}"
        hdop = f" hdop {f.hdop:.1f}" if f.hdop is not None else ""
        return f"{f.quality_text} sats {f.satellites}{hdop}{pos}"


class GnssReceiver:
    """Read the receiver on a thread and keep a status snapshot."""

    def __init__(self, config: GnssConfig, sink: CorrectionSink | None = None) -> None:
        self._cfg = config
        self._sink = sink or NullSink()
        self._nmea = NmeaExtractor()
        self._rtcm = Rtcm3Splitter()
        self._lock = threading.Lock()
        self._status = GnssStatus()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._raw: IO[bytes] | None = None

    # ----- data path (testable) --------------------------------------------------

    def process_bytes(self, data: bytes, now: float | None = None) -> None:
        """Log raw bytes, parse GGA, split RTCM, forward RTCM."""
        now = time.time() if now is None else now
        if self._raw is not None:
            self._raw.write(data)
        with self._lock:
            self._status.bytes_logged += len(data)
        for sentence in self._nmea.feed(data):
            fix = parse_gga(sentence)
            if fix is not None:
                with self._lock:
                    self._status.fix = fix
                    self._status.last_fix_time = now
        for frame in self._rtcm.feed(data):
            self._sink.write(frame)
            mtype = rtcm_message_type(frame)
            with self._lock:
                self._status.rtcm_frames += 1
                if mtype is not None:
                    self._status.rtcm_types[mtype] = self._status.rtcm_types.get(mtype, 0) + 1

    def status(self) -> GnssStatus:
        with self._lock:
            return GnssStatus(
                **{**self._status.__dict__, "rtcm_types": dict(self._status.rtcm_types)}
            )

    # ----- thread ------------------------------------------------------------------

    def start(self) -> None:
        if not self._cfg.enabled:
            return
        self._cfg.log_dir.mkdir(parents=True, exist_ok=True)
        path = self._cfg.log_dir / f"gnss_{utc_stamp()}.ubx"
        self._raw = path.open("ab")
        with self._lock:
            self._status.log_path = str(path)
        self._thread = threading.Thread(target=self._run, name="gnss", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._raw is not None:
            self._raw.close()
            self._raw = None
        self._sink.close()

    def _run(self) -> None:
        port = None
        last_try = 0.0
        while not self._stop.is_set():
            if port is None:
                if time.time() - last_try < OPEN_RETRY_S:
                    time.sleep(0.2)
                    continue
                last_try = time.time()
                try:
                    import serial

                    port = serial.Serial(self._cfg.port, baudrate=self._cfg.baud, timeout=0.2)
                    with self._lock:
                        self._status.port_open = True
                        self._status.error = None
                    log.info("GNSS receiver open: %s @ %d", self._cfg.port, self._cfg.baud)
                except Exception as exc:
                    with self._lock:
                        self._status.port_open = False
                        self._status.error = f"cannot open {self._cfg.port}: {exc}"
                    port = None
                    continue
            try:
                data = port.read(4096)
            except Exception as exc:
                log.warning("GNSS read failed: %s", exc)
                with self._lock:
                    self._status.port_open = False
                    self._status.error = str(exc)
                try:
                    port.close()
                finally:
                    port = None
                continue
            if data:
                self.process_bytes(data)
        if port is not None:
            port.close()
