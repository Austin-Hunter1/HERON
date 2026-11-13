/* sim_shim.js - Join heron_sim.js to the real ground station page.
 *
 * The site build copies the real page (heron_base/web/index.html) and
 * adds this script in <head>, before the page's own script. This file:
 *
 * 1. Loads sim_config.json (from the real config templates), scenario.json
 *    (simulation-only values) and snapshot_template.json (a real snapshot).
 * 2. Replaces window.fetch for the two API routes, so the unchanged page
 *    talks to the simulation instead of a Python server.
 * 3. Runs the simulation clock, with a speed control.
 * 4. Adds a banner and a "scenario" panel: cut the link, fail an SDR,
 *    fill the disk, reboot the payload, and so on.
 * 5. Checks the key layout of its snapshot against the real one, and
 *    shows a warning when they differ (the Python code changed).
 */
(function () {
  "use strict";
  const realFetch = window.fetch.bind(window);
  const loadJson = (name) => realFetch(name, { cache: "no-store" }).then((r) => {
    if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
    return r.json();
  });

  let sim = null, speed = 1, simT = 0, wallT = 0, shapeChecked = false;
  const ready = Promise.all([loadJson("sim_config.json"), loadJson("scenario.json"), loadJson("snapshot_template.json")])
    .then(([cfg, sc, tpl]) => {
      simT = Date.now() / 1000; wallT = performance.now();
      sim = new window.HeronSim.Sim(cfg, sc, simT);
      startClock();
      return { cfg, sc, tpl };
    });

  /* ------------------------------------------------------------- the clock */
  /** Sim time advances `speed` sim seconds per real second. */
  function nowSim() { const w = performance.now(); simT += ((w - wallT) / 1000) * speed; wallT = w; return simT; }
  function startClock() { setInterval(() => sim.advance(nowSim()), 50); }

  /* ------------------------------------------------------------- the API */
  const json = (status, obj) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.pathname.endsWith("/api/state")) {
      const { tpl } = await ready;
      sim.advance(nowSim());
      const snap = sim.dash.snapshot(sim.now);
      if (!shapeChecked) checkShape(snap, tpl);
      updatePanel();
      return json(200, snap);
    }
    if (url.pathname.endsWith("/api/command")) {
      await ready;
      let data;
      try { data = JSON.parse((init && init.body) || "{}"); } catch { return json(400, { ok: false, message: "body is not JSON" }); }
      if (typeof data !== "object" || data === null || Array.isArray(data)) return json(400, { ok: false, message: "body is not an object" });
      const [ok, message] = sim.dash.queueCommand(data.name, data.flight_id);
      return json(ok ? 200 : 400, { ok, message });
    }
    return realFetch(input, init);
  };

  /* ------------------------------------------------------- the shape check */
  /** List the key differences of two JSON values (dict keys, first list item). */
  function shapeDiff(a, b, path, out) {
    const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
    if (isObj(a) && isObj(b)) {
      for (const k of Object.keys(b)) if (!(k in a)) out.push(`missing ${path}${k}`);
      for (const k of Object.keys(a)) if (!(k in b)) out.push(`extra ${path}${k}`);
      for (const k of Object.keys(b)) if (k in a) shapeDiff(a[k], b[k], `${path}${k}.`, out);
      return out;
    }
    if (Array.isArray(a) && Array.isArray(b) && a.length && b.length && typeof b[0] === "object") shapeDiff(a[0], b[0], `${path}[0].`, out);
    return out;
  }
  function checkShape(snap, tpl) {
    // Compare only when every part has data, as in the template.
    if (!snap.telemetry || !snap.history.length || !snap.telemetry.sdrs.length) return;
    shapeChecked = true;
    // Some dict keys are data, not layout: RTCM message numbers and
    // temperature sensor names (they depend on the machine). Empty them.
    const strip = (s) => Object.assign({}, s, {
      gnss: Object.assign({}, s.gnss, { rtcm_types: {} }),
      telemetry: Object.assign({}, s.telemetry, { temps_c: {} }),
    });
    const diff = shapeDiff(strip(snap), strip(tpl), "", []);
    if (diff.length) {
      console.warn("HERON sim: snapshot layout differs from the real dashboard:", diff);
      const w = document.getElementById("sim-warn");
      if (w) { w.hidden = false; w.textContent = `The simulation is out of date with the real dashboard (${diff.slice(0, 3).join(", ")}${diff.length > 3 ? ", …" : ""}).`; }
    }
  }

  /* -------------------------------------------------------- banner + panel */
  function el(tag, attrs = {}, kids = []) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) { if (k === "on") Object.entries(v).forEach(([ev, f]) => e.addEventListener(ev, f)); else if (k === "text") e.textContent = v; else e.setAttribute(k, v); }
    kids.forEach((k) => e.append(k));
    return e;
  }
  const act = (f) => () => { if (sim) { sim.advance(nowSim()); f(); updatePanel(); } };

  function buildUi({ cfg, sc }) {
    const banner = el("div", { id: "sim-banner" }, [
      el("a", { href: "../", text: "← herongnss.org" }),
      el("span", { class: "sim-tag", text: "SIMULATION" }),
      el("span", { text: "The real HERON ground station page. The payload and the radio link run in your browser. No hardware is connected." }),
      el("div", { id: "sim-warn", hidden: "" }),
    ]);
    document.body.prepend(banner);

    const sdrSel = el("select", { id: "sim-sdr" }, cfg.onboard.sdrs.map((s) => el("option", { value: s.id, text: `${s.id} (${s.model})` })));
    const speedBtns = sc.speeds.map((s) => el("button", { "data-speed": s, text: `${s}×`, on: { click: () => { nowSim(); speed = s; updatePanel(); } } }));
    const check = (id, label, f) => el("label", { class: "sim-check" }, [el("input", { type: "checkbox", id, on: { change: (e) => act(() => f(e.target.checked))() } }), " " + label]);
    const slider = (id, label, max, value, f) => el("label", { class: "sim-slider" }, [
      el("span", { text: label }), el("input", { type: "range", id, min: 0, max, step: 1, value, on: { input: (e) => { f(+e.target.value); document.getElementById(id + "-v").textContent = e.target.value + " %"; } } }), el("b", { id: id + "-v", text: value + " %" })]);

    const body = el("div", { id: "sim-body" }, [
      el("h3", { text: "Time" }), el("div", { class: "sim-row" }, speedBtns),
      el("dl", { id: "sim-state" }),
      el("h3", { text: "Radio link" }),
      check("sim-cut", "Cut the link (out of range)", (v) => (sim.link.cut = v)),
      slider("sim-loss", "Frame loss", 90, sc.link_loss_pct, (v) => (sim.link.lossPct = v)),
      slider("sim-bad", "Corrupt frames", 50, sc.corrupt_pct, (v) => (sim.link.corruptPct = v)),
      el("h3", { text: "Payload" }),
      el("div", { class: "sim-row" }, [sdrSel,
        el("button", { text: "Fail SDR", on: { click: act(() => sim.payload.backend.fail(sdrSel.value, "simulated recorder exit")) } }),
        el("button", { text: "Ref lock ⇄", on: { click: act(() => { const u = sim.payload.backend.unlocked; u[sdrSel.value] = !u[sdrSel.value]; }) } }),
        el("button", { text: "Overflow +1", on: { click: act(() => { const o = sim.payload.backend.overflows; o[sdrSel.value] = (o[sdrSel.value] || 0) + 1; }) } })]),
      el("div", { class: "sim-row" }, [
        el("button", { text: "Kill all recorders", on: { click: act(() => sim.payload.backend.killAll()) } }),
        el("button", { text: "Reboot payload", on: { click: act(() => sim.rebootPayload()) } })]),
      el("div", { class: "sim-row" }, [
        el("button", { text: "Fill disk to the limit", on: { click: act(() => (sim.payload.diskFree = cfg.onboard.disk.min_free_gb + sc.fill_disk_margin_gb)) } }),
        el("button", { text: "Offload data", on: { click: act(() => (sim.payload.diskFree = sc.disk_free_gb)) } })]),
      el("h3", { text: "Ground GNSS" }),
      check("sim-gnss", "Unplug the GNSS receiver", (v) => (sim.gnss.unplugged = v)),
      el("details", {}, [el("summary", { text: "Things to try" }), el("ul", {}, [
        el("li", { text: `Wait: with no command for ${cfg.onboard.control.link_grace_s} s after boot, the payload starts to record on its own (fallback, D-017). Use 20× to see it soon.` }),
        el("li", { text: "START, then cut the link: the ground shows DEGRADED, then LOST, but the payload keeps recording (D-011)." }),
        el("li", { text: "Cut the link and press STOP: the ground resends the command, then reports FAILED: no ack." }),
        el("li", { text: "Fail one SDR: the others keep recording. Kill all: the payload goes to FAULT; STOP clears it." }),
        el("li", { text: "Fill the disk while recording: the payload stops and refuses a new START until you offload data." }),
      ])]),
    ]);
    const panel = el("aside", { id: "sim-panel" }, [
      el("button", { id: "sim-toggle", text: "Scenario ▾", on: { click: () => panel.classList.toggle("closed") } }), body]);
    // On a narrow screen the panel would cover the dashboard, so it starts closed.
    if (window.matchMedia("(max-width: 1099px)").matches) panel.classList.add("closed");
    document.body.append(panel);
    updatePanel();
  }

  /** Show the payload-side state that the ground cannot see. */
  function updatePanel() {
    const box = document.getElementById("sim-state");
    if (!box || !sim) return;
    document.querySelectorAll("#sim-body [data-speed]").forEach((b) => b.classList.toggle("on", +b.dataset.speed === speed));
    const c = sim.payload.ctrl, cfg = sim.cfg.onboard.control, t = sim.now;
    let fb = c.fallbackArmed ? "armed" : "off";
    if (c.fallbackArmed && c.linkState === "NEVER" && c.state === "IDLE" && cfg.fallback_mode === "record_if_no_link") fb = `starts in ${Math.max(0, cfg.link_grace_s - (t - c.bootTime)).toFixed(0)} s`;
    const rows = [
      ["sim time", new Date(t * 1000).toISOString().slice(11, 19) + "Z"],
      ["payload sees link", c.linkState],
      ["fallback", fb],
      ["SSD free", `${sim.payload.diskFree.toFixed(1)} GB`],
    ];
    box.innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
  }

  ready.then((r) => {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => buildUi(r)); else buildUi(r);
  }).catch((e) => {
    console.error("HERON sim failed to start:", e);
    document.addEventListener("DOMContentLoaded", () => document.body.prepend(el("div", { id: "sim-banner", text: "The simulation could not load: " + e.message })));
  });
})();
