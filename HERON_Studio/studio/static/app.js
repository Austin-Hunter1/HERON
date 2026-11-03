// HERON Studio front end. Plain JS modules, no build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const desktop = window.heron || null;                 // Electron preload bridge (pick files, real paths)
if (desktop?.platform === "darwin") document.body.classList.add("electron-mac");

const api = {
  async req(method, url, body) {
    const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    const t = await r.text(); let j; try { j = JSON.parse(t); } catch { j = t; }
    if (!r.ok) throw new Error(j?.error || `${r.status} ${r.statusText}`);
    return j;
  },
  get: (u) => api.req("GET", u), post: (u, b) => api.req("POST", u, b ?? {}), patch: (u, b) => api.req("PATCH", u, b), del: (u) => api.req("DELETE", u),
};

const S = { app: null, flight: null, timer: null, map: null, three: null, uploads: new Map(), tab: null };

function toast(msg, err = false) {
  const t = document.createElement("div"); t.className = "toast" + (err ? " err" : ""); t.textContent = msg;
  $("#toasts").append(t); setTimeout(() => t.remove(), err ? 7000 : 3500);
}
const fmtBytes = (b) => b == null ? "" : b > 1e9 ? (b / 1e9).toFixed(2) + " GB" : b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : b > 1e3 ? (b / 1e3).toFixed(0) + " KB" : b + " B";
const fmtDur = (s) => s == null ? "" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const pct = (a, b) => b ? (100 * a / b) : 0;
const fmtArea = (m2) => m2 == null ? "—" : m2 > 1e4 ? `${(m2 / 1e4).toFixed(1)} ha` : `${Math.round(m2).toLocaleString()} m²`;

// ------------------------------------------------------------------ routing
window.addEventListener("hashchange", route);
async function route() {
  clearInterval(S.timer); S.timer = null; teardown();
  const [, a, fid, tab] = location.hash.split("/");
  if (a === "f" && fid) return flightView(fid, tab || "import");
  return library();
}
function teardown() {
  if (S.map) { S.map.remove(); S.map = null; }
  if (S.three) { S.three.dispose(); S.three = null; }
}

// ------------------------------------------------------------------ library
async function library() {
  $("#tabs").hidden = true; $("#barRight").innerHTML = `<span class="chip hide-sm">${esc(S.app?.home || "")}</span>`;
  const flights = await api.get("/api/flights");
  $("#view").innerHTML = `
  <div class="page">
    <div class="row" style="justify-content:space-between">
      <div><p class="eyebrow info">After the flight</p><h1>Flights</h1></div>
      <button class="btn primary" id="newBtn">New flight</button>
    </div>
    <div class="cards">
      <button class="fcard newcard" id="newCard"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 5v14M5 12h14"/></svg>
        <b>New flight</b><small>Drop the GoPro video and flight log</small></button>
      ${flights.map(card).join("")}
    </div>
    ${S.app && !S.app.colmap ? `<div class="callout warn">COLMAP (pycolmap) isn't installed, so new flights can't be processed yet. <span class="mono">pip install pycolmap</span></div>` : ""}
  </div>`;
  $("#newBtn").onclick = $("#newCard").onclick = () => newFlight();
}
function card(f) {
  const st = { done: ["ok", "Processed"], processing: ["run", `Processing ${f.progress ?? 0}%`], error: ["err", "Needs attention"], ready: ["", "Ready to process"], new: ["warn", "Missing files"] }[f.status] || ["", f.status];
  return `<a class="fcard" href="#/f/${f.id}/${f.status === "done" ? "video" : f.status === "processing" ? "processing" : "import"}">
    <div class="thumb" style="${f.thumb ? `background-image:url('${f.thumb}')` : ""}">${f.sample ? `<span class="chip">Example</span>` : ""}</div>
    <div class="body"><b>${esc(f.name)}</b>
      <span class="muted">${esc([f.site, f.date].filter(Boolean).join(" · ") || "—")}</span>
      <div class="row" style="margin-top:4px"><span class="chip ${st[0]}">${st[1]}</span>${f.video ? `<span class="chip">${f.video} · ${fmtDur(f.duration)}</span>` : ""}
      ${f.water_area_m2 ? `<span class="chip">water ${fmtArea(f.water_area_m2)}</span>` : ""}</div></div></a>`;
}
async function newFlight(files) {
  const d = new Date().toISOString().slice(0, 10);
  const f = await api.post("/api/flights", { name: `Flight ${d}`, date: d });
  location.hash = `#/f/${f.id}/import`;
  if (files?.length) setTimeout(() => addFiles(files), 300);
}

// ------------------------------------------------------------------ flight shell
const TABS = [["import", "Import"], ["processing", "Processing"], ["video", "Video"], ["water", "Water map"], ["3d", "3D model"]];
async function flightView(fid, tab) {
  let f; try { f = await api.get(`/api/flights/${fid}`); } catch (e) { toast(e.message, true); location.hash = "#/"; return; }
  S.flight = f; S.tab = tab;
  const done = f.status === "done" || (f.job?.done || []).includes("exports");
  const nav = $("#tabs"); nav.hidden = false;
  nav.innerHTML = TABS.map(([k, l]) => {
    const dis = (["video", "water", "3d"].includes(k) && !done && !f.sample) || (k === "import" && f.sample);
    return `<a href="#/f/${fid}/${k}" ${k === tab ? 'aria-current="page"' : ""} ${dis ? 'aria-disabled="true"' : ""}>${l}${k === "processing" && f.status === "processing" ? '<i class="dot"></i>' : ""}</a>`;
  }).join("");
  $("#barRight").innerHTML = `<span class="muted hide-sm">${esc(f.name)}</span><a class="btn sm" href="#/">All flights</a>`;
  ({ import: importView, processing: processingView, video: videoView, water: waterView, "3d": modelView }[tab] || importView)(f);
}
async function reload() { S.flight = await api.get(`/api/flights/${S.flight.id}`); return S.flight; }

// ------------------------------------------------------------------ import
const KIND = { video: ["VID", "Video"], log: ["LOG", "Flight log (.bin)"], log_folder: ["LOG", "Flight log CSV folder"], sensor_folder: ["IMU", "Camera gyro + accel folder"],
  mission: ["WPT", "Mission plan"], outline: ["MAP", "Lake outline"], tlog: ["TLG", "Telemetry log (.tlog, not used)"], unknown: ["?", "Not recognised"],
  "csv:GYRO": ["IMU", "Camera gyro CSV"], "csv:ACCL": ["IMU", "Camera accelerometer CSV"] };
