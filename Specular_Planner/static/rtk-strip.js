// RTK corrections bar + panel for the Fly deck. A base station on this laptop sends RTCM corrections;
// engine/rtk.py forwards them to the drone over the MAVLink link. The bar shows the drone's fix;
// the panel shows the path base -> HERON -> drone and the base's setup.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const root = $("rtkStrip");
  if (!root) return;

  let token = "", last = null, busy = false, open = false, noteUntil = 0, portsKey = "", spot = null, editing = false;

  const flying = () => document.body.dataset.view === "fly";
  const clock = (s) => {
    s = Math.max(0, Math.floor(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  };
  const metres = (m) => m == null ? "—" : m < 1 ? Math.round(m * 100) + " cm" : m < 10 ? m.toFixed(1) + " m" : Math.round(m) + " m";
  const distance = (m) => m == null ? "" : m < 1000 ? Math.round(m) + " m" : (m / 1000).toFixed(1) + " km";
  const rate = (bps) => !bps ? "" : bps >= 1000 ? (bps / 1000).toFixed(1) + " kB/s" : Math.round(bps) + " B/s";
  const mode = () => document.querySelector('#rtkMode input:checked')?.value || "survey";
  const date = (iso) => { try { return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }); } catch { return ""; } };

  async function session() {
    if (!token) token = (await (await fetch("/api/rtk/session")).json()).token;
    return token;
  }

  async function poll() {
    if (!flying()) return;
    try {
      const r = await fetch("/api/rtk/status");
      if (!r.ok) throw new Error((await r.json()).error);
      last = await r.json();
      render();
    } catch (e) { /* keep the last view */ }
  }

  async function send(path, body = {}) {
    busy = true; render();
    try {
      const r = await fetch("/api/rtk/" + path, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-RTK-Token": await session() },
        body: JSON.stringify(body),
      });
      const out = await r.json();
      if (!r.ok) throw new Error(out.error || "The base station didn't respond.");
      last = out;
      return true;
    } catch (e) {
      note(e.message, true);
      return false;
    } finally { busy = false; render(); }
  }

  function note(text, bad) {
    const el = $("rtkNote");
    el.textContent = text || ""; el.classList.toggle("err", !!bad);
    noteUntil = text ? Date.now() + 8000 : 0;
  }

  // What the bar says. The drone's fix leads once corrections flow; before that, the base's state.
  function headline(s) {
    const a = s.aircraft || {}, base = s.base_state;
    if (base === "error") return { fix: "bad", badge: "Base", title: "Base station disconnected", sub: s.error || "Check the cable, then start it again." };
    if (s.config === "rejected") return { fix: "bad", badge: "Base", title: "Base refused the settings", sub: "Set it up in u-center, then choose “Use the receiver's settings”." };
    if (base === "off") return { fix: "none", badge: "RTK off", title: "No base station", sub: a.connected ? `Drone on ${a.fix_name}` : "Set up a base to send corrections" };
    if (base === "surveying") {
      const sv = s.survey || {};
      return { fix: "base", badge: "Survey", title: `Base surveying  ${clock(sv.duration_s)}`, sub: `Within ${metres(sv.acc_m)} so far, target ${metres(Number(s.options?.acc_m))}` };
    }
    if (base === "silent") return { fix: "bad", badge: "Base", title: "No data from the base", sub: s.rx_age_s == null ? "Check the port and that the base is outputting RTCM." : `Nothing for ${Math.round(s.rx_age_s)} s` };
    if (!a.connected) return { fix: "base", badge: "Ready", title: "Base ready", sub: "Corrections start when the drone connects" };
    if (a.fix === 6) return { fix: "fixed", badge: "Fixed", title: "RTK fixed", sub: `Sending ${rate(s.rate_bps)}${a.baseline_m ? ", " + distance(a.baseline_m) + " from base" : ""}` };
    if (a.fix === 5) return { fix: "float", badge: "Float", title: "RTK float", sub: "Converging. Usually fixed within a few minutes." };
    return { fix: "base", badge: a.fix_name === "—" ? "No GPS" : a.fix_name.replace(" fix", ""), title: "Waiting for RTK", sub: `Sending ${rate(s.rate_bps) || "corrections"}. The drone needs a clear sky view.` };
  }

  function render() {
    const s = last;
    if (!s) return;
    const a = s.aircraft || {};
    const h = headline(s);
    root.dataset.fix = h.fix;
    root.dataset.state = s.base_state;
    $("rtkBadge").textContent = h.badge;
    $("rtkState").textContent = h.title;
    $("rtkSub").textContent = h.sub; $("rtkSub").title = h.sub;
    $("rtkSub").classList.toggle("err", h.fix === "bad");
    $("rtkAcc").textContent = a.h_acc_m != null ? metres(a.h_acc_m) : "—";
    root.dataset.acc = a.h_acc_m != null ? "some" : "none";
    if (open) renderPanel();
  }

  function renderPanel() {
    const s = last || {}, a = s.aircraft || {}, base = s.base_state, running = !!s.running;

    // Correction path
    const baseNode = $("rtkNodeBase");
    baseNode.className = "node " + ({ surveying: "wait", receiving: "on", sending: "on", silent: "warn", error: "bad" }[base] || "");
    $("rtkNodeBaseText").textContent = { off: "Off", surveying: "Surveying", receiving: "Ready", sending: "Ready", silent: "No data", error: "Disconnected" }[base] || "—";
    const inOn = running && s.rx_age_s != null && s.rx_age_s < 5;
    $("rtkLinkIn").classList.toggle("on", inOn);
    $("rtkLinkInText").textContent = inOn ? rate(s.rx_rate_bps) : "";
    const outOn = inOn && a.connected && s.rate_bps > 0;
    $("rtkLinkOut").classList.toggle("on", outOn);
    $("rtkLinkOutText").textContent = outOn ? rate(s.rate_bps) : running && inOn && !a.connected ? "waiting" : "";
    const drone = $("rtkNodeDrone");
    drone.className = "node " + (!a.connected ? "" : a.fix === 6 ? "on" : a.fix === 5 ? "float" : "wait");
    $("rtkNodeDroneText").textContent = !a.connected ? "Not connected" : a.fix === 6 || a.fix === 5 ? `${a.fix === 6 ? "Fixed" : "Float"}, ${metres(a.h_acc_m)}` : a.fix_name;

    // Survey progress
    const sv = s.survey;
    const surveying = base === "surveying" && sv;
    $("rtkSurvey").hidden = !surveying;
    if (surveying) {
      const minS = Number(s.options?.min_s) || 120, target = Number(s.options?.acc_m) || 2;
      const tFrac = Math.min(1, sv.duration_s / minS), aFrac = sv.acc_m ? Math.min(1, target / sv.acc_m) : 0;
      $("rtkTimeBar").style.width = (tFrac * 100).toFixed(1) + "%"; $("rtkTimeBar").classList.toggle("done", tFrac >= 1);
      $("rtkAccBar").style.width = (aFrac * 100).toFixed(1) + "%"; $("rtkAccBar").classList.toggle("done", aFrac >= 1);
      $("rtkTimeText").textContent = `${clock(sv.duration_s)} / ${clock(minS)}`;
      $("rtkAccText").textContent = `${metres(sv.acc_m)} / ${metres(target)}`;
    }

    // Saved positions
    const saved = s.saved || [];
    if (!saved.some((p) => p.name === spot)) spot = saved[0]?.name ?? null;
    const key = JSON.stringify([saved.map((p) => p.name), spot, busy]);
    const list = $("rtkSpots");
    if (list.dataset.key !== key) {
      list.replaceChildren(...saved.map((p) => {
        const li = document.createElement("li");
        const pick = document.createElement("button");
        pick.type = "button"; pick.className = "pick"; pick.setAttribute("aria-pressed", String(p.name === spot));
        const b = document.createElement("b"); b.textContent = p.name;
        const sm = document.createElement("small");
        sm.textContent = [p.acc_m != null ? "within " + metres(p.acc_m) : null, p.saved_at ? "saved " + date(p.saved_at) : null].filter(Boolean).join(", ");
        pick.append(b, sm);
        pick.addEventListener("click", () => { spot = p.name; renderPanel(); });
        const forget = document.createElement("button");
        forget.type = "button"; forget.className = "forget"; forget.textContent = "Remove"; forget.disabled = busy;
        forget.setAttribute("aria-label", "Remove " + p.name);
        forget.addEventListener("click", () => send("forget", { name: p.name }));
        li.append(pick, forget);
        return li;
      }));
      list.dataset.key = key;
    }
    $("rtkSavedEmpty").hidden = saved.length > 0;
    $("rtkSaveRow").hidden = !(running && s.can_save);

    // Actions. While the base runs, its setup folds away behind "Change setup".
    const showSetup = !running || editing;
    $("rtkSetup").hidden = !showSetup;
    $("rtkEdit").hidden = showSetup;
    const start = $("rtkStart");
    start.hidden = !showSetup;
    start.textContent = running ? "Apply and restart" : "Start base";
    start.disabled = busy || !$("rtkPort").value || (mode() === "saved" && !spot);
    $("rtkStop").hidden = !running;
    $("rtkStop").disabled = busy;
    const types = Object.keys(s.messages || {});
    $("rtkMsgs").textContent = running && types.length ? "Receiving " + types.join(", ") : "";
    $("rtkMsgs").title = Object.entries(s.messages || {}).map(([k, v]) => `RTCM ${k}: ${v}`).join("\n");
    if (Date.now() > noteUntil) {
      const slow = a.connected && running && a.transport && !["serial", "tcp", "herelink_hotspot", "herelink_client"].includes(a.transport);
      note(slow ? "Corrections need about 1–2 kB/s on the drone link. ELRS may be too slow." : "");
    }
  }

  async function loadPorts() {
    try {
      const r = await (await fetch("/api/rtk/ports")).json();
      const ports = (r.ports || []).filter((p) => !p.autopilot);
      const key = JSON.stringify(ports.map((p) => p.device));
      if (key === portsKey) return;
      portsKey = key;
      const sel = $("rtkPort"), keep = sel.value || last?.options?.port;
      const label = (p) => {
        const name = p.device.replace(/^\/dev\/(cu\.|tty\.)?/, "");
        return p.description && p.description !== p.device && p.description !== "n/a" ? `${p.description} (${name})` : name;
      };
      sel.replaceChildren(...(ports.length ? ports.map((p) => new Option(label(p), p.device)) : [new Option("No USB devices found", "")]));
      if (ports.some((p) => p.device === keep)) sel.value = keep;
      else {
        const guess = ports.find((p) => /u-blox|ublox|gnss|gps|here|simplertk|f9p|ardusimple/i.test(p.description || ""));
        if (guess) sel.value = guess.device;
      }
      render();
    } catch (e) { /* leave the list alone */ }
  }

  function toggle(next) {
    open = next ?? !open;
    if (!open) editing = false;
    root.classList.toggle("open", open);
    $("rtkPanel").hidden = !open;
    $("rtkOpen").setAttribute("aria-expanded", String(open));
    if (open) {
      const o = last?.options || {};
      if (o.mode) document.querySelector(`#rtkMode input[value="${o.mode}"]`).checked = true;
      if (o.mode === "saved" && o.spot) spot = o.spot;
      if (o.min_s) $("rtkMinS").value = String(+(Number(o.min_s) / 60).toFixed(2));
      if (o.acc_m) $("rtkAccM").value = String(Number(o.acc_m));
      loadPorts();
      renderPanel();
    }
  }

  $("rtkOpen").addEventListener("click", () => toggle());
  document.querySelectorAll("#rtkMode input").forEach((r) => r.addEventListener("change", renderPanel));
  $("rtkPort").addEventListener("change", renderPanel);
  $("rtkPortRefresh").addEventListener("click", () => { portsKey = ""; loadPorts(); });
  $("rtkStart").addEventListener("click", async () => {
    const m = mode();
    const body = { port: $("rtkPort").value, baud: Number($("rtkBaud").value), mode: m };
    if (m === "survey") {
      const minutes = Number($("rtkMinS").value), acc = Number($("rtkAccM").value);
      if (!(minutes >= 0.5 && minutes <= 1440)) { note("Survey time must be 0.5 to 1440 minutes.", true); $("rtkMinS").focus(); return; }
      if (!(acc >= 0.01 && acc <= 50)) { note("Accuracy must be 0.01 to 50 m.", true); $("rtkAccM").focus(); return; }
      Object.assign(body, { min_s: Math.round(minutes * 60), acc_m: acc });
    }
    if (m === "saved") body.spot = spot;
    if (await send("start", body)) { editing = false; note(m === "survey" ? "Survey started." : "Base started."); }
  });
  $("rtkStop").addEventListener("click", async () => { if (await send("stop")) { editing = false; note("Base stopped."); } });
  $("rtkEdit").addEventListener("click", () => { editing = true; loadPorts(); renderPanel(); });
  $("rtkSave").addEventListener("click", async () => {
    const name = $("rtkSpotName").value;
    if (await send("save", { name })) { spot = name.trim(); $("rtkSpotName").value = ""; note(`Saved “${spot}”.`); }
  });
  $("rtkSpotName").addEventListener("keydown", (e) => { if (e.key === "Enter") $("rtkSave").click(); });
  ["rtkMinS", "rtkAccM"].forEach((id) => {
    $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") $("rtkStart").click(); });
    $(id).addEventListener("input", () => { if ($("rtkNote").classList.contains("err")) note(""); });
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && open) toggle(false); });
  document.addEventListener("pointerdown", (e) => { if (open && !root.contains(e.target)) toggle(false); });

  new MutationObserver(() => { if (!flying() && open) toggle(false); poll(); })
    .observe(document.body, { attributes: true, attributeFilter: ["data-view"] });
  setInterval(poll, 1000);
  poll();
})();
