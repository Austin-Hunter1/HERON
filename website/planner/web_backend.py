"""Run Jack's mission planner (Specular_Planner/app.py) inside a browser.

herongnss.org/sim/ runs the REAL planner code with Pyodide, in a Web
Worker (decision D-120). This module boots it and adapts the parts that
a browser cannot run. It does not edit any planner file; it patches the
runtime before ``app`` is imported.

What a browser cannot do, and what this module does instead:

1. **Threads.** Pyodide cannot start a thread. The planner starts three
   kinds:
   - Poll loops of the form ``while not event.wait(t): body`` (the
     simulated payload, the payload monitor). ``tick()`` runs one pass of
     each loop body, every 100 ms, called from the worker.
   - The flight operation (``FlightService._run``: refresh, upload,
     launch). It runs at once, inside the request that asked for it.
     Its sleeps and waits advance a *virtual clock* and tick the poll
     loops, so the demo aircraft climbs and the payload arms as they
     would in real time. The wait for the planned takeoff time is
     skipped, as if the operator pressed "Fly now".
   - One-shot helpers: they run at once.
2. **Locks.** With one thread, a second ``acquire`` of a plain Lock would
   hang the page. ``threading.Lock`` becomes ``RLock``.
3. **Time.** ``time.time``, ``time.monotonic`` and ``datetime.now`` read
   the real clock plus the virtual offset, so all planner modules agree.

``handle()`` answers one HTTP request through Flask's test client, so
every route and every check in app.py is the real one.

The same module runs under CPython for the smoke test
(``website/planner/smoke_test.py``).
"""

from __future__ import annotations

import datetime as _dt
import sys
import threading
import time as _time
from pathlib import Path

# ----------------------------------------------------------------- clock
_real_time, _real_mono, _real_sleep = _time.time, _time.monotonic, _time.sleep
_offset = [0.0]  # seconds added to the real clock by virtual sleeps


def _vtime() -> float:
    return _real_time() + _offset[0]


def _vmono() -> float:
    return _real_mono() + _offset[0]


class _VDateTime(_dt.datetime):
    """datetime whose now()/utcnow() follow the virtual clock."""

    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(_vtime(), tz)

    @classmethod
    def utcnow(cls):
        return cls.fromtimestamp(_vtime(), _dt.UTC).replace(tzinfo=None)


# ------------------------------------------------------- virtual waiting
_virtual = [0]  # > 0 while a synchronous "thread" runs
_pollers: list = []  # bound _loop methods to tick
_in_tick = [False]


class _TickDone(Exception):
    """Raised by Event.wait to end one pass of a poll loop."""


def _advance(seconds: float) -> None:
    """Move the virtual clock forward and let the poll loops run."""
    _offset[0] += max(0.0, float(seconds or 0.0))
    tick()


def _sleep(seconds: float) -> None:
    if _virtual[0]:
        _advance(seconds)
    # Outside a synchronous run nobody should sleep; do not block the worker.


_RealEvent = threading.Event


class _Event(_RealEvent):
    """Event.wait that never blocks the single browser thread.

    - In a poll-loop tick: the first wait returns False (run the body
      once), the next raises _TickDone (end of this tick).
    - In a synchronous operation: advance the virtual clock by the timeout.
    - Otherwise: return the flag at once.
    """

    def wait(self, timeout=None):
        if self.is_set():
            return True
        if _in_tick[0]:
            state = _tick_state.get(id(self), 0)
            _tick_state[id(self)] = state + 1
            if state == 0:
                return False
            raise _TickDone
        if _virtual[0]:
            _advance(0.1 if timeout is None else min(float(timeout), 1.0))
        return self.is_set()


_tick_state: dict[int, int] = {}


class _Condition(threading.Condition):
    """Condition.wait that advances the virtual clock instead of blocking."""

    def wait(self, timeout=None):
        if _virtual[0]:
            _advance(0.1 if timeout is None else min(float(timeout), 1.0))
        return False


class _Thread(threading.Thread):
    """Thread.start runs the target in a way the browser allows (see module doc)."""

    def start(self):
        target = self._target
        name = getattr(target, "__name__", "")
        if name == "_loop":
            _pollers.append(target)
            return
        # The flight operation skips the planned-takeoff wait ("Fly now").
        owner = getattr(target, "__self__", None)
        if name == "_run" and hasattr(owner, "release_wait"):
            owner.release_wait.set()
        _virtual[0] += 1
        try:
            target(*self._args, **self._kwargs)
        finally:
            _virtual[0] -= 1


def tick() -> None:
    """Run one pass of every poll loop. Called every 100 ms by the worker."""
    if _in_tick[0]:
        return
    _in_tick[0] = True
    try:
        for loop in list(_pollers):
            _tick_state.clear()
            try:
                loop()
            except _TickDone:
                pass
            except Exception as exc:  # A loop error must not stop the others.
                print("poll loop error:", exc, file=sys.stderr)
    finally:
        _in_tick[0] = False


def install_patches() -> None:
    """Patch time, datetime and threading. Call before importing app."""
    _time.time, _time.monotonic, _time.sleep = _vtime, _vmono, _sleep
    _dt.datetime = _VDateTime
    threading.Event = _Event
    threading.Condition = _Condition
    threading.Lock = threading.RLock
    threading.Thread = _Thread


# ------------------------------------------------------------- the app
_client = None


def boot(root: str, before_import=None) -> str:
    """Import the planner from ``root`` and return its source description.

    ``root`` holds Specular_Planner/, HERON_Payload/ and deps/ (pure
    Python packages: flask, sgp4, ...).
    """
    global _client
    base = Path(root)
    for sub in ("deps", "HERON_Payload", "Specular_Planner"):
        sys.path.insert(0, str(base / sub))
    install_patches()
    if before_import is not None:  # test hook (see smoke_test.py)
        before_import()
    import app as planner  # noqa: E402  (after the patches, on purpose)

    _client = planner.app.test_client()
    src = base / "SOURCE.txt"
    return src.read_text(encoding="utf-8").strip() if src.exists() else "unknown"


def handle(method: str, url: str, headers: dict, body: bytes | None):
    """Answer one request. Return (status, headers, body bytes).

    From the worker, ``headers`` and ``body`` arrive as JavaScript
    objects; convert them to Python first.
    """
    if hasattr(headers, "to_py"):
        headers = headers.to_py()
    if hasattr(body, "to_bytes"):
        body = body.to_bytes()
    resp = _client.open(
        url,
        method=method,
        headers=dict(headers or {}),
        data=bytes(body) if body else None,
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    out = (resp.status_code, dict(resp.headers), resp.get_data())
    resp.close()
    return out
