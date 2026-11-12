// Payload recorder strip for the Simulate and Fly decks. Configuration lives on the NUC (Payload Setup);
// this only shows recorder status and sends Record / Stop / Auto.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const strip = $("payloadStrip");
  if (!strip) return;

  let token = "", planId = null, last = null, busy = false, popOpen = false, errorUntil = 0;
  let recMode = "phases";
  let notRecSince = 0;

  const mode = () => ({ sim: "sim", fly: "fly" })[document.body.dataset.view] || null;
  const clock = (s) => {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  };
  const minutes = (m) => m == null ? "—" : m >= 90 ? Math.floor(m / 60) + " h " + String(m % 60).padStart(2, "0") + " m" : m + " min";

  // Map labels say WP1, WP2...; ArduPilot item numbers include takeoff/yaw items. Show the map numbers.
  const wpName = (seq) => {
    const n = last?.waypoints?.numbers?.[String(seq)];
    return n ? "WP" + n : "item " + seq;
  };

  async function session() {
    if (!token) token = (await (await fetch("/api/payload/session")).json()).token;
    return token;
  }

  async function poll() {
    const m = mode();
    if (!m) return;
    try {
      const q = new URLSearchParams({ mode: m });
      if (planId) q.set("plan_id", planId);
      const r = await fetch("/api/payload/status?" + q);
      if (!r.ok) throw new Error((await r.json()).error);
      last = await r.json();
      render();
    } catch (e) { /* keep the last view; the deck has its own link indicator */ }
  }

  async function send(action, extra = {}) {
    busy = true; render();
    try {
      const r = await fetch("/api/payload/command", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Payload-Token": await session() },
        body: JSON.stringify({ mode: mode(), action, plan_id: planId, ...extra }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.error || "Payload command failed");
      last = body;
      return true;
    } catch (e) {
      say(e.message, true);
      return false;
    } finally { busy = false; render(); }
  }

  function say(text, bad) {
    const sub = $("plSub");
    sub.textContent = text; sub.title = text; sub.classList.toggle("err", !!bad);
    errorUntil = Date.now() + 6000;
    if (popOpen) { $("plPopNote").textContent = text; $("plPopNote").classList.toggle("err", !!bad); }
  }

  function render() {
    const s = last;
    if (!s) return;
    const st = s.state;
    strip.dataset.state = st;
    const auto = s.auto;
    const phase = auto?.segments ? `Phase ${auto.segment} of ${auto.segments}: ` : "";
    const [title, sub] = {
      OFFLINE: ["Payload", "No signal from the NUC"],
      IDLE: ["No setup loaded", "Open Payload Setup on the NUC"],
      READY: ["Payload ready", s.setup || "Setup loaded"],
      ARMED: ["Auto-record armed", auto ? (auto.segments
        ? `${phase}starts at ${wpName(auto.start_seq)}, ends after ${wpName(auto.stop_seq)}`
        : `Starts at ${wpName(auto.start_seq)}, stops at ${wpName(auto.stop_seq)}`) : ""],
      RECORDING: ["REC " + clock(s.elapsed_s), auto ? (auto.segments
        ? `${phase}ends after ${wpName(auto.stop_seq)}`
        : `Auto, stops at ${wpName(auto.stop_seq)}`) : "Manual: stop when done"],
      FAULT: ["Payload fault", s.fault || s.message || "Check Payload Setup on the NUC"],
      PREPARING: ["Starting radios…", s.setup || ""],
      FINALIZING: ["Finishing files…", ""],
    }[st] || [st, ""];
    $("plState").textContent = title;
    // In the air, armed for a phase whose first waypoint is already behind the aircraft, and still
    // not recording after 5 s: say so loudly and offer the manual Record button.
    const ac = s.aircraft || {};
    if (st === "ARMED" && auto && ac.armed && ac.auto && ac.current != null && ac.current > auto.start_seq) {
      notRecSince = notRecSince || Date.now();
      if (Date.now() - notRecSince > 5000) say(`Not recording! Aircraft is past ${wpName(auto.start_seq)}. Tap Record.`, true);
    } else notRecSince = 0;
    if (Date.now() > errorUntil) {
      const subEl = $("plSub");
      subEl.textContent = sub; subEl.title = sub; subEl.classList.remove("err");
    }
    const live = s.connected;
    $("plRate").textContent = live && st === "RECORDING" ? s.rate_mbps.toFixed(1) + " MB/s" : "—";
    $("plLeft").textContent = live ? minutes(s.minutes_left) : "—";
    $("plOvf").textContent = live ? String(s.overflows) : "—";
    $("plOvf").classList.toggle("bad", live && s.overflows > 0);
    $("plLeft").classList.toggle("bad", live && s.minutes_left != null && s.minutes_left < 5);

    const rec = $("plRec");
    const recording = st === "RECORDING";
    rec.classList.toggle("stop", recording);
    rec.querySelector("span").textContent = recording ? "Stop" : "Record";
    rec.title = st === "ARMED" ? "Start recording now instead of waiting for the start waypoint" : "";
    rec.disabled = busy || !live || !(recording || st === "READY" || st === "ARMED");

    const autoBtn = $("plAuto");
    const armed = st === "ARMED" || (recording && auto);
    autoBtn.classList.toggle("on", !!armed);
    $("plAutoText").textContent = !(armed && auto) ? (s.arm_at_launch === false ? "off" : "at launch")
      : auto.segments ? `phase ${auto.segment}/${auto.segments}`
      : `${wpName(auto.start_seq)}–${wpName(auto.stop_seq).replace("WP", "")}`;
    autoBtn.disabled = busy;
    if (autoBtn.disabled && popOpen) togglePop(false);

    if (popOpen) renderPop();
  }

  function renderPop() {
    const w = last?.waypoints || { waypoints: [] };
    const opts = (sel, value) => {
      const keep = sel.value;
      sel.innerHTML = w.waypoints.map((seq) => `<option value="${seq}">${wpName(seq)}  (item ${seq})</option>`).join("");
      sel.value = keep && w.waypoints.includes(Number(keep)) ? keep : value ?? "";
    };
    const a = last?.auto;
    // The plan's recording (Plan tab: "Record this phase" + "Payload recording") sets the defaults.
    const phases = w.phases || [];
    const recorded = phases.filter((p) => p.record !== false);
    const planStart = recorded.length ? recorded[0].start_seq : w.start_seq;
    const planStop = recorded.length ? recorded[recorded.length - 1].stop_seq : w.stop_seq;
    opts($("plStart"), a?.start_seq ?? planStart);
    opts($("plStop"), a?.stop_seq ?? planStop);
    const canPhases = recorded.length > 0 && recorded.length <= (w.max_phases || 4);
    const usePhases = canPhases && recMode === "phases";
    $("plModeRow").classList.toggle("hidden", !canPhases);
    document.querySelector(`input[name="plMode"][value="${usePhases ? "phases" : "range"}"]`).checked = true;
    $("plPhases").classList.toggle("hidden", !usePhases);
    $("plRangeRow").classList.toggle("hidden", usePhases);
    $("plPhases").replaceChildren(...phases.map((p, i) => {
      const li = document.createElement("li");
      const k = recorded.indexOf(p);
      li.classList.toggle("now", !!(a?.segments && k >= 0 && a.segment === k + 1));
      li.classList.toggle("off", k < 0);
      const nm = document.createElement("span"); nm.className = "nm"; nm.textContent = p.name;
      const wp = document.createElement("span"); wp.className = "wp";
      wp.textContent = p.first_wp === p.last_wp ? `WP${p.first_wp}` : `WP${p.first_wp}–${p.last_wp}`;
      if (k < 0) wp.textContent += " · not recorded";
      li.append(nm, wp);
      return li;
    }));
    $("plAtLaunch").checked = last?.arm_at_launch !== false;
    const none = usePhases ? false : w.waypoints.length < 2;
    $("plArm").disabled = busy || none || !last?.connected || !["READY", "ARMED"].includes(last?.state);
    $("plArm").textContent = last?.state === "ARMED" ? "Update" : "Arm now";
    $("plCancelAuto").disabled = busy || last?.state !== "ARMED";
    if (!$("plPopNote").textContent) {
      const fromPlan = w.recording_mode ? " Set in the Plan tab." : "";
      $("plPopNote").textContent = usePhases ? `${recorded.length} separate recording${recorded.length === 1 ? "" : "s"}. Each starts on arrival at its first waypoint and ends when its last one is finished.${fromPlan}`
        : recorded.length > (w.max_phases || 4) ? `Per-phase recording handles up to ${w.max_phases || 4} phases; this plan records ${recorded.length}.`
        : w.recording_mode === "continuous" && recorded.length ? `One file from ${wpName(planStart)} to ${wpName(planStop)}.${fromPlan}`
        : none ? "Put a survey mission on the drone first."
        : w.source === "plan" ? "Using the HERON plan. Write it to the drone before flying." : "Using the mission on the drone.";
    }
  }

  function togglePop(open) {
    popOpen = open ?? !popOpen;
    $("plPop").classList.toggle("hidden", !popOpen);
    $("plAuto").setAttribute("aria-expanded", String(popOpen));
    $("plPopNote").textContent = "";
    $("plPopNote").classList.remove("err");
    if (popOpen) {
      const a = last?.auto;
      if (a && last?.state === "ARMED") recMode = a.segments ? "phases" : "range";
      else if (last?.waypoints?.recording_mode) recMode = last.waypoints.recording_mode === "continuous" ? "range" : "phases";
      renderPop();
    }
  }

  $("plRec").addEventListener("click", () => send(last?.state === "RECORDING" ? "stop" : "start"));
  $("plAuto").addEventListener("click", () => togglePop());
  document.querySelectorAll('input[name="plMode"]').forEach((r) => r.addEventListener("change", () => {
    recMode = r.value;
    $("plPopNote").textContent = "";
    renderPop();
  }));
  $("plArm").addEventListener("click", async () => {
    const phases = last?.waypoints?.phases || [];
    const ok = !$("plPhases").classList.contains("hidden") && phases.length
      ? await send("arm_segments", { segments: phases.filter((p) => p.record !== false).map((p) => [p.start_seq, p.stop_seq]) })
      : await send("arm", { start_seq: Number($("plStart").value), stop_seq: Number($("plStop").value) });
    if (ok) togglePop(false);
  });
  $("plAtLaunch").addEventListener("change", async (e) => {
    const enabled = e.target.checked;
    try {
      const r = await fetch("/api/payload/arm-at-launch", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Payload-Token": await session() },
        body: JSON.stringify({ mode: mode(), enabled }),
      });
      if (!r.ok) throw new Error((await r.json()).error || "Could not change that");
      if (last) last.arm_at_launch = (await r.json()).arm_at_launch;
    } catch (err) { say(err.message, true); }
    render();
  });
  $("plCancelAuto").addEventListener("click", async () => { if (await send("cancel_auto")) togglePop(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && popOpen) togglePop(false); });
  document.addEventListener("pointerdown", (e) => { if (popOpen && !strip.contains(e.target)) togglePop(false); });

  window.addEventListener("heron:plan-ready", (e) => { planId = e.detail?.flight_plan_id || null; poll(); });
  window.addEventListener("heron:plan-invalid", () => { planId = null; });
  new MutationObserver(() => { last = null; poll(); }).observe(document.body, { attributes: true, attributeFilter: ["data-view"] });
  setInterval(poll, 1000);
  poll();
})();