function importView(f) {
  const v = f.video, st = f.settings || {}, ck = f.checklist || {};
  const ro = f.sizes?.render_options || {};
  const missing = Object.entries({ video: "GoPro video", log: "flight log", camera_gyro: "camera gyro (original GoPro MP4 or GYRO/ACCL folder)", outline: "lake outline (GeoJSON/KML)" })
    .filter(([k]) => !ck[k]).map(([, l]) => l);
  $("#view").innerHTML = `
  <div class="split">
    <aside class="aside">
      <div><p class="eyebrow info">Review a flight</p>
        <input class="input" id="fName" value="${esc(f.name)}" aria-label="Flight name" style="font-size:20px;font-weight:650;margin-top:4px">
        <div class="grid2" style="margin-top:8px">
          <label class="field"><span>Site</span><input class="input" id="fSite" value="${esc(f.site || "")}" placeholder="e.g. Boulder Reservoir"></label>
          <label class="field"><span>Date</span><input class="input" id="fDate" type="date" value="${esc(f.date || "")}"></label>
        </div></div>
      <section aria-label="Files" class="files" id="files">${fileRows(f)}</section>
      <div class="dropzone" id="dz">
        <span>Drop the GoPro video, the flight <b>.bin</b>, the mission and a lake outline</span>
        <div class="row" style="justify-content:center">
          <button class="btn sm" id="pick">Choose files…</button>
          ${desktop ? `<button class="btn sm" id="pickFolder">Add a CSV folder…</button>` : ""}
        </div>
        ${desktop ? `<label class="row faint" style="font-size:12px"><input type="checkbox" id="copyIn"> Copy into the Studio folder (safe to unplug the SD card)</label>` : `<small class="faint">Large videos upload in 8 MB pieces and resume if interrupted.</small>`}
      </div>
      ${v ? `<section class="callout" aria-label="Found automatically">
        <p class="eyebrow info" style="margin-bottom:6px">Found automatically</p>
        <div class="kv"><span>Video</span><span class="mono">${v.width}×${v.height} · ${(+v.fps).toFixed(3).replace(/\.?0+$/, "")} fps · ${v.resolution_label} ${v.aspect}</span></div>
        <div class="kv"><span>Length · codec</span><span class="mono">${fmtDur(v.duration_s)} · ${esc(v.codec)}${v.bitrate_mbps ? ` · ${v.bitrate_mbps} Mb/s` : ""}</span></div>
        ${v.parts?.length > 1 ? `<div class="kv"><span>Recording</span><span>${v.parts.length} chapters joined · ${v.parts.map(p => fmtDur(p.duration_s)).join(" + ")}</span></div>` : ""}
        ${(f.files || []).filter(x => x.kind === "video").length > 1 ? `<label class="row" style="min-height:30px;font-size:13px"><input type="checkbox" id="joinCh" ${st.join_chapters !== false ? "checked" : ""}> Join GoPro chapters into one recording</label>` : ""}
        ${f.video_unused?.length ? `<small class="muted" style="display:block">Not used: ${f.video_unused.map(esc).join(", ")}</small>` : ""}
        <div class="kv"><span>Camera</span><span>${esc(v.camera_model || f.telemetry_info?.model || "unknown")}</span></div>
        <div class="kv"><span>Gyro track</span><span>${v.telemetry_stream != null ? "in the video" : ck.camera_gyro ? "from folder" : '<span style="color:var(--warn)">missing</span>'}</span></div>
        <label class="field" style="margin-top:6px"><span>Lens profile (${v.aspect} only)</span>
          <select class="input" id="lens">${(f.lens_options || []).map(o => `<option value="${esc(o.id)}" ${o.id === f.lens?.id ? "selected" : ""}>${esc(o.name)}</option>`).join("") || `<option>${esc(f.lens?.name || "none")}</option>`}
          <option value="__search">Search all profiles…</option></select></label>
        ${(f.lens?.notes || []).map(n => `<small class="muted" style="display:block;margin-top:4px">${esc(n)}</small>`).join("")}
        <div class="kv" style="margin-top:6px"><span>Works at</span><span class="mono">solve ${f.sizes.solve.width}×${f.sizes.solve.height} · water ${f.sizes.water.width}×${f.sizes.water.height}</span></div>
      </section>` : ""}
      <section aria-label="Camera solve"><p class="eyebrow" style="margin-bottom:8px">Camera solve</p>
        <div class="grid2">
          <label class="choice"><span class="row"><input type="radio" name="solve" value="quick" ${st.solve === "quick" ? "checked" : ""}><b>Quick</b></span><small>COLMAP every 12th frame, gyro in between</small></label>
          <label class="choice"><span class="row"><input type="radio" name="solve" value="full" ${st.solve !== "quick" ? "checked" : ""}><b>Full</b></span><small>Every frame solved. Slower, steadiest overlay</small></label>
        </div></section>
      <section aria-label="Make"><p class="eyebrow" style="margin-bottom:6px">Make</p>
        ${[["ar_video", "AR video with reflections"], ["water", "Water map + shoreline"], ["model3d", "3D model of the shore"], ["report", "Lake report + exports"]]
          .map(([k, l]) => `<label class="row" style="min-height:30px"><input type="checkbox" data-make="${k}" ${st.make?.[k] !== false ? "checked" : ""}> ${l}</label>`).join("")}
        <label class="field" style="margin-top:6px"><span>AR video size</span><select class="input" id="rsize">
          ${Object.entries(ro).map(([k, s]) => `<option value="${k}" ${st.render_size === k ? "selected" : ""}>${k === "source" ? "Same as video" : k} · ${s.width}×${s.height}</option>`).join("") || "<option>add a video first</option>"}</select></label>
      </section>
      <details><summary>Advanced</summary>
        <div class="grid2" style="margin-top:10px">
          <label class="field"><span>Clip starts at (s) in the original recording</span><input class="input mono" id="cstart" type="number" step="0.01" value="${st.clip_start_in_recording_s ?? 0}"></label>
          <label class="field"><span>Water surface elevation (m, GPS)</span><input class="input mono" id="welev" type="number" step="0.1" placeholder="from takeoff" value="${st.water_elevation_m ?? ""}"></label>
        </div>
        <small class="faint">Clip start matters only when the gyro comes from a different file than the video (an edited clip). Leave 0 for original GoPro files, joined chapters included.</small>
      </details>
      ${missing.length ? `<div class="callout warn"><b>Still needed</b><br><span class="muted">${missing.map(esc).join(" · ")}</span></div>` : ""}
      <button class="btn primary big" id="go" ${f.ready && S.app?.colmap ? "" : "disabled"}>${f.job?.state === "error" || f.job?.state === "interrupted" || f.job?.state === "cancelled" ? "Resume processing" : "Process flight"}</button>
    </aside>
    <section class="main" aria-label="Preview">
      <div class="preview">
        <div>${v ? `<video controls preload="metadata" poster="/api/flights/${f.id}/media/thumb.jpg" src="/api/flights/${f.id}/media/video.mp4"></video>`
          : `<div class="card empty">Add a video to preview it here</div>`}</div>
        <div class="map" id="map"></div>
      </div>
    </section>
  </div>`;
  const save = async (body) => { try { await api.patch(`/api/flights/${f.id}`, body); } catch (e) { toast(e.message, true); } };
  $("#fName").onchange = (e) => save({ name: e.target.value });
  $("#fSite").onchange = (e) => save({ site: e.target.value });
  $("#fDate").onchange = (e) => save({ date: e.target.value });
  $$('input[name=solve]').forEach(r => r.onchange = () => save({ settings: { solve: r.value } }));
  $$("[data-make]").forEach(c => c.onchange = () => save({ settings: { make: { [c.dataset.make]: c.checked } } }));
  $("#rsize")?.addEventListener("change", (e) => save({ settings: { render_size: e.target.value } }));
  $("#joinCh")?.addEventListener("change", async (e) => { await save({ settings: { join_chapters: e.target.checked } }); flightView(f.id, "import"); });
  $("#cstart").onchange = (e) => save({ settings: { clip_start_in_recording_s: +e.target.value } });
  $("#welev").onchange = (e) => save({ settings: { water_elevation_m: e.target.value ? +e.target.value : null } });
  $("#lens")?.addEventListener("change", async (e) => {
    let id = e.target.value;
    if (id === "__search") return lensSearch(f);
    await api.post(`/api/flights/${f.id}/lens`, { id }); flightView(f.id, "import");
  });
  $("#go").onclick = async () => {
    try {
      const resume = ["error", "interrupted", "cancelled"].includes(f.job?.state);
      await api.post(`/api/flights/${f.id}/process`, resume ? { start_at: "resume" } : {}); location.hash = `#/f/${f.id}/processing`;
    } catch (e) { toast(e.message, true); }
  };
  $("#pick").onclick = async () => {
    if (desktop) { const ps = await desktop.pickFiles(); if (ps?.length) linkPaths(ps); }
    else $("#filePick").click();
  };
  $("#pickFolder")?.addEventListener("click", async () => { const p = await desktop.pickFolder(); if (p) linkPaths([p]); });
  $$("#files [data-rm]").forEach(b => b.onclick = async () => { await api.del(`/api/flights/${f.id}/files/${encodeURIComponent(b.dataset.rm)}`); flightView(f.id, "import"); });
  wireDropzone($("#dz"));
  previewMap(f);
  renderUploads();
}
function fileRows(f) {
  const rows = (f.files || []).map(x => {
    const k = KIND[x.kind] || KIND.unknown, i = x.info || {};
    const detail = x.error ? `<span style="color:var(--bad)">${esc(x.error)}</span>`
      : x.kind === "video" ? `${i.chapter ? `chapter ${i.chapter.n || 1} · ` : ""}${i.width}×${i.height} · ${(+i.fps).toFixed(2)} fps · ${fmtDur(i.duration_s)} · ${fmtBytes(x.size)}`
      : x.kind === "log" ? `${esc(i.utc_start || "")} · ${fmtBytes(x.size)}`
      : x.kind === "mission" ? `${i.waypoints ?? i.items} waypoints` : `${k[1]} ${fmtBytes(x.size)}`;
    return `<div class="file"><span class="ic ${x.error ? "err" : x.kind === "unknown" ? "" : "ok"}">${k[0]}</span>
      <div><b title="${esc(x.linked ? x.path : x.name)}">${esc(x.name)}</b><small>${k[1]} · ${detail}${x.linked ? " · used in place" : ""}</small></div>
      <button class="btn sm" data-rm="${esc(x.name)}" aria-label="Remove ${esc(x.name)}">✕</button></div>`;
  });
  return rows.join("") + `<div id="upRows"></div>`;
}
async function lensSearch(f) {
  const box = document.createElement("div"); box.className = "card";
  box.innerHTML = `<label class="field"><span>Search lens profiles (${f.video.aspect})</span><input class="input" id="lq" placeholder="hero10 linear 2.7k"></label><div id="lr" style="max-height:260px;overflow:auto;margin-top:8px"></div>`;
  $("#lens").closest("section").append(box);
  const go = async () => {
    const r = await api.get(`/api/lenses?aspect=${encodeURIComponent(f.video.aspect)}&q=${encodeURIComponent($("#lq").value)}`);
    $("#lr").innerHTML = r.map(p => `<button class="btn sm" style="width:100%;justify-content:flex-start;margin-bottom:4px" data-id="${esc(p.id)}">${esc(p.name)}</button>`).join("") || `<small class="muted">No match</small>`;
    $$("#lr [data-id]").forEach(b => b.onclick = async () => { await api.post(`/api/flights/${f.id}/lens`, { id: b.dataset.id }); flightView(f.id, "import"); });
  };
  $("#lq").oninput = go; go(); $("#lq").focus();
}

