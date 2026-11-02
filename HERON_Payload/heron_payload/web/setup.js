(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const BANDS = [
    { name: "L1", mhz: 1575.42 },
    { name: "L2", mhz: 1227.6 },
    { name: "L5", mhz: 1176.45 },
  ];
  // Radio profiles of the collect wizard (collect_wizard.py radio_profiles). A profile sets the centre,
  // sample rate and bandwidth; the operator can still type any value (D-078).
  const PROFILES = [
    { name: "L5 Band", mhz: 1176.45, msps: 15, bw: 15 },
    { name: "L1 Band", mhz: 1575.42, msps: 15, bw: 15 },
    { name: "L1CA", mhz: 1575.42, msps: 5, bw: 5 },
  ];
  const STATE_TEXT = {
    IDLE: ["No setup loaded", "warn"], PREPARING: ["Loading setup…", "warn"], READY: ["Ready", "good"],
    AUTO_ARMED: ["Armed for auto-record", "warn"], RECORDING: ["Recording", "bad"],
    FINALIZING: ["Finishing files…", "warn"], FAULT: ["Fault", "bad"],
  };

  let setups = [], draft = null, active = null, status = null, disk = null, locked = false;
  let devices = [], test = null, dirty = false, polling = null, ssdDir = null;

  async function api(path, body) {
    const opt = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Heron": "1" }, body: JSON.stringify(body),
    };
    const r = await fetch(path, opt);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }
  function toast(text, bad) {
    const t = $("toast");
    t.textContent = text; t.className = "toast" + (bad ? " bad" : "");
    clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add("hidden"), bad ? 6000 : 3000);
  }
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const get = (obj, path) => path.split(".").reduce((o, k) => o?.[k], obj);
  function set(obj, path, v) { const ks = path.split("."); const last = ks.pop(); ks.reduce((o, k) => o[k], obj)[last] = v; }
  const fmtGB = (b) => b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : b >= 1e6 ? (b / 1e6).toFixed(0) + " MB" : Math.ceil(b / 1e3) + " kB";
  function fmtMinutes(m) {
    if (!isFinite(m) || m < 0) return "—";
    if (m < 90) return Math.floor(m) + " min";
    return Math.floor(m / 60) + " h " + String(Math.floor(m % 60)).padStart(2, "0") + " min";
  }

  // ---------------- editor ----------------
  function render() {
    if (!draft) return;
    $("setupName").value = draft.name;
    $("setupSelect").innerHTML = setups.map((s) =>
      `<option value="${esc(s.name)}"${s.name === draft.name ? " selected" : ""}>${esc(s.name)}${s.builtin ? " (built-in)" : ""}</option>`
    ).join("") + (setups.some((s) => s.name === draft.name) ? "" : `<option selected>${esc(draft.name)} (unsaved)</option>`);
    const builtin = setups.find((s) => s.name === draft.name)?.builtin;
    $("deleteSetup").disabled = !setups.some((s) => s.name === draft.name) || builtin;

    const root = $("radios");
    root.innerHTML = "";
    draft.radios.forEach((r, ri) => root.appendChild(radioEl(r, ri)));
    document.querySelectorAll("[data-k]").forEach((el) => {
      const v = get(draft, el.dataset.k);
      if (el.type === "checkbox") el.checked = !!v; else el.value = v ?? "";
    });
    document.querySelectorAll("[data-show=sc8]").forEach((el) => el.classList.toggle("hidden", draft.file_format !== "sc8"));
    $("serials").innerHTML = devices.map((d) => `<option value="${esc(d.serial)}">${esc(d.name || d.model)}</option>`).join("");
    budget();
  }

  function radioEl(r, ri) {
    const el = $("radioTpl").content.firstElementChild.cloneNode(true);
    el.classList.toggle("off", !r.enabled);
    el.querySelectorAll("[data-r]").forEach((input) => {
      const k = input.dataset.r;
      if (input.type === "checkbox") input.checked = r[k]; else input.value = r[k];
      input.addEventListener(input.tagName === "SELECT" || input.type === "checkbox" ? "change" : "input", () => {
        let v = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value;
        if (k === "model" && v !== r.model) {
          r.model = v;
          // A B200 has only RF A: keep whichever channel was in use (e.g. SURGE's RF B) and switch it on.
          if (v === "b200") r.channels = [{ ...(r.channels.find((c) => c.enabled) || r.channels[0]), enabled: true }];
          else r.channels = [r.channels[0], { index: 1, enabled: false, label: "RF B antenna", port: "RX2", gain_db: r.channels[0].gain_db }];
          r.channels.forEach((c, i) => (c.index = i));
          return changed(true);
        }
        r[k] = v;
        changed(k === "enabled" || k === "frequency_mhz");
      });
    });
    el.querySelector(".remove").addEventListener("click", () => {
      if (draft.radios.length === 1) return toast("A setup needs at least one radio. Switch it off instead.", true);
      draft.radios.splice(ri, 1); changed(true);
    });
    const bands = el.querySelector(".bands");
    const match = BANDS.find((b) => Math.abs(b.mhz - r.frequency_mhz) < 0.005);
    bands.innerHTML = BANDS.map((b) =>
      `<button type="button" aria-pressed="${b === match}" data-mhz="${b.mhz}"><b>${b.name}</b><small>${b.mhz.toFixed(2)}</small></button>`
    ).join("") + `<button type="button" aria-pressed="${!match}" data-mhz="custom"><b>Other</b><small>type below</small></button>`;
    bands.addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.dataset.mhz === "custom") { el.querySelector('[data-r="frequency_mhz"]').focus(); return; }
      r.frequency_mhz = Number(b.dataset.mhz); changed(true);
    });
    const profile = el.querySelector(".profile");
    const same = (a, b) => Math.abs(a - b) < 0.0005;
    const current = PROFILES.find((p) => same(p.mhz, r.frequency_mhz) && same(p.msps, r.sample_rate_msps) && same(p.bw, r.bandwidth_mhz));
    profile.innerHTML = PROFILES.map((p, i) =>
      `<option value="${i}"${p === current ? " selected" : ""}>${p.name} (${p.msps} MS/s, ${p.bw} MHz)</option>`
    ).join("") + `<option value="custom"${current ? "" : " selected"}>Custom</option>`;
    profile.addEventListener("change", () => {
      const p = PROFILES[Number(profile.value)]; if (!p) return;
      r.frequency_mhz = p.mhz; r.sample_rate_msps = p.msps; r.bandwidth_mhz = p.bw; changed(true);
    });
    const chans = el.querySelector(".chans");
    r.channels.forEach((c) => chans.appendChild(chanEl(r, c)));
    return el;
  }

  function chanEl(r, c) {
    const el = $("chanTpl").content.firstElementChild.cloneNode(true);
    el.classList.toggle("off", !c.enabled);
    el.querySelector(".port-name").textContent = "RF " + "AB"[c.index];
    el.querySelector(".port-name").title = r.model === "b210" ? `B210 channel ${"AB"[c.index]} (subdev A:${"AB"[c.index]})` : "B200 RF A";
    el.querySelectorAll("[data-c]").forEach((input) => {
      const k = input.dataset.c;
      if (input.type === "checkbox") input.checked = c[k]; else input.value = c[k];
      input.addEventListener(input.type === "checkbox" || input.tagName === "SELECT" ? "change" : "input", () => {
        const v = input.type === "checkbox" ? input.checked : (input.type === "number" || input.type === "range") ? Number(input.value) : input.value;
        c[k] = v;
        if (k === "gain_db") el.querySelectorAll('[data-c="gain_db"]').forEach((o) => o !== input && (o.value = v));
        changed(k === "enabled");
      });
    });
    return el;
  }

  function changed(rerender) {
    dirty = true;
    if (rerender) render(); else budget();
  }

  function budget() {
    const on = draft.radios.filter((r) => r.enabled);
    const msps = on.reduce((s, r) => s + r.sample_rate_msps * r.channels.filter((c) => c.enabled).length, 0);
    const diskMb = msps * (draft.file_format === "sc8" ? 2 : 4);
    const gbMin = diskMb * 0.06;
    $("bDisk").textContent = diskMb.toFixed(diskMb < 100 ? 1 : 0);
    $("bMin").textContent = gbMin.toFixed(1);
    $("bUsb").textContent = (msps * 4).toFixed(0);
    const free = disk?.free_gb;
    $("bFits").textContent = free == null || !gbMin ? "—" : fmtMinutes((free - draft.storage.reserve_gb) / gbMin);
    const warn = [];
    if (msps * 4 > 300) warn.push("Several radios share the NUC's USB bus. Run a 15-minute soak test and check the dropped-sample count.");
    if (diskMb > 150) warn.push("High write rate. Check the SSD's sustained speed with the dd test in the README.");
    $("budgetWarn").textContent = warn.join(" ");
    $("budgetWarn").classList.toggle("hidden", !warn.length);
    renderApply();
    renderWhere();
  }

  function renderApply() {
    const same = active && draft && JSON.stringify(strip(active)) === JSON.stringify(strip(draft));
    const btn = $("applySetup");
    btn.disabled = locked || (same && status?.state !== "FAULT");
    btn.textContent = same ? "Loaded on payload" : "Load onto payload";
    $("applyNote").textContent = locked ? "Locked while the payload is armed or recording."
      : same ? "The payload is running exactly this setup."
      : active ? `The payload is running “${active.name}”. Loading restarts the radios.` : "Nothing is loaded yet.";
  }
  const strip = (c) => { const x = clone(c); delete x.builtin; return x; };

  // wire top-level fields
  document.querySelectorAll("[data-k]").forEach((el) => {
    el.addEventListener(el.tagName === "SELECT" || el.type === "checkbox" ? "change" : "input", () => {
      let v = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
      if (el.dataset.k === "sc8_peak") v = Number(v);
      if (el.dataset.k === "device_args") v = v.trim();
      set(draft, el.dataset.k, v);
      changed(el.dataset.k === "file_format");
    });
  });
  $("setupName").addEventListener("input", (e) => { draft.name = e.target.value; dirty = true; renderApply(); });
  $("setupSelect").addEventListener("change", (e) => {
    const s = setups.find((x) => x.name === e.target.value);
    if (!s) return;
    if (dirty && !confirm("Discard unsaved changes to this setup?")) { render(); return; }
    draft = strip(s); dirty = false; render();
  });
  $("saveSetup").addEventListener("click", async () => {
    try {
      const r = await api("/api/setups/save", { config: draft });
      setups = r.setups; draft = strip(r.config); dirty = false; render(); toast("Saved “" + draft.name + "”");
    } catch (e) { toast(e.message, true); }
  });
  $("deleteSetup").addEventListener("click", async () => {
    if (!confirm(`Delete the saved setup “${draft.name}”?`)) return;
    try { setups = (await api("/api/setups/delete", { name: draft.name })).setups; draft = strip(setups[0]); dirty = false; render(); }
    catch (e) { toast(e.message, true); }
  });
  function addRadio(model) {
    const n = draft.radios.length + 1;
    const ch = (i, label) => ({ index: i, enabled: true, label, port: "RX2", gain_db: 45 });
    draft.radios.push({
      id: model + "_" + n + "_" + Date.now().toString(36).slice(-3), name: model.toUpperCase() + " " + n, model, serial: "",
      enabled: true, frequency_mhz: 1176.45, sample_rate_msps: 22, bandwidth_mhz: 15,
      channels: model === "b210" ? [ch(0, `Radio ${n} RF A`), ch(1, `Radio ${n} RF B`)] : [ch(0, `Radio ${n} RF A`)],
    });
    changed(true);
  }
  $("addB210").addEventListener("click", () => addRadio("b210"));
  $("addB200").addEventListener("click", () => addRadio("b200"));
  $("findRadios").addEventListener("click", async () => {
    $("foundNote").textContent = "Looking on USB…";
    try {
      // UHD reports B200, B210, B200mini, B205mini. Minis are one-channel like the B200.
      devices = (await api("/api/discover", {})).devices.map((d) => ({ ...d, kind: d.model.startsWith("b210") ? "b210" : "b200" }));
      // Fill empty serials with unused radios of the same kind.
      const used = new Set(draft.radios.map((r) => r.serial).filter(Boolean));
      draft.radios.forEach((r) => {
        if (r.serial) return;
        const d = devices.find((x) => x.kind === r.model && !used.has(x.serial));
        if (d) { r.serial = d.serial; used.add(d.serial); dirty = true; }
      });
      const spare = devices.filter((d) => !used.has(d.serial));
      $("foundNote").innerHTML = !devices.length ? "No radios found. Check the USB 3 cable and power."
        : "Found " + devices.map((d) => `${esc(d.model.toUpperCase())} ${esc(d.serial)}`).join(", ") + "."
          + (spare.length ? ` <button type="button" class="btn quiet" id="addSpare">Add ${spare.length === 1 ? "it" : "them"} to this setup</button>` : "");
      $("addSpare")?.addEventListener("click", () => {
        spare.forEach((d) => { addRadio(d.kind); draft.radios[draft.radios.length - 1].serial = d.serial; });
        $("foundNote").textContent = "Added. Set each radio's band and antenna names.";
        changed(true);
      });
      render();
    } catch (e) { $("foundNote").textContent = ""; toast(e.message, true); }
  });
  $("applySetup").addEventListener("click", async () => {
    const btn = $("applySetup"); btn.disabled = true; btn.textContent = "Starting radios…";
    try {
      await api("/api/apply", { config: draft });
      toast("Loaded “" + draft.name + "” onto the payload");
      dirty = false;
      await refresh();
      if (active) { draft = strip(active); render(); }   // show exactly what the payload accepted
    } catch (e) { toast(e.message, true); renderApply(); }
  });

  // ---------------- signal check ----------------
  $("runTest").addEventListener("click", async () => {
    try { await api("/api/test", { seconds: Number($("testSeconds").value) }); await refresh(); }
    catch (e) { toast(e.message, true); }
  });
  $("stopTest").addEventListener("click", async () => {
    $("stopTest").disabled = true;
    try { await api("/api/test/stop", {}); await refresh(); }
    catch (e) { toast(e.message, true); }
    finally { $("stopTest").disabled = false; }
  });
  const clock = (s) => { s = Math.max(0, Math.round(s)); return s < 60 ? s + " s" : Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };

  function renderTest() {
    const box = $("testResult");
    const running = !!test?.running;
    $("runTest").disabled = running || locked;
    $("runTest").classList.toggle("hidden", running);
    $("stopTest").classList.toggle("hidden", !running);
    $("testSeconds").disabled = running;
    if (running) {
      const dropped = (status?.radios || []).reduce((n, r) => n + (r.overflows || 0) + (r.gaps || 0), 0);
      const rate = status?.rate_mbps ? ` · ${status.rate_mbps.toFixed(0)} MB/s` : "";
      const drop = `<span class="${dropped ? "bad" : ""}">dropped ${dropped}</span>`;
      if (test.remaining_s == null) {
        box.innerHTML = `<p class="note">Recording ${clock(test.elapsed_s)}${rate} · ${drop}. Press Stop when done.</p><div class="progress live"><i></i></div>`;
      } else {
        const pct = Math.min(100, 100 * test.elapsed_s / Math.max(1, test.seconds));
        box.innerHTML = `<p class="note">Recording ${clock(test.elapsed_s)} of ${clock(test.seconds)}${rate} · ${drop}</p><div class="progress"><i style="width:${pct}%"></i></div>`;
      }
      return;
    }
    if (test?.error) { box.innerHTML = `<p class="warn">${esc(test.error)}</p>`; return; }
    if (!test?.result) { box.innerHTML = ""; return; }
    box.innerHTML = test.result.map((c, i) => {
      const cls = { good: "good", low: "good", high: "quiet", clipping: "clipping" }[c.verdict] || "quiet";
      const say = { good: "Good level", low: "Usable, a little quiet", high: "Usable, a little hot", clipping: "Clipping", "too quiet": "Too quiet" }[c.verdict];
      const advice = c.gain_change_db && c.suggested_gain_db != null
        ? `<span>Try ${c.suggested_gain_db} dB</span><button type="button" class="btn quiet" data-apply="${i}">Use ${c.suggested_gain_db} dB</button>`
        : `<span>RMS ${c.rms} of 127, ${c.clip_pct}% at full scale</span>`;
      return `<div class="hist"><div class="hist-head"><b>${esc(c.label)}</b><span class="verdict ${cls}">${say}</span></div>
        ${histSvg(c)}
        <div class="axis"><span>−128</span><span>0</span><span>+127</span></div>
        <div class="hist-foot">${advice}</div></div>`;
    }).join("");
    box.querySelectorAll("[data-apply]").forEach((b) => b.addEventListener("click", () => {
      const c = test.result[Number(b.dataset.apply)];
      const radio = draft.radios.find((r) => r.id === c.radio);
      const ch = radio?.channels.find((x) => x.label === c.label);
      if (!ch) return toast("That channel is not in the setup being edited.", true);
      ch.gain_db = c.suggested_gain_db; changed(true);
      toast(`Gain for ${c.label} set to ${c.suggested_gain_db} dB. Load onto payload, then test again.`);
    }));
  }

  function histSvg(c) {
    const w = 320, h = 86, n = c.histogram.length, max = Math.max(...c.histogram, 1);
    const bw = w / n;
    const bars = c.histogram.map((v, i) => {
      const bh = Math.max(v ? 1.5 : 0, (h - 6) * v / max);
      const edge = i === 0 || i === n - 1;
      return `<rect x="${(i * bw + 1).toFixed(1)}" y="${(h - bh).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${bh.toFixed(1)}" fill="${edge && v ? "#b3372e" : "#0b7068"}"/>`;
    }).join("");
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Sample histogram for ${esc(c.label)}">
      <rect x="0" y="0" width="${bw}" height="${h}" fill="#f8e1dd"/><rect x="${w - bw}" y="0" width="${bw}" height="${h}" fill="#f8e1dd"/>
      <line x1="${w / 2}" x2="${w / 2}" y1="0" y2="${h}" stroke="#d2dbd6" stroke-dasharray="3 3"/>${bars}</svg>`;
  }

  // ---------------- status ----------------
  function renderStatus(s) {
    status = s.status; active = s.active; disk = s.disk; locked = s.locked; test = s.test; ssdDir = s.ssd_dir || ssdDir;
    $("host").textContent = s.host + (s.version ? " · v" + s.version : "") + (status.simulated ? " (simulated radios)" : "");
    const [text, tone] = STATE_TEXT[status.state] || [status.state, "warn"];
    $("vState").textContent = status.state === "RECORDING" ? `${text} ${Math.floor(status.elapsed_s)} s` : text;
    $("vState").dataset.tone = tone;
    $("vSetup").textContent = active?.name || "none";
    const cube = status.cube || {};
    $("vCube").textContent = cube.connected ? `${cube.mode || "linked"}${cube.armed ? ", armed" : ""}` : "not linked";
    $("vCube").dataset.tone = cube.connected ? "good" : "warn";
    $("vDisk").textContent = disk ? `${disk.drive === "NUC SSD" ? "NUC SSD" : "Data drive"} · ${fmtFree(disk.free_gb)} free` : "—";
    $("vDisk").dataset.tone = disk && active && disk.free_gb <= active.storage.reserve_gb ? "bad" : disk?.fallback ? "warn" : "";
    renderWhere();
    const banner = $("lockBanner");
    if (status.state === "FAULT") {
      banner.textContent = "Payload fault: " + (status.fault || "unknown") + ". Fix the cause, then load the setup again.";
      banner.className = "banner bad";
    } else if (locked) {
      banner.textContent = status.state === "RECORDING"
        ? "Recording in progress. Settings are locked until it stops (Stop from the ground station or below)."
        : "Armed for auto-record from the ground station. Settings are locked until it is cancelled or finishes.";
      banner.className = "banner";
    } else banner.className = "banner hidden";
    $("eventList").innerHTML = (status.events || []).slice(-6).reverse().map((e) =>
      `<li><b>${esc(e.text)}</b><span>${new Date(e.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></li>`
    ).join("") || `<li class="empty">Nothing yet.</li>`;
    renderTest();
    if (draft) budget();
    window.HERON.state = s;
    window.dispatchEvent(new CustomEvent("heron-state", { detail: s }));
  }

  // ---------------- where recordings go ----------------
  const DATA_DRIVE_PATH = "/media/DataStore/heron";
  const fmtFree = (gb) => gb >= 1000 ? (gb / 1000).toFixed(1) + " TB" : Math.round(gb) + " GB";
  function renderWhere() {
    const note = $("whereNote");
    if (!note || !draft) return;
    const path = draft.storage.path;
    $("useDataDrive").disabled = locked || path === DATA_DRIVE_PATH;
    $("useSsd").disabled = locked || !ssdDir || path === ssdDir;
    if (!disk) { note.textContent = ""; return; }
    const loaded = active && active.storage.path === path;
    if (!loaded) {
      note.textContent = "Load onto payload to switch. Under /media/… the payload checks that drive is plugged in; if it is not, it records to the NUC SSD instead.";
    } else if (disk.fallback) {
      note.innerHTML = `<b>No data drive is mounted</b>, so recordings go to the NUC SSD (<code>${esc(disk.path)}</code>), keeping ${active.storage.reserve_gb} GB free.`;
    } else {
      note.innerHTML = `Recording to the ${disk.drive === "NUC SSD" ? "NUC SSD" : "data drive"}: <code>${esc(disk.path)}</code>, ${fmtFree(disk.free_gb)} free of ${fmtFree(disk.total_gb)}.`;
    }
  }
  $("useDataDrive").addEventListener("click", () => { draft.storage.path = DATA_DRIVE_PATH; changed(true); });
  $("useSsd").addEventListener("click", () => { if (ssdDir) { draft.storage.path = ssdDir; changed(true); } });

  async function refresh() {
    try { renderStatus(await api("/api/state")); }
    catch (e) { $("vState").textContent = "no connection"; $("vState").dataset.tone = "bad"; }
  }

  function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]); }
  // editDraft lets other panels (sweeps.js) change the setup being edited, the same way the form does.
  window.HERON = { api, toast, esc, fmtGB, histSvg, state: null, editDraft: (fn) => { fn(draft); changed(true); } };

  async function boot() {
    const [st, su] = await Promise.all([api("/api/state"), api("/api/setups")]);
    setups = su.setups;
    renderStatus(st);
    draft = strip(st.active || setups[0]);
    render();
    polling = setInterval(refresh, 1000);
  }
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
  boot().catch((e) => toast("Cannot reach the payload service: " + e.message, true));
})();
