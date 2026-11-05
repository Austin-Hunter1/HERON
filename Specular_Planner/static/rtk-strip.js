// RTK corrections bar + panel for the Fly deck. A base station on this laptop sends RTCM corrections;
// engine/rtk.py forwards them to the drone over the MAVLink link. The bar shows the drone's fix;
// the panel holds the base's setup and one action bar (text actions left, one primary action right).
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const root = $("rtkStrip");
  if (!root) return;

  let token = "", last = null, busy = false, open = false, noteUntil = 0, portsKey = "", spot = null, editing = false, naming = false;

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
    if (!a.connected) return { fix: "base", badge: "Ready", title: "Base ready", sub: s.finished_early ? `Survey stopped early, base within ${metres(s.survey?.acc_m)}` : "Corrections start when the drone connects" };
    if (a.fix === 6) return { fix: "fixed", badge: "Fixed", title: "RTK fixed", sub: a.baseline_m ? `${distance(a.baseline_m)} from base, ${rate(s.rate_bps)}` : `Sending ${rate(s.rate_bps)}` };
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
    align();
  }

  function renderPanel() {
    const s = last || {}, a = s.aircraft || {}, base = s.base_state, running = !!s.running;
    const sv = s.survey;
    const surveying = running && base === "surveying" && sv;
    const showSetup = !running || editing;
    if (!running || surveying || !s.can_save) naming = false;

    // Live facts while the base runs and isn't surveying
    $("rtkFacts").hidden = !running || surveying || showSetup;
    if (running && !surveying) {
      const pos = s.base_position;
      const how = s.options?.mode === "saved" ? `Saved, ${s.options.spot}`
        : s.options?.mode === "as_is" ? "From the receiver"
        : sv && sv.acc_m ? `Surveyed, within ${metres(sv.acc_m)}${s.finished_early ? " (stopped early)" : ""}` : "Surveyed";
      $("rtkFactBase").textContent = pos || s.options?.mode !== "survey" ? how : "Waiting for the base";
      $("rtkFactLink").textContent = !a.connected ? "Waiting for the drone" : s.rate_bps ? `${rate(s.rate_bps)} to the drone` : "Nothing sent yet";
    }

    // Survey progress
    $("rtkSurvey").hidden = !surveying || showSetup;
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
    $("rtkSetup").hidden = !showSetup;
    $("rtkSaveRow").hidden = !naming;

    // Action bar: at most one primary button, chosen by state.
    const primary = showSetup ? "rtkStart" : surveying ? "rtkFinish" : naming ? "rtkSave" : s.can_save ? "rtkSaveOpen" : null;
    ["rtkStart", "rtkFinish", "rtkSave", "rtkSaveOpen"].forEach((id) => { $(id).hidden = id !== primary; });
    $("rtkStart").textContent = running ? "Restart base" : "Start base";
    $("rtkStart").disabled = busy || !$("rtkPort").value || (mode() === "saved" && !spot);
    $("rtkFinish").disabled = busy || !s.can_finish;
    $("rtkSave").disabled = busy;
    $("rtkSaveOpen").disabled = busy;
    $("rtkEdit").hidden = !running || showSetup || naming;
    $("rtkStop").hidden = !running || showSetup || naming;
    $("rtkCancel").hidden = !((running && editing) || naming);
    $("rtkStop").disabled = busy;
    if (Date.now() > noteUntil) note("");
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
    if (!open) editing = naming = false;
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
  $("rtkFinish").addEventListener("click", async () => {
    if (await send("finish")) note("Survey stopped. The base is sending from the position averaged so far.");
  });
  $("rtkStop").addEventListener("click", async () => { if (await send("stop")) { editing = false; note("Base stopped."); } });
  $("rtkEdit").addEventListener("click", () => { editing = true; loadPorts(); renderPanel(); });
  $("rtkCancel").addEventListener("click", () => { editing = naming = false; note(""); renderPanel(); });
  $("rtkSaveOpen").addEventListener("click", () => { naming = true; renderPanel(); $("rtkSpotName").focus(); });
  $("rtkSave").addEventListener("click", async () => {
    const name = $("rtkSpotName").value;
    if (await send("save", { name })) { spot = name.trim(); naming = false; $("rtkSpotName").value = ""; note(`Saved “${spot}”.`); }
  });
  $("rtkSpotName").addEventListener("keydown", (e) => { if (e.key === "Enter") $("rtkSave").click(); });
  ["rtkMinS", "rtkAccM"].forEach((id) => {
    $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") $("rtkStart").click(); });
    $(id).addEventListener("input", () => { if ($("rtkNote").classList.contains("err")) note(""); });
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && open) toggle(false); });
  document.addEventListener("pointerdown", (e) => { if (open && !root.contains(e.target)) toggle(false); });

  // Sit directly under the payload strip, on the same centre line.
  // Before the drone connects the payload strip is hidden and the CSS centring applies instead.
  const deck = document.getElementById("deck");
  const payload = document.getElementById("payloadStrip");
  function align() {
    const r = payload?.getBoundingClientRect();
    if (!deck || !payload || !deck.classList.contains("attached") || !r || !r.width) {
      ["left", "right", "top", "margin"].forEach((k) => root.style.removeProperty(k));
      return;
    }
    const d = deck.getBoundingClientRect();
    const w = root.getBoundingClientRect().width;
    root.style.left = `${Math.round(r.left - d.left + (r.width - w) / 2)}px`;
    root.style.right = "auto";
    root.style.top = `${Math.round(r.bottom - d.top + 8)}px`;
    root.style.margin = "0";
  }
  if (payload && "ResizeObserver" in window) new ResizeObserver(align).observe(payload);
  if (deck) new MutationObserver(align).observe(deck, { attributes: true, attributeFilter: ["class"] });
  window.addEventListener("resize", align);

  new MutationObserver(() => { if (!flying() && open) toggle(false); poll(); align(); })
    .observe(document.body, { attributes: true, attributeFilter: ["data-view"] });
  setInterval(poll, 1000);
  poll();
})();