// ------------------------------------------------------------------ uploads (chunked, resumable)
function wireDropzone(dz) {
  ["dragenter", "dragover"].forEach(e => dz.addEventListener(e, (ev) => { ev.preventDefault(); dz.classList.add("hot"); }));
  ["dragleave", "drop"].forEach(e => dz.addEventListener(e, () => dz.classList.remove("hot")));
}
let dragDepth = 0;
window.addEventListener("dragenter", (e) => { if (e.dataTransfer?.types?.includes("Files")) { dragDepth++; $("#dropVeil").hidden = false; } });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#dropVeil").hidden = true; } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault(); dragDepth = 0; $("#dropVeil").hidden = true;
  const files = [...(e.dataTransfer?.files || [])]; if (!files.length) return;
  if (S.flight && S.tab === "import" && location.hash.startsWith("#/f/")) addFiles(files); else newFlight(files);
});
$("#filePick").addEventListener("change", (e) => { addFiles([...e.target.files]); e.target.value = ""; });

async function addFiles(files) {
  const fid = S.flight?.id; if (!fid) return;
  if (desktop?.pathForFile) {                      // desktop: use files where they are (or copy) – no upload
    const paths = files.map(f => desktop.pathForFile(f)).filter(Boolean);
    if (paths.length === files.length) return linkPaths(paths);
  }
  for (const file of files) queueUpload(fid, file);
}
async function linkPaths(paths) {
  const fid = S.flight.id, copy = $("#copyIn")?.checked;
  for (const p of paths) {
    try { const r = await api.post(`/api/flights/${fid}/link`, { path: p, copy }); if (r.file.error) toast(`${r.file.name}: ${r.file.error}`, true); }
    catch (e) { toast(e.message, true); }
  }
  flightView(fid, "import");
}
function queueUpload(fid, file) {
  const key = fid + "/" + file.name + "/" + file.size;
  if (S.uploads.has(key)) return;
  const u = { key, fid, file, sent: 0, state: "waiting", rate: 0, paused: false, err: null };
  S.uploads.set(key, u); renderUploads(); pump();
}
let active = 0;
function pump() {
  for (const u of S.uploads.values()) {
    if (active >= 2) break;
    if (u.state === "waiting" && !u.paused) { active++; runUpload(u).finally(() => { active--; pump(); }); }
  }
}
async function runUpload(u) {
  u.state = "sending"; u.err = null; renderUploads();
  try {
    const s = await api.post("/api/uploads", { flight: u.fid, name: u.file.name, size: u.file.size });
    u.id = s.id; u.sent = s.received;
    const chunk = S.app?.chunk || 8 * 1024 * 1024; let t0 = performance.now(), b0 = u.sent, tries = 0;
    while (u.sent < u.file.size) {
      if (u.paused) { u.state = "paused"; renderUploads(); return; }
      const blob = u.file.slice(u.sent, Math.min(u.file.size, u.sent + chunk));
      try {
        const r = await fetch(`/api/uploads/${u.id}?offset=${u.sent}`, { method: "PUT", body: blob, headers: { "Content-Type": "application/octet-stream" } });
        const j = await r.json(); u.sent = j.received; tries = 0;
      } catch (e) {
        if (++tries > 5) throw e;
        await new Promise(r => setTimeout(r, 800 * tries));
        const st = await api.get(`/api/uploads/${u.id}`).catch(() => null); if (st) u.sent = st.received;
      }
      const dt = (performance.now() - t0) / 1000; if (dt > 0.5) { u.rate = (u.sent - b0) / dt; }
      renderUploads();
    }
    u.state = "checking"; renderUploads();
    const r = await api.post(`/api/uploads/${u.id}/finish`);
    S.uploads.delete(u.key);
    if (r.file.error) toast(`${r.file.name}: ${r.file.error}`, true);
    if (S.flight?.id === u.fid && S.tab === "import") flightView(u.fid, "import");
  } catch (e) { u.state = "error"; u.err = e.message; renderUploads(); }
}
function renderUploads() {
  const el = $("#upRows"); if (!el) return;
  el.innerHTML = [...S.uploads.values()].filter(u => u.fid === S.flight?.id).map(u => {
    const p = pct(u.sent, u.file.size), eta = u.rate > 0 ? (u.file.size - u.sent) / u.rate : null;
    const label = { waiting: "Waiting", sending: `${p.toFixed(0)}% · ${fmtBytes(u.rate)}/s${eta ? ` · ${fmtDur(eta)} left` : ""}`, paused: `Paused at ${p.toFixed(0)}%`, checking: "Reading file…", error: `Stopped: ${esc(u.err)}` }[u.state];
    return `<div class="file"><span class="ic">${u.state === "error" ? "!" : "↑"}</span>
      <div><b>${esc(u.file.name)}</b><small>${fmtBytes(u.file.size)} · ${label}</small><div class="bar-p"><i style="width:${p}%"></i></div></div>
      <div class="row">${u.state === "sending" ? `<button class="btn sm" data-pause="${esc(u.key)}">Pause</button>` : ["paused", "error"].includes(u.state) ? `<button class="btn sm" data-resume="${esc(u.key)}">Resume</button>` : ""}
      <button class="btn sm" data-cancel="${esc(u.key)}" aria-label="Cancel">✕</button></div></div>`;
  }).join("");
  $$("[data-pause]", el).forEach(b => b.onclick = () => { S.uploads.get(b.dataset.pause).paused = true; });
  $$("[data-resume]", el).forEach(b => b.onclick = () => { const u = S.uploads.get(b.dataset.resume); u.paused = false; u.state = "waiting"; pump(); });
  $$("[data-cancel]", el).forEach(b => b.onclick = async () => { const u = S.uploads.get(b.dataset.cancel); u.paused = true; S.uploads.delete(u.key); if (u.id) await api.del(`/api/uploads/${u.id}`).catch(() => { }); renderUploads(); });
}
window.addEventListener("beforeunload", (e) => { if ([...S.uploads.values()].some(u => u.state === "sending")) { e.preventDefault(); e.returnValue = ""; } });

