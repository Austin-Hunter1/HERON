/* heron_sim.js - The HERON payload and ground link, simulated in the browser.
 *
 * Why this file exists: herongnss.org must show the real ground station
 * page with no server and no hardware (decision D-103). This file is a
 * JavaScript port of the Python parts behind that page:
 *
 *   RecordingController  <- heron_onboard/state_machine.py
 *   FakeBackend          <- heron_onboard/capture/backend.py (FakeCaptureBackend)
 *   Supervisor           <- heron_onboard/supervisor.py
 *   GroundClient         <- heron_base/link_client.py + link_health.py
 *   Dashboard            <- heron_base/web/server.py (build_snapshot, queue_command)
 *
 * Keep the rules and the message texts the same as the Python code. When
 * the Python code changes, change this file in the same commit. The
 * shape check in sim_shim.js and Base_Software/tests/test_web_sim_data.py
 * catch a changed snapshot layout; they cannot catch a changed rule.
 *
 * This file has no DOM code. sim_shim.js joins it to the page.
 * All time is "sim seconds" (UNIX-like), passed in as `now`.
 */
(function (global) {
  "use strict";

  /* ------------------------------------------------------------------ helpers */
  /** Python-like dict text, for event lines: {'flight_id': 'x'}. */
  const pyDict = (o) => "{" + Object.entries(o).map(([k, v]) => `'${k}': '${v}'`).join(", ") + "}";
  /** "%H:%M:%S" in UTC, as time.strftime(..., time.gmtime(t)). */
  const hms = (t) => new Date(t * 1000).toISOString().slice(11, 19);
  /** UTC stamp for a flight id, like heron_common.timeutil.utc_stamp. */
  const utcStamp = (t) => new Date(t * 1000).toISOString().replace(/[-:]/g, "").slice(0, 15) + "Z";

  const State = { IDLE: "IDLE", RECORDING: "RECORDING", FAULT: "FAULT" };
  const LinkState = { NEVER: "NEVER", UP: "UP", LOST: "LOST" };
  const Health = { NO_DATA: "NO DATA", CONNECTED: "CONNECTED", DEGRADED: "DEGRADED", LOST: "LOST" };
  const COMMANDS = ["start", "stop", "status", "ping"];

  /** compute_link_health from link_health.py. */
  function linkHealth(age, degradedS, lostS) {
    if (age === null) return Health.NO_DATA;
    if (age >= lostS) return Health.LOST;
    if (age >= degradedS) return Health.DEGRADED;
    return Health.CONNECTED;
  }

  /** make_flight_id from state_machine.py. */
  function makeFlightId(now, prefix, requested) {
    let base = requested ? String(requested).trim() : "";
    base = base.replace(/[^A-Za-z0-9_\-]+/g, "_").replace(/^_+|_+$/g, "");
    if (!base) base = utcStamp(now);
    if (prefix) base = `${prefix}_${base}`;
    return base.slice(0, 64);
  }

  /* ------------------------------------------------------- RecordingController */
  class RecordingController {
    constructor(cfg, now) {
      this.cfg = cfg;
      this.state = State.IDLE;
      this.linkState = LinkState.NEVER;
      this.bootTime = now;
      this.lastLinkSeen = null;
      this.linkLostAt = null;
      this.fallbackArmed = cfg.fallback_mode !== "none";
      this.diskLow = false;
      this.fault = null;
      this.flightId = null;
      this.recordingSince = null;
      this.lastCmdSeq = null;
      this._lastCmd = null;
      this._lastResult = null;
    }
    onFrameReceived(now) {
      this.lastLinkSeen = now;
      const actions = [];
      if (this.linkState !== LinkState.UP) {
        actions.push({ kind: "ALERT", reason: `link ${this.linkState} -> UP` });
        this.linkState = LinkState.UP;
        this.linkLostAt = null;
        if (this.cfg.rearm_fallback_on_link_loss && this.cfg.fallback_mode !== "none") this.fallbackArmed = true;
      }
      return actions;
    }
    handleCommand(cmd, now) {
      const last = this._lastCmd;
      if (this._lastResult && last && cmd.seq === last.seq && cmd.name === last.name && JSON.stringify(cmd.args) === JSON.stringify(last.args)) {
        return { ok: this._lastResult.ok, message: this._lastResult.message, actions: [] };
      }
      this.lastCmdSeq = cmd.seq;
      this._lastCmd = cmd;
      let r;
      if (cmd.name === "start") r = this._start(now, cmd.args.flight_id, "ground START");
      else if (cmd.name === "stop") r = this._stop(now, "ground STOP");
      else r = { ok: true, message: `state ${this.state}`, actions: [] };
      this._lastResult = r;
      return r;
    }
    _start(now, requested, reason) {
      if (this.state === State.RECORDING) return { ok: true, message: `already recording ${this.flightId}`, actions: [] };
      if (this.state === State.FAULT) return { ok: false, message: `in FAULT (${this.fault}); send STOP to clear`, actions: [] };
      if (this.diskLow) return { ok: false, message: "disk free space below threshold", actions: [] };
      this.flightId = makeFlightId(now, this.cfg.flight_id_prefix, requested);
      this.state = State.RECORDING;
      this.recordingSince = now;
      this.fallbackArmed = false;
      return { ok: true, message: `starting ${this.flightId}`, actions: [{ kind: "START_CAPTURE", flightId: this.flightId, reason }] };
    }
    _stop(now, reason) {
      if (this.state === State.IDLE) return { ok: true, message: "already idle", actions: [] };
      const wasFault = this.state === State.FAULT;
      this.state = State.IDLE;
      this.fault = null;
      this.recordingSince = null;
      this.fallbackArmed = false;
      return { ok: true, message: wasFault ? "fault cleared" : `stopping ${this.flightId}`, actions: [{ kind: "STOP_CAPTURE", reason }] };
    }
    onDiskLow(now, freeGb) {
      const actions = [];
      if (!this.diskLow) actions.push({ kind: "ALERT", reason: `disk low: ${freeGb.toFixed(1)} GB free` });
      this.diskLow = true;
      if (this.state === State.RECORDING) actions.push(...this._stop(now, "disk low").actions);
      return actions;
    }
    onDiskOk() { this.diskLow = false; }
    onCaptureLost(now, reason) {
      if (this.state !== State.RECORDING) return [];
      this.state = State.FAULT;
      this.fault = reason;
      this.recordingSince = null;
      return [{ kind: "STOP_CAPTURE", reason }, { kind: "ALERT", reason: `capture lost: ${reason}` }];
    }
    tick(now) {
      const actions = [];
      if (this.linkState === LinkState.UP && this.lastLinkSeen !== null && now - this.lastLinkSeen > this.cfg.link_lost_s) {
        this.linkState = LinkState.LOST;
        this.linkLostAt = now;
        actions.push({ kind: "ALERT", reason: "link LOST (recording continues)" });
      }
      if (this.fallbackArmed && this.state === State.IDLE && !this.diskLow) {
        const mode = this.cfg.fallback_mode, grace = this.cfg.link_grace_s;
        if (mode === "record_at_boot") actions.push(...this._start(now, null, "fallback: record at boot").actions);
        else if (mode === "record_if_no_link") {
          if (this.linkState === LinkState.NEVER && now - this.bootTime >= grace) actions.push(...this._start(now, null, "fallback: no link after boot").actions);
          else if (this.linkState === LinkState.LOST && this.cfg.rearm_fallback_on_link_loss && this.linkLostAt !== null && now - this.linkLostAt >= grace) {
            actions.push(...this._start(now, null, "fallback: link lost").actions);
          }
        }
      }
      return actions;
    }
    recordingSeconds(now) { return this.recordingSince === null ? 0 : Math.max(0, now - this.recordingSince); }
  }

  /* ------------------------------------------------------------- FakeBackend */
  /** Same behaviour as FakeCaptureBackend, plus two teaching faults:
   *  loss of the 10 MHz reference lock, and USB overflows. */
  class FakeBackend {
    constructor(sdrs, segmentS) {
      this.sdrs = sdrs; this.segmentS = segmentS;
      this.running = {}; this.faults = {}; this.unlocked = {}; this.overflows = {};
    }
    start(now) { this.running = {}; this.sdrs.forEach((s) => (this.running[s.id] = now)); this.faults = {}; this.overflows = {}; }
    stop() { this.running = {}; }
    poll(now) {
      return this.sdrs.map((s) => {
        const fault = this.faults[s.id] ?? null, since = this.running[s.id];
        const base = { id: s.id, present: true, streaming: false, ref_locked: null, overflows: 0, host_drops: 0, samples: 0, rate_mbps: 0.0, segment: 0, fault };
        if (since === undefined) return base;
        const el = Math.max(0, now - since);
        return Object.assign(base, {
          streaming: true, ref_locked: !this.unlocked[s.id], overflows: this.overflows[s.id] || 0,
          samples: Math.floor(el * s.sample_rate_hz), rate_mbps: s.bytes_per_second / 1e6, segment: Math.floor(el / this.segmentS),
        });
      });
    }
    runningCount() { return Object.keys(this.running).length; }
    fail(id, reason = "fake fault") { delete this.running[id]; this.faults[id] = reason; }
    killAll(reason = "fake: all recorders died") { this.sdrs.forEach((s) => this.fail(s.id, reason)); }
  }

  /* -------------------------------------------------------------------- Link */
  /** A two-way radio link: latency, random loss, corruption and a cut.
   *  Real frames have a CRC, so a corrupt frame counts as a bad frame at
   *  the receiver and is dropped (heron_common/protocol/framing.py). */
  class Link {
    constructor(sc) { this.latency = sc.link_latency_s; this.lossPct = sc.link_loss_pct; this.corruptPct = sc.corrupt_pct; this.cut = false; this.q = { up: [], down: [] }; this.rnd = Math.random; }
    send(dir, msg, now) {
      if (this.cut || this.rnd() * 100 < this.lossPct) return;
      this.q[dir].push({ at: now + this.latency, msg, bad: this.rnd() * 100 < this.corruptPct });
    }
    recv(dir, now) { const ready = this.q[dir].filter((m) => m.at <= now); this.q[dir] = this.q[dir].filter((m) => m.at > now); return ready; }
  }

  /* -------------------------------------------------------------- Supervisor */
  class Supervisor {
    constructor(cfg, sc, link, now) {
      this.cfg = cfg; this.sc = sc; this.link = link;
      this.ctrl = new RecordingController(cfg.control, now);
      this.backend = new FakeBackend(cfg.sdrs, cfg.segment_seconds);
      this.boot = now; this.tlmSeq = 0; this.lastTlm = -1e9; this.lastDisk = -1e9;
      this.good = 0; this.bad = 0;
      this.diskTotal = sc.disk_total_gb; this.diskFree = sc.disk_free_gb;
      this.disk = { free_gb: 0, free_pct: 0, below: false };
      this.statuses = this.backend.poll(now);
      this.log = []; // payload-side log lines (the real payload writes these to its journal)
      this._lastStep = now;
    }
    step(now) {
      const dt = Math.max(0, now - this._lastStep); this._lastStep = now;
      // The data drive fills while the recorders write. (Not in the Python fake.)
      if (this.backend.runningCount()) this.diskFree = Math.max(0, this.diskFree - dt * this.statuses.reduce((a, s) => a + (s.streaming ? s.rate_mbps : 0), 0) / 1000);
      for (const f of this.link.recv("up", now)) {
        if (f.bad) { this.bad++; continue; }
        this.good++;
        this.run(this.ctrl.onFrameReceived(now), now);
        this.handleCommand(f.msg, now);
      }
      this.run(this.ctrl.tick(now), now);
      this.statuses = this.backend.poll(now);
      if (this.ctrl.state === State.RECORDING && this.backend.runningCount() === 0) {
        const reason = this.statuses.filter((s) => s.fault).map((s) => `${s.id}: ${s.fault}`).join("; ") || "all recorders exited";
        this.run(this.ctrl.onCaptureLost(now, reason), now);
      }
      if (now - this.lastDisk >= this.cfg.disk.check_interval_s) {
        this.lastDisk = now;
        const pct = this.diskTotal > 0 ? (100 * this.diskFree) / this.diskTotal : 0;
        const below = this.diskFree < this.cfg.disk.min_free_gb || (this.diskTotal > 0 && pct < this.cfg.disk.min_free_pct);
        this.disk = { free_gb: this.diskFree, free_pct: pct, below };
        if (below) this.run(this.ctrl.onDiskLow(now, this.diskFree), now); else this.ctrl.onDiskOk();
      }
      if (now - this.lastTlm >= 1 / this.cfg.telemetry.rate_hz) this.sendTelemetry(now);
    }
    handleCommand(cmd, now) {
      const r = this.ctrl.handleCommand(cmd, now);
      this.run(r.actions, now);
      this.link.send("down", { kind: "ack", v: 1, seq: cmd.seq, ok: r.ok, message: r.message, state: this.ctrl.state, sent_at: now }, now);
      if (cmd.name === "status") this.sendTelemetry(now);
    }
    run(actions, now) {
      for (const a of actions) {
        if (a.kind === "START_CAPTURE") this.backend.start(now);
        else if (a.kind === "STOP_CAPTURE") this.backend.stop();
        else if (a.kind === "ALERT") this.log.push(`${hms(now)}Z ALERT: ${a.reason}`);
      }
    }
    telemetry(now) {
      const c = this.ctrl, rec = c.state === State.RECORDING, sc = this.sc;
      const [lo, hi] = rec ? sc.cpu_recording_pct : sc.cpu_idle_pct;
      const temps = {};
      if (this.cfg.telemetry.include_temps) this.cfg.temp_labels.forEach((k) => { if (k in sc.temps_idle_c) temps[k] = sc.temps_idle_c[k] + (rec ? sc.temps_recording_rise_c : 0) + Math.random() * 1.5; });
      this.tlmSeq += 1;
      return {
        kind: "tlm", v: 1, seq: this.tlmSeq, sent_at: now, uptime_s: now - this.boot, state: c.state,
        flight_id: rec ? c.flightId : null, recording_s: c.recordingSeconds(now), sdrs: this.statuses,
        data_rate_mbps: this.statuses.reduce((a, s) => a + (s.streaming ? s.rate_mbps : 0), 0),
        disk_free_gb: Math.round(this.disk.free_gb * 100) / 100, disk_free_pct: Math.round(this.disk.free_pct * 10) / 10,
        disk_alert: this.disk.below, cpu_pct: lo + Math.random() * (hi - lo), temps_c: temps,
        last_cmd_seq: c.lastCmdSeq, fault: c.fault, version: sc.version, link_good_frames: this.good, link_bad_frames: this.bad,
      };
    }
    sendTelemetry(now) { this.lastTlm = now; this.link.send("down", this.telemetry(now), now); }
  }

  /* ------------------------------------------------------------ GroundClient */
  class GroundClient {
    constructor(cfg, link, now) {
      this.cfg = cfg; this.link = link;
      this.seq = Math.floor(now) & 0x3fffffff;
      this.latest = null; this.lastRx = null; this.pending = null;
      this.history = []; this.events = []; this.telemetryCount = 0; this.badFrames = 0;
      this.lastHealth = Health.NO_DATA; this.maxEvents = cfg.event_lines;
    }
    event(now, text) { this.events.push(`${hms(now)}Z ${text}`); if (this.events.length > this.maxEvents) this.events.shift(); }
    age(now) { return this.lastRx === null ? null : Math.max(0, now - this.lastRx); }
    health(now) { return linkHealth(this.age(now), this.cfg.link_health.degraded_s, this.cfg.link_health.lost_s); }
    summary(p) {
      const c = p.command;
      if (!p.done) return `${c.name} seq=${c.seq} waiting (try ${p.attempts})`;
      if (!p.ack) return `${c.name} seq=${c.seq} FAILED: no ack`;
      return `${c.name} seq=${c.seq} ${p.ack.ok ? "ok" : "REFUSED"}: ${p.ack.message}`;
    }
    poll(now) {
      for (const f of this.link.recv("down", now)) {
        if (f.bad) { this.badFrames++; continue; }
        this.lastRx = now;
        if (f.msg.kind === "tlm") { this.latest = f.msg; this.telemetryCount++; }
        else if (f.msg.kind === "ack") this.handleAck(f.msg, now);
      }
      this.retry(now);
      const h = this.health(now);
      if (h !== this.lastHealth) { this.event(now, `link ${this.lastHealth} -> ${h}`); this.lastHealth = h; }
    }
    pushHistory(p) { this.history.push(p); if (this.history.length > 50) this.history.shift(); }
    handleAck(ack, now) {
      const p = this.pending;
      if (!p || p.command.seq !== ack.seq) return;
      p.ack = ack; p.done = true; this.event(now, this.summary(p)); this.pushHistory(p); this.pending = null;
    }
    retry(now) {
      const p = this.pending, lh = this.cfg.link_health;
      if (!p || p.done || now - p.sentAt < lh.command_timeout_s) return;
      if (p.attempts <= lh.command_retries) {
        p.attempts++; p.sentAt = now; this.link.send("up", p.command, now);
        this.event(now, `resend ${p.command.name} seq=${p.command.seq} (try ${p.attempts})`);
        return;
      }
      p.done = true; this.event(now, this.summary(p)); this.pushHistory(p); this.pending = null;
    }
    send(name, args, now) {
      if (this.pending && !this.pending.done) {
        this.pending.done = true; this.event(now, `abandoned ${this.pending.command.name} seq=${this.pending.command.seq}`); this.pushHistory(this.pending);
      }
      this.seq += 1;
      const cmd = { kind: "cmd", v: 1, seq: this.seq, name, args: args || {}, sent_at: now };
      this.pending = { command: cmd, sentAt: now, attempts: 1, done: false, ack: null };
      this.link.send("up", cmd, now);
      const a = Object.keys(cmd.args).length ? " " + pyDict(cmd.args) : "";
      this.event(now, `sent ${name} seq=${cmd.seq}${a}`);
    }
  }

  /* --------------------------------------------------------------- Dashboard */
  class Dashboard {
    constructor(cfg, client, gnss) { this.cfg = cfg; this.client = client; this.gnss = gnss; this.queue = []; }
    /** queue_command from server.py: validate, keep the flight id short. */
    queueCommand(name, flightId) {
      const n = String(name);
      if (!COMMANDS.includes(n)) return [false, `unknown command '${n}'`];
      const args = {};
      if (n === "start" && flightId) args.flight_id = String(flightId).slice(0, 64);
      this.queue.push([n, args]);
      return [true, `${n} queued`];
    }
    step(now) { this.client.poll(now); while (this.queue.length) { const [n, a] = this.queue.shift(); this.client.send(n, a, now); } }
    snapshot(now) {
      const c = this.client;
      return {
        server_time: now, refresh_ms: this.cfg.refresh_ms,
        link: { health: c.health(now), age_s: c.age(now), telemetry_count: c.telemetryCount, bad_frames: c.badFrames, transport: "simulation(browser)" },
        telemetry: c.latest,
        pending: c.pending && !c.pending.done ? c.summary(c.pending) : null,
        history: c.history.slice(-10).map((p) => c.summary(p)),
        events: c.events.slice(),
        gnss: this.gnss.snapshot(now),
      };
    }
  }

  /* -------------------------------------------------------- simulated GNSS */
  /** A ZED-F9P that always has a fix. It gives the GNSS card numbers that
   *  grow at a plausible rate; it does not model RTK behaviour. */
  class GnssSim {
    constructor(sc, staleS) { this.sc = sc; this.staleS = staleS; this.t0 = null; this.unplugged = false; this.lastFix = null; this.rtcm = {}; this.bytes = 0; this._last = null; }
    step(now) {
      if (this.t0 === null) { this.t0 = now; this._last = now; }
      const dt = now - this._last; this._last = now;
      if (this.unplugged) return;
      this.lastFix = now; this.bytes += this.sc.raw_bytes_per_s * dt;
      for (const [k, r] of Object.entries(this.sc.rtcm_types_per_s)) this.rtcm[k] = (this.rtcm[k] || 0) + r * dt;
    }
    snapshot(now) {
      const g = this.sc, types = {};
      Object.keys(this.rtcm).sort().forEach((k) => (types[k] = Math.floor(this.rtcm[k])));
      let fix = "no GGA yet";
      if (this.lastFix !== null) fix = now - this.lastFix > this.staleS ? `STALE (${(now - this.lastFix).toFixed(0)} s)` : `${g.quality_text} sats ${g.satellites} hdop ${g.hdop.toFixed(1)} ${g.latitude.toFixed(6)},${g.longitude.toFixed(6)}`;
      return {
        enabled: true, port_open: !this.unplugged, error: this.unplugged ? "port closed (simulated unplug)" : null, fix,
        bytes_logged: Math.floor(this.bytes), rtcm_frames: Object.values(types).reduce((a, b) => a + b, 0), rtcm_types: types, log_path: g.log_path,
      };
    }
  }

  /* --------------------------------------------------------------------- Sim */
  /** One whole system: payload, link, ground client, dashboard and GNSS. */
  class Sim {
    constructor(simConfig, scenario, now) {
      const b = simConfig.base, o = simConfig.onboard;
      this.cfg = simConfig; this.sc = scenario;
      this.link = new Link(scenario);
      this.payload = new Supervisor(o, scenario, this.link, now);
      this.client = new GroundClient(b, this.link, now);
      this.gnss = new GnssSim(scenario.gnss, b.gnss_stale_fix_s);
      this.dash = new Dashboard(b, this.client, this.gnss);
      this.tick = o.tick_s; this.poll = b.link_health.poll_s;
      this.now = now; this._nextTick = now; this._nextPoll = now;
      this.client.event(now, "SIMULATION: payload and link run in this browser; no hardware");
    }
    /** Advance to `t`, stepping each part at its own period, in time order. */
    advance(t) {
      while (Math.min(this._nextTick, this._nextPoll) <= t) {
        if (this._nextTick <= this._nextPoll) { this.now = this._nextTick; this.payload.step(this.now); this._nextTick += this.tick; }
        else { this.now = this._nextPoll; this.gnss.step(this.now); this.dash.step(this.now); this._nextPoll += this.poll; }
      }
      this.now = t;
    }
    /** Reboot only the payload: a new supervisor; the ground keeps its state. */
    rebootPayload() {
      // The data on the SSD survives a reboot, so keep the free space.
      const free = this.payload.diskFree;
      this.payload = new Supervisor(this.cfg.onboard, this.sc, this.link, this.now);
      this.payload.diskFree = free;
      this.client.event(this.now, "SIM: payload rebooted");
    }
  }

  global.HeronSim = { Sim, RecordingController, FakeBackend, GroundClient, linkHealth, makeFlightId, State, Health };
})(typeof window !== "undefined" ? window : globalThis);