// ------------------------------------------------------------------ maps
// ------------------------------------------------------------------ base maps (shared by every map)
const BASES = {
  satellite: ["Satellite", () => L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 21, maxNativeZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" })],
  hybrid: ["Satellite + labels", () => L.layerGroup([BASES.satellite[1](), L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", { maxZoom: 21, maxNativeZoom: 19 }),
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}", { maxZoom: 21, maxNativeZoom: 19, opacity: 0.7 })])],
  streets: ["Streets", () => L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 21, maxNativeZoom: 19, attribution: "© OpenStreetMap contributors" })],
  topo: ["Topographic", () => L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", { maxZoom: 21, maxNativeZoom: 19, attribution: "© Esri" })],
  dark: ["Dark", () => L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", { maxZoom: 21, maxNativeZoom: 20, attribution: "© OpenStreetMap © CARTO" })],
  g_sat: ["Google satellite", () => L.gridLayer.googleMutant({ type: "satellite", maxZoom: 21 }), "google"],
  g_hyb: ["Google hybrid", () => L.gridLayer.googleMutant({ type: "hybrid", maxZoom: 21 }), "google"],
  g_road: ["Google roads", () => L.gridLayer.googleMutant({ type: "roadmap", maxZoom: 21 }), "google"],
  g_ter: ["Google terrain", () => L.gridLayer.googleMutant({ type: "terrain", maxZoom: 21 }), "google"],
  none: ["No base map (offline)", () => L.layerGroup()],
};
const store = { get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch { } } };
let googleReady = null;
function loadGoogle(key) {                         // official Maps JavaScript API, only if the user has saved a key
  if (!key) return Promise.resolve(false);
  if (googleReady) return googleReady;
  googleReady = new Promise((res) => {
    window.__gmapsReady = () => {
      const sc = document.createElement("script"); sc.src = "/static/vendor/leaflet/Leaflet.GoogleMutant.js"; sc.onload = () => res(true); sc.onerror = () => res(false); document.head.append(sc);
    };
    const s = document.createElement("script"); s.async = true; s.onerror = () => res(false);
    s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&loading=async&callback=__gmapsReady`; document.head.append(s);
  });
  return googleReady;
}
function baseMap(el, opts = {}) {
  const map = L.map(el, { zoomControl: true, attributionControl: true, preferCanvas: true, maxZoom: 21, ...opts });
  let cur = null, curKey = null;
  const set = async (key) => {
    if (!BASES[key]) key = "satellite";
    if (BASES[key][2] === "google" && !(await loadGoogle(S.app?.google_maps_key))) { toast("Google base maps need a Maps JavaScript API key (Map settings)", true); key = "satellite"; }
    if (cur) cur.remove();
    cur = BASES[key][1](); cur.addTo(map); if (cur.bringToBack) cur.bringToBack(); curKey = key; store.set("heron.base", key);
    map.fire("basechange", { key });
  };
  set(navigator.onLine ? store.get("heron.base", "satellite") : "none");
  L.control.scale({ imperial: false, position: "bottomleft" }).addTo(map);
  return { map, setBase: set, baseKey: () => curKey };
}
async function previewMap(f) {
  const el = $("#map"); if (!el) return;
  const { map } = baseMap(el); S.map = map;
  const layers = [];
  const ol = await fetch(`/api/flights/${f.id}/geo/outline`).then(r => r.ok ? r.json() : null).catch(() => null);
  if (ol) layers.push(L.geoJSON(ol, { style: { color: "#ffc857", weight: 2, dashArray: "6 6", fill: false } }).addTo(map));
  const tr = await api.get(`/api/flights/${f.id}/geo/track`).catch(() => null);
  if (tr?.features?.length) layers.push(L.geoJSON(tr, { style: { color: "#3ddc97", weight: 2.5 } }).addTo(map));
  const g = L.featureGroup(layers);
  if (layers.length) map.fitBounds(g.getBounds(), { padding: [20, 20] }); else map.setView([40.078, -105.215], 14);
  if (!layers.length) L.marker([40.078, -105.215]).addTo(map).bindTooltip("Track appears once the flight log is added").openTooltip();
}

// ------------------------------------------------------------------ processing
function processingView(f) {
  $("#view").innerHTML = `
  <div class="split">
    <aside class="aside">
      <div><p class="eyebrow info" id="pEyebrow">Processing</p><h1 id="pTitle">…</h1><p class="muted" id="pSub" style="margin:6px 0 0"></p></div>
      <div class="pbar"><i id="pBar" style="width:0"></i></div>
      <ol class="steps" id="steps"></ol>
      <div class="row" style="margin-top:auto" id="pBtns"></div>
    </aside>
    <section class="main">
      <div class="row" style="justify-content:space-between"><h2>Log</h2><span class="muted mono" id="pDetail"></span></div>
      <pre class="console" id="log" aria-live="off"></pre>
    </section>
  </div>`;
  const tick = async () => {
    let r; try { r = await api.get(`/api/flights/${f.id}/job?n=200`); } catch { return; }
    const j = r.job || {}; const steps = j.steps || []; const done = new Set(j.done || []);
    const now = steps.find(s => s.id === j.step);
    $("#pTitle").textContent = j.state === "done" ? "Finished" : j.state === "error" ? "Stopped with an error" : j.state === "cancelled" ? "Cancelled"
      : j.state === "interrupted" ? "Interrupted" : now ? now.label : f.sample ? "Example flight" : "Not started";
    $("#pSub").textContent = j.error || (j.state === "running" ? `Step ${steps.indexOf(now) + 1} of ${steps.length}` : j.finished ? `Finished ${new Date(j.finished).toLocaleString()}` : "");
    $("#pBar").style.width = (j.state === "done" ? 100 : (j.progress || 0) + (now ? (j.step_progress || 0) * 0.01 * 10 : 0)) + "%";
    $("#pDetail").textContent = j.state === "running" ? (j.detail || "") : "";
    $("#steps").innerHTML = steps.map(s => {
      const cls = done.has(s.id) ? "done" : s.id === j.step && j.state === "running" ? "now" : "todo";
      return `<li class="${cls}"><span class="s">${cls === "done" ? "✓" : ""}</span><div><b>${esc(s.label)}</b>${j["t_" + s.id] ? `<small class="faint mono"> ${fmtDur(j["t_" + s.id])}</small>` : ""}</div>
        ${j.state !== "running" && !f.sample ? `<button class="btn sm" data-from="${s.id}">Re-run from here</button>` : ""}</li>`;
    }).join("");
    $$("#steps [data-from]").forEach(b => b.onclick = async () => { try { await api.post(`/api/flights/${f.id}/process`, { start_at: b.dataset.from }); } catch (e) { toast(e.message, true); } });
    $("#pBtns").innerHTML = j.state === "running" ? `<button class="btn danger" id="cancel" style="flex:1">Cancel</button>`
      : j.state === "done" ? `<a class="btn primary" style="flex:1" href="#/f/${f.id}/video">See results</a>`
      : ["error", "interrupted", "cancelled"].includes(j.state) ? `<button class="btn primary" id="resume" style="flex:1">Resume</button><a class="btn" href="#/f/${f.id}/import">Back to import</a>` : "";
    $("#cancel")?.addEventListener("click", () => api.post(`/api/flights/${f.id}/cancel`));
    $("#resume")?.addEventListener("click", async () => { try { await api.post(`/api/flights/${f.id}/process`, { start_at: "resume" }); } catch (e) { toast(e.message, true); } });
    const log = $("#log"); const atBottom = log.scrollTop + log.clientHeight > log.scrollHeight - 40;
    log.textContent = r.log.join("\n") || (f.sample ? "The example flight was processed before Studio existed; its results are bundled." : "");
    if (atBottom) log.scrollTop = log.scrollHeight;
    if (j.state === "done" && S.flight.status !== "done") { S.flight.status = "done"; flightView(f.id, "processing"); }
  };
  tick(); S.timer = setInterval(tick, 1500);
}

// ------------------------------------------------------------------ results: video
async function videoView(f) {
  const R = f.results || {}, rs = R.reflections || R.water?.reflections, lake = R.lake || {}, w = R.water || {}, solve = R.solve;
  const ol = w.outline_above_water_m?.median ?? lake.outline_above_water_m;
  const src = `/api/flights/${f.id}/media/ar.mp4`;
  const hasAR = await fetch(src, { method: "HEAD" }).then(r => r.ok).catch(() => false);
  $("#view").innerHTML = `
  <div class="split">
    <section class="main">
      <div class="player"><video id="vid" controls preload="metadata" playsinline src="${hasAR ? src : `/api/flights/${f.id}/media/video.mp4`}" poster="/api/flights/${f.id}/media/thumb.jpg"></video></div>
      <div class="card timeline">
        <div class="row" style="justify-content:space-between"><b>Where reflections land</b><span class="muted" style="font-size:12px">${R.ar_video_note ? esc(R.ar_video_note) + " · " : ""}click to jump</span></div>
        <div class="track" id="tl"><span class="head" id="head"></span></div>
        <div class="legend row" style="font-size:12px"><span><i class="sw water"></i> on water the camera saw</span><span><i class="sw" style="background:rgba(255,200,87,.45)"></i> on water by the map outline only</span></div>
      </div>
    </section>
    <aside class="aside right">
      ${rs ? `<div class="card"><p class="eyebrow info">Reflections · plan vs measured</p>
        <div class="grid2" style="margin-top:8px">
          <div class="stat"><b style="color:var(--warn)">${pct(rs.outline_water, rs.samples).toFixed(1)}%</b><small>on water by the map outline</small></div>
          <div class="stat"><b style="color:var(--info)">${pct(rs.measured_water, rs.samples).toFixed(1)}%</b><small>on water the camera saw</small></div></div>
        <p class="muted" style="font-size:12px;margin:8px 0 0">${rs.samples.toLocaleString()} reflection points: every satellite above the mask, every ${rs.step_s} s.</p>
        <a class="btn primary" style="margin-top:10px;width:100%" href="#/f/${f.id}/water">Water map + exports</a></div>` : ""}
      <div class="card"><p class="eyebrow">Lake on the day</p><div class="stats4" style="margin-top:8px">
        <div class="stat"><b>${ol != null ? ol.toFixed(1) + " m" : "—"}</b><small>water below the map outline</small></div>
        <div class="stat"><b>${lake.exposed_lakebed_ha != null ? lake.exposed_lakebed_ha.toFixed(1) + " ha" : fmtArea(w.water_area_m2)}</b><small>${lake.exposed_lakebed_ha != null ? "lakebed exposed (mapped part)" : "water mapped"}</small></div>
        ${lake.ripple_tilt_deg != null ? `<div class="stat"><b>${lake.ripple_tilt_deg}°</b><small>ripple tilt (sun glint)</small></div>` : ""}
        ${lake.wind_ms != null ? `<div class="stat"><b>${lake.wind_ms} m/s</b><small>wind from the ${esc(lake.wind_from)}, from drone lean</small></div>` : ""}
      </div></div>
      <div class="card"><p class="eyebrow">Checks</p>
        ${solve ? `<div class="kv"><span>Frames solved</span><span class="mono">${solve.frames.toLocaleString()} / ${solve.frames_total.toLocaleString()}</span></div>
        <div class="kv"><span>3D points</span><span class="mono">${solve.points.toLocaleString()}</span></div>
        <div class="kv"><span>Reprojection</span><span class="mono">${solve.reprojection_px} px</span></div>
        <div class="kv"><span>Fit to GPS</span><span class="mono">${solve.gps_fit_m} m</span></div>` : ""}
        ${R.georef ? `<div class="kv"><span>Tie to GPS</span><span class="mono" style="font-size:12px">${esc(R.georef.slice(-80))}</span></div>` : ""}
        ${R.sync ? `<div class="kv"><span>Clock sync</span><span class="mono">${(+R.sync.offset).toFixed(2)} s</span></div>` : ""}
        ${R.glint ? `<div class="kv"><span>Sun reflection check</span><span class="mono" style="color:var(--live)">${R.glint.median_px} px ✓</span></div>` : ""}
      </div>
      <div class="row"><a class="btn" href="${hasAR ? src : "#"}" download="HERON_AR_${esc(f.date || f.id)}.mp4" ${hasAR ? "" : "hidden"}>Save AR video</a>
        <a class="btn" href="/api/flights/${f.id}/exports/reflections.csv">Reflections CSV</a></div>
    </aside>
  </div>`;
  const vid = $("#vid"), tl = $("#tl");
  const refl = await api.get(`/api/flights/${f.id}/geo/reflections?max=20000`).catch(() => []);
  const T = vid.duration || f.video?.duration_s || Math.max(1, ...refl.map(r => r.t));
  const draw = (dur) => {
    const byT = new Map(); refl.forEach(r => { const k = r.t; const o = byT.get(k) || { m: 0, o: 0 }; o.m += r.m; o.o += r.o; byT.set(k, o); });
    const segs = (key) => { const out = []; let s = null; [...byT.entries()].sort((a, b) => a[0] - b[0]).forEach(([t, v]) => { if (v[key] > 0) { if (s == null) s = t; out.e = t; } else if (s != null) { out.push([s, out.e]); s = null; } }); if (s != null) out.push([s, out.e]); return out; };
    tl.querySelectorAll("i").forEach(e => e.remove());
    for (const [k, cls] of [["o", "o"], ["m", ""]]) for (const [a, b] of segs(k)) {
      if (a >= dur) continue;
      const i = document.createElement("i"); if (cls) i.className = cls; i.style.left = pct(a, dur) + "%"; i.style.width = Math.max(0.25, Math.min(100 - pct(a, dur), pct(b - a + 0.5, dur))) + "%"; tl.prepend(i);
    }
  };
  draw(T); vid.addEventListener("loadedmetadata", () => draw(vid.duration));
  vid.addEventListener("timeupdate", () => { $("#head").style.left = pct(vid.currentTime, vid.duration || T) + "%"; });
  tl.onclick = (e) => { const r = tl.getBoundingClientRect(); vid.currentTime = (e.clientX - r.left) / r.width * (vid.duration || T); };
}

// ------------------------------------------------------------------ water map + exports
async function waterView(f) {
  const w = f.results?.water || {}; const hasDem = (w.files || []).includes("dem.tif");
  const gOK = !!S.app?.google_maps_key;
  $("#view").innerHTML = `
  <div class="split">
    <section class="main">
      <div class="row maptools" style="justify-content:space-between">
        <div class="row" role="group" aria-label="Layers">
          <label class="chip"><input type="checkbox" id="lyW" checked> Measured water</label>
          <label class="chip"><input type="checkbox" id="lyO" checked> Map outline</label>
          <label class="chip"><input type="checkbox" id="lyT"> Flown track</label>
          <label class="chip"><input type="checkbox" id="lyR"> Reflection points</label>
          <label class="chip" id="lyPw"><input type="checkbox" id="lyP"> Drone photo map</label>
        </div>
        <div class="row">
          <select class="input" id="base" aria-label="Base map" style="width:auto;min-height:32px">
            ${Object.entries(BASES).map(([k, [l, , g]]) => `<option value="${k}" ${g && !gOK ? "disabled" : ""}>${l}${g && !gOK ? " (add key)" : ""}</option>`).join("")}</select>
          <button class="btn sm" id="measure" aria-pressed="false" title="Click points on the map; double-click to finish">Measure</button>
          <button class="btn sm" id="fs" title="Full screen map">⤢</button>
        </div>
      </div>
      <div class="map-full" id="wmap">
        <div class="map-hud glass mono" id="hud">Move over the map for coordinates</div>
      </div>
      <div class="row" style="font-size:12.5px">
        <label class="row muted">Water opacity <input type="range" id="opW" min="0" max="100" value="35" style="width:110px"></label>
        <label class="row muted" id="opPw">Photo opacity <input type="range" id="opP" min="0" max="100" value="95" style="width:110px"></label>
        <span class="muted" style="margin-left:auto">Open this view in</span>
        <a class="btn sm" id="gmaps" target="_blank" rel="noopener">Google Maps</a>
        <a class="btn sm" id="gearth" target="_blank" rel="noopener">Google Earth</a>
      </div>
    </section>
    <aside class="aside right">
      <div><p class="eyebrow info">Water map → next plan</p><h1>${w.outline_above_water_m ? `Water ${w.outline_above_water_m.median.toFixed(1)} m below the map` : "Measured water"}</h1>
        <p class="muted" style="margin:6px 0 0">${fmtArea(w.water_area_m2)} of water mapped from the camera${f.date ? `, ${esc(f.date)}` : ""}.</p></div>
      <div class="legend"><span><i class="sw water"></i> water the camera saw</span><span><i class="sw outline"></i> map outline the plan used</span>
        <span><i class="sw" style="background:#3ddc97;height:3px"></i> flown track</span><span><i class="sw level"></i> water at the level below</span></div>
      ${hasDem ? `<section class="card"><div class="row" style="justify-content:space-between"><p class="eyebrow">Other water level</p><b class="mono" id="lvlV" style="color:var(--live)">+0.00 m</b></div>
        <input type="range" id="lvl" min="-2" max="2.5" step="0.1" value="0" aria-label="Water level change in metres" style="margin-top:8px">
        <div class="row" style="margin-top:6px"><button class="btn sm" data-l="0">Flight day</button>${w.outline_above_water_m ? `<button class="btn sm" data-l="${w.outline_above_water_m.median}">Map outline level</button>` : ""}
        <a class="btn sm" id="lvlDl" href="/api/flights/${f.id}/exports/level.geojson?delta=0">Export this level</a></div>
        <small class="faint" style="display:block;margin-top:6px">Uses the 3D height model to move the edge. Heights are metres above the water on the flight day.</small></section>` : ""}
      ${f.results?.site_package ? `<section class="callout"><b>For the Specular Planner</b><p class="muted" style="margin:4px 0 10px">One file with the measured water and heights, to score lines against the real shoreline.</p>
        <a class="btn primary" style="width:100%" href="/api/flights/${f.id}/exports/${encodeURIComponent(f.results.site_package)}">Download site package</a></section>` : ""}
      <section class="card"><div class="row" style="justify-content:space-between"><p class="eyebrow">Export</p><button class="btn sm" id="rebuild">Rebuild</button></div>
        <div id="dl" style="margin-top:6px"></div>
        <div class="row" style="margin-top:12px"><a class="btn solid" href="/api/flights/${f.id}/exports/all.zip">Download all (.zip)</a></div></section>
      <details class="card"><summary>Map settings</summary>
        <label class="field" style="margin-top:10px"><span>Google Maps API key (for Google base maps)</span>
          <input class="input mono" id="gkey" type="password" autocomplete="off" placeholder="AIza…" value="${esc(S.app?.google_maps_key || "")}"></label>
        <small class="faint" style="display:block;margin-top:6px">Needs the Maps JavaScript API on a Google Cloud project; if the key has website restrictions, allow <span class="mono">http://127.0.0.1:*</span>. The key is saved only on this computer. Esri satellite, streets and topo need no key.</small>
        <button class="btn sm" id="gsave" style="margin-top:8px">Save</button></details>
    </aside>
  </div>`;
  const ex = await api.get(`/api/flights/${f.id}/exports`);
  const byName = Object.fromEntries(ex.map(e => [e.name, e]));
  const row = (e) => `<div><b>${esc(e.label)}</b><small>${esc(e.name)} · ${esc(e.kind)} · ${fmtBytes(e.size)}</small></div>
    <a class="btn sm" href="/api/flights/${f.id}/exports/${encodeURIComponent(e.name)}" download>Download</a>`;
  const groups = (S.app?.export_groups || []).map(([g, names]) => {
    const items = names.map(n => byName[n]).filter(Boolean); return items.length ? `<p class="eyebrow" style="margin:12px 0 6px">${esc(g)}</p><div class="dl">${items.map(row).join("")}</div>` : "";
  }).join("");
  $("#dl").innerHTML = groups || `<small class="muted">No exports yet</small>`;
  $("#rebuild").onclick = async () => { try { await api.post(`/api/flights/${f.id}/exports/build`); toast("Exports rebuilt"); waterView(await reload()); } catch (e) { toast(e.message, true); } };
  $("#gsave").onclick = async () => { S.app = { ...S.app, ...(await api.post("/api/settings", { google_maps_key: $("#gkey").value.trim() })) }; toast("Saved"); waterView(f); };

  const { map, setBase } = baseMap($("#wmap")); S.map = map;
  $("#base").value = store.get("heron.base", "satellite");
  $("#base").onchange = (e) => setBase(e.target.value);
  map.on("basechange", (e) => { $("#base").value = e.key; });
  const [water, outline, ortho, track] = await Promise.all([
    fetch(`/api/flights/${f.id}/geo/water`).then(r => r.ok ? r.json() : null),
    fetch(`/api/flights/${f.id}/geo/outline`).then(r => r.ok ? r.json() : null),
    fetch(`/api/flights/${f.id}/geo/ortho`).then(r => r.ok ? r.json() : null),
    fetch(`/api/flights/${f.id}/geo/track`).then(r => r.ok ? r.json() : null)]);
  const Ly = {};
  if (ortho) Ly.P = L.imageOverlay(`/api/flights/${f.id}/media/ortho.png`, [[ortho.south, ortho.west], [ortho.north, ortho.east]], { opacity: 0.95 });
  else { $("#lyPw").hidden = true; $("#opPw").hidden = true; }
  if (outline) Ly.O = L.geoJSON(outline, { style: { color: "#ffc857", weight: 2.5, dashArray: "7 6", fill: false } }).addTo(map);
  if (water) Ly.W = L.geoJSON(water, { style: { color: "#4fd6ff", weight: 1.5, fillColor: "#4fd6ff", fillOpacity: 0.35 },
    onEachFeature: (ft, l) => l.bindTooltip(fmtArea(ft.properties.area_m2), { sticky: true }) }).addTo(map);
  if (track?.features?.length) Ly.T = L.geoJSON(track, { style: { color: "#3ddc97", weight: 2.5 } });
  const fitTo = Ly.W || Ly.O; if (fitTo) map.fitBounds(fitTo.getBounds(), { padding: [20, 20] });
  const tog = (id, key, mk) => $(id).onchange = async (e) => {
    if (!Ly[key] && mk) Ly[key] = await mk();
    if (Ly[key]) e.target.checked ? Ly[key].addTo(map) : Ly[key].remove();
  };
  tog("#lyW", "W"); tog("#lyO", "O"); tog("#lyP", "P"); tog("#lyT", "T");
  tog("#lyR", "R", async () => {
    const pts = await api.get(`/api/flights/${f.id}/geo/reflections?max=6000`);
    return L.layerGroup(pts.map(p => L.circleMarker([p.lat, p.lon], { radius: 3, weight: 0, fillOpacity: 0.85, fillColor: p.m ? "#4fd6ff" : p.o ? "#ffc857" : "#93a3b0" })
      .bindTooltip(`${p.sat} · ${p.el.toFixed(0)}° · ${p.t.toFixed(1)} s<br>${p.m ? "on water" : p.o ? "map says water, camera says dry" : "on land"}`)));
  });
  $("#opW").oninput = (e) => Ly.W?.setStyle({ fillOpacity: e.target.value / 100, opacity: Math.min(1, e.target.value / 35) });
  $("#opP").oninput = (e) => Ly.P?.setOpacity(e.target.value / 100);

  // coordinates + "open in Google" links follow the view
  const hud = $("#hud");
  map.on("mousemove", (e) => { if (!measuring) hud.textContent = `${e.latlng.lat.toFixed(6)}, ${e.latlng.lng.toFixed(6)}  ·  click to copy`; });
  map.on("click", (e) => {
    if (measuring) return;
    const t = `${e.latlng.lat.toFixed(6)}, ${e.latlng.lng.toFixed(6)}`;
    navigator.clipboard?.writeText(t).then(() => toast(`Copied ${t}`)).catch(() => { });
  });
  const links = () => {
    const c = map.getCenter(), z = map.getZoom(), m = Math.round(40000000 / 2 ** z * 1.2);
    $("#gmaps").href = `https://www.google.com/maps/@${c.lat.toFixed(6)},${c.lng.toFixed(6)},${m}m/data=!3m1!1e3`;
    $("#gearth").href = `https://earth.google.com/web/@${c.lat.toFixed(6)},${c.lng.toFixed(6)},0a,${m}d,35y,0h,0t,0r`;
  };
  map.on("moveend", links); links();

  // measure: click points; click the last point again (or press Enter) to finish; shows length and, with 3+ points, area
  let measuring = false, pts = [], ml = null, mp = null;
  const clearM = () => { pts = []; ml?.remove(); mp?.remove(); ml = mp = null; };
  const showM = (live) => {
    const all = live ? [...pts, live] : pts; ml?.remove(); mp?.remove(); ml = mp = null;
    if (all.length < 2) return;
    ml = L.polyline(all, { color: "#fff", weight: 2, dashArray: "4 4", interactive: false }).addTo(map);
    let d = 0; for (let i = 1; i < all.length; i++) d += map.distance(all[i - 1], all[i]);
    let txt = `Length ${d >= 1000 ? (d / 1000).toFixed(3) + " km" : d.toFixed(1) + " m"}`;
    if (all.length >= 3) {
      mp = L.polygon(all, { color: "#fff", weight: 0, fillOpacity: 0.12, interactive: false }).addTo(map);
      const o = all[0], k = Math.PI / 180, R = 6371008.8;
      const xy = all.map(p => [(p.lng - o.lng) * k * R * Math.cos(o.lat * k), (p.lat - o.lat) * k * R]);
      let a = 0; for (let i = 0; i < xy.length; i++) { const [x1, y1] = xy[i], [x2, y2] = xy[(i + 1) % xy.length]; a += x1 * y2 - x2 * y1; }
      txt += ` · area ${fmtArea(Math.abs(a) / 2)}`;
    }
    hud.textContent = txt + (measuring ? " · click the last point or press Enter to finish" : "");
  };
  const setMeasuring = (on) => {
    measuring = on; $("#measure").setAttribute("aria-pressed", on); $("#measure").textContent = on ? "Done" : "Measure";
    map.getContainer().style.cursor = on ? "crosshair" : ""; map.doubleClickZoom[on ? "disable" : "enable"]();
  };
  const finish = () => { setMeasuring(false); showM(); };
  $("#measure").onclick = () => {
    if (measuring) return finish();
    clearM(); setMeasuring(true); hud.textContent = "Click points to measure a distance or an area";
  };
  map.on("click", (e) => {
    if (!measuring) return;
    const last = pts[pts.length - 1];
    if (last && map.latLngToContainerPoint(last).distanceTo(e.containerPoint) < 8) return finish();
    pts.push(e.latlng); showM();
  });
  map.on("mousemove", (e) => { if (measuring && pts.length) showM(e.latlng); });
  document.addEventListener("keydown", function k_(e) { if (!$("#wmap")) return document.removeEventListener("keydown", k_); if (measuring && e.key === "Enter") finish(); });

  $("#fs").onclick = () => { $("#wmap").classList.toggle("fullscreen"); setTimeout(() => map.invalidateSize(), 50); };
  document.addEventListener("keydown", function esc_(e) { if (!$("#wmap")) return document.removeEventListener("keydown", esc_); if (e.key === "Escape") { $("#wmap").classList.remove("fullscreen"); map.invalidateSize(); } });

  if (hasDem) {
    let lv = null, tmo;
    const set = (d) => {
      $("#lvl").value = d; $("#lvlV").textContent = (d >= 0 ? "+" : "") + (+d).toFixed(2) + " m"; $("#lvlDl").href = `/api/flights/${f.id}/exports/level.geojson?delta=${d}`;
      clearTimeout(tmo); tmo = setTimeout(async () => {
        if (+d === 0) { lv?.remove(); lv = null; return; }
        const g = await api.get(`/api/flights/${f.id}/geo/level?delta=${d}`).catch((e) => (toast(e.message, true), null)); if (!g) return;
        lv?.remove(); lv = L.geoJSON(g, { style: { color: "#3ddc97", weight: 1.5, fillColor: "#3ddc97", fillOpacity: 0.22 } }).addTo(map);
      }, 250);
    };
    $("#lvl").oninput = (e) => set(e.target.value);
    $$("[data-l]").forEach(b => b.onclick = () => set(b.dataset.l));
  }
}

// ------------------------------------------------------------------ 3D model
async function modelView(f) {
  $("#view").innerHTML = `
  <div class="split">
    <section class="main">
      <div class="three" id="three">
        <div class="overlay tl"><button class="btn sm solid" data-v="orbit">Orbit</button><button class="btn sm glass" data-v="top">Top</button><button class="btn sm glass" data-v="low">Low angle</button></div>
        <div class="overlay bl"><div class="glass mono" id="probe" style="font-size:12px">Click the ground to read its height</div></div>
      </div>
    </section>
    <aside class="aside right">
      <div><p class="eyebrow info">3D reconstruction</p><h1>Shore built from the video</h1>
        <p class="muted" style="margin:6px 0 0">Heights come from the camera solve. The water is a flat plane you can raise or lower.</p></div>
      <section><div class="row" style="justify-content:space-between"><p class="eyebrow">Water level</p><b class="mono" id="wl" style="color:var(--info)">+0.00 m</b></div>
        <input type="range" id="wlr" min="-2" max="2.5" step="0.05" value="0" aria-label="Water level"></section>
      <section><div class="row" style="justify-content:space-between"><p class="eyebrow">Height exaggeration</p><b class="mono" id="exv">2×</b></div>
        <input type="range" id="ex" min="1" max="10" step="0.5" value="2" aria-label="Height exaggeration"></section>
      <section><p class="eyebrow" style="margin-bottom:6px">Show</p>
        <label class="row" style="min-height:30px"><input type="checkbox" id="cTex" checked> Photo texture (where mapped)</label>
        <label class="row" style="min-height:30px"><input type="checkbox" id="cTrack" checked> Flown track</label>
        <label class="row" style="min-height:30px"><input type="checkbox" id="cWater" checked> Water plane</label></section>
      <div class="row"><a class="btn sm" href="/api/flights/${f.id}/exports/dem.tif" download>Height model (GeoTIFF)</a><button class="btn sm" id="snap">Save view as PNG</button></div>
      <div class="card" id="mstat"></div>
    </aside>
  </div>`;
  const m = await api.get(`/api/flights/${f.id}/model3d?max=${Math.min(900, Math.round(innerWidth * devicePixelRatio / 2))}`).catch((e) => (toast(e.message, true), null));
  if (!m) return;
  const { mountTerrain } = await import("/static/terrain3d.js");
  S.three = mountTerrain($("#three"), m, f.id);
  $("#mstat").innerHTML = `<div class="kv"><span>Grid</span><span class="mono">${m.w}×${m.h} · ${m.cell.toFixed(1)} m</span></div>
    <div class="kv"><span>Area</span><span class="mono">${(m.w * m.cell).toFixed(0)} × ${(m.h * m.cell).toFixed(0)} m</span></div>`;
  $("#wlr").oninput = (e) => { $("#wl").textContent = (e.target.value >= 0 ? "+" : "") + (+e.target.value).toFixed(2) + " m"; S.three.setWater(+e.target.value); };
  $("#ex").oninput = (e) => { $("#exv").textContent = e.target.value + "×"; S.three.setExaggeration(+e.target.value); };
  $("#cTrack").onchange = (e) => S.three.show("track", e.target.checked);
  $("#cWater").onchange = (e) => S.three.show("water", e.target.checked);
  $("#cTex").onchange = (e) => S.three.setTexture(e.target.checked);
  $$("[data-v]").forEach(b => b.onclick = () => { $$("[data-v]").forEach(x => x.className = "btn sm glass"); b.className = "btn sm solid"; S.three.view(b.dataset.v); });
  $("#snap").onclick = () => { const a = document.createElement("a"); a.href = $("#three canvas").toDataURL("image/png"); a.download = `HERON_3D_${f.date || f.id}.png`; a.click(); };
  S.three.onProbe = (p) => { $("#probe").textContent = p ? `E ${p.e.toFixed(0)} m · N ${p.n.toFixed(0)} m · ${p.h >= 0 ? "+" : ""}${p.h.toFixed(2)} m above flight-day water` : "No height here"; };
}

// ------------------------------------------------------------------ start
(async () => {
  try { S.app = await api.get("/api/state"); } catch { }
  route();
})();
