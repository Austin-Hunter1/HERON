// Recordings section of the Payload Setup page: list sessions, draw a spectrogram + histogram of any
// channel (the NUC sends ~1 MB of evenly spaced sample slices; the FFTs run here in the browser),
// and download single files. "Find drops" reads a radio's samples.jsonl on the NUC and lists where samples
// are missing; the spectrogram marks those places.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const H = window.HERON;
  const esc = H.esc;
  let sessions = [], selected = null, view = null, loading = false, lastBusy = null;
  const drops = {};                  // "session|radio" -> drop scan result, {busy}, or {error}

  const fmtLen = (s) => !s ? "0 s" : s < 90 ? s.toFixed(s < 10 ? 1 : 0) + " s" : Math.floor(s / 60) + " min " + String(Math.round(s % 60)).padStart(2, "0") + " s";
  const fmtWhen = (iso, name) => {
    if (!iso) return name;
    const d = new Date(iso);
    return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };
  const mhz = (hz) => hz ? (hz / 1e6).toFixed(hz % 1e4 ? 3 : 2) + " MHz" : "—";
  const busyNow = () => !!(H.state && (H.state.locked || H.state.test?.running));

  // ---------------- list ----------------
  async function load(keep) {
    if (loading) return;
    loading = true;
    try {
      const r = await H.api("/api/recordings");
      sessions = r.sessions;
      const where = r.folders.filter((f) => f.exists).map((f) => `${f.drive}: ${f.path}`);
      $("recsWhere").textContent = where.length ? "Looking in " + where.join(" · ") : "";
      if (!keep || !sessions.some((s) => s.name === selected)) selected = sessions[0]?.name || null;
      renderList();
      renderDetail();
    } catch (e) { H.toast("Recordings: " + e.message, true); }
    finally { loading = false; }
  }

  function badge(s) {
    if (s.complete === null) return `<span class="tag warn">in progress</span>`;
    if (!s.complete) return `<span class="tag bad">incomplete</span>`;
    if (s.overflows || s.gaps) return `<span class="tag warn">${s.overflows + s.gaps} dropped</span>`;
    return "";
  }

  function renderList() {
    const list = $("recList");
    if (!sessions.length) { list.innerHTML = `<li class="empty">No recordings yet. Use “Record test” above or start one from the ground station.</li>`; return; }
    list.innerHTML = sessions.map((s) => `<li><button type="button" data-s="${esc(s.name)}" class="${s.name === selected ? "on" : ""}">
        <b>${esc(fmtWhen(s.started, s.name))}</b>${badge(s)}
        <span>${esc(s.setup || "unknown setup")} · ${fmtLen(s.seconds)} · ${H.fmtGB(s.bytes)}${s.drive === "NUC SSD" ? " · NUC SSD" : ""}${s.simulated ? " · simulated" : ""}</span>
      </button></li>`).join("");
    list.querySelectorAll("[data-s]").forEach((b) => b.addEventListener("click", () => {
      selected = b.dataset.s; view = null; renderList(); renderDetail();
    }));
  }

  // ---------------- detail ----------------
  function fileLink(s, radio, f, text) {
    const q = new URLSearchParams({ session: s.name, name: f.name });
    if (radio) q.set("radio", radio);
    return `<a class="dl" href="/api/recordings/file?${q}" download>${esc(text || f.name)}</a>`;
  }

  function renderDetail() {
    const box = $("recDetail");
    const s = sessions.find((x) => x.name === selected);
    if (!s) { box.innerHTML = `<p class="note">Pick a recording to see its channels, draw a spectrogram and histogram, or download files.</p>`; return; }
    const busy = busyNow();
    const problems = [...s.errors, ...(s.overflows ? [`${s.overflows} overflow(s): samples were dropped because USB or the disk fell behind`] : []),
      ...(s.gaps ? [`${s.gaps} gap(s) in the sample timeline`] : [])];
    box.innerHTML = `
      <div class="rec-title">
        <div><h3>${esc(fmtWhen(s.started, s.name))}</h3>
          <p class="note"><code>${esc(s.folder)}</code></p></div>
        <dl class="rec-facts">
          <div><dt>Setup</dt><dd>${esc(s.setup || "—")}</dd></div>
          <div><dt>Length</dt><dd>${fmtLen(s.seconds)}</dd></div>
          <div><dt>Size</dt><dd>${H.fmtGB(s.bytes)}</dd></div>
          <div><dt>Stored on</dt><dd>${esc(s.drive)}</dd></div>
        </dl>
      </div>
      ${problems.length ? `<p class="warn">${problems.map(esc).join("<br>")}</p>` : ""}
      <p class="note pull-cmd">To copy this session into D:\\01_Data\\collects on your laptop (one .dat, .yml and metadata.yml entry per channel), run in the HERON_Payload folder:
        <code>uv run tools/heron_pull.py --nuc ${esc(location.origin)} --session ${esc(s.name)}</code></p>
      ${busy ? `<p class="warn">The payload is recording or armed. Viewing and downloading are paused so the disk keeps up.</p>` : ""}
      ${s.radios.map((r) => `
        <div class="rec-radio">
          <div class="rec-radio-head"><b>${esc(r.name)}</b>
            <span>${esc((r.model || "").toUpperCase())}${r.serial ? " · " + esc(r.serial) : ""} · ${mhz(r.freq_hz)} · ${r.rate_hz ? (r.rate_hz / 1e6).toFixed(2) + " MS/s" : "rate ?"} · ${esc(r.format || "")}</span>
            <span class="files">${r.files.map((f) => fileLink(s, r.id, f)).join(" ")}</span>
            <button type="button" class="btn quiet" data-drops="${esc(r.id)}" ${busy ? "disabled" : ""}>Find drops</button>
          </div>
          <div class="drops" data-drops-box="${esc(r.id)}">${dropsHtml(s, r)}</div>
          ${r.channels.map((c) => `
            <div class="rec-chan">
              <span class="lbl"><b>${esc(c.label)}</b><i>${c.gain_db != null ? c.gain_db + " dB gain" : ""}${c.port ? " · " + esc(c.port) : ""} · ${fmtLen(c.seconds)} · ${H.fmtGB(c.bytes)}</i></span>
              <button type="button" class="btn" data-view="${esc(r.id)}|${c.n}" ${busy ? "disabled" : ""}>Spectrogram + histogram</button>
              <select class="seg" data-seg="${esc(r.id)}|${c.n}" aria-label="Download a file" ${busy ? "disabled" : ""}>
                <option value="">Download…</option>
                ${c.segments.map((g) => `<option value="${esc(g.name)}">${esc(g.name)} (${H.fmtGB(g.bytes)}${g.seconds != null ? ", " + fmtLen(g.seconds) : ""})</option>`).join("")}
              </select>
            </div>`).join("") || `<p class="note">No sample files in this radio folder.</p>`}
        </div>`).join("") || `<p class="note">This session folder has no radio folders.</p>`}
      <div class="rec-session-files">${["session.json", "completion.json"].map((n) => fileLink(s, null, { name: n })).join(" ")}</div>
      <div id="viewer"></div>`;
    box.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
      const [radio, n] = b.dataset.view.split("|");
      openViewer(s, radio, Number(n));
    }));
    box.querySelectorAll("[data-drops]").forEach((b) => b.addEventListener("click", () => findDrops(s, b.dataset.drops)));
    box.querySelectorAll("[data-show]").forEach((b) => b.addEventListener("click", () => {
      const [radio, at] = b.dataset.show.split("|");
      const r = s.radios.find((x) => x.id === radio);
      // One second around the drop, on the radio's first channel (every channel of a radio drops together).
      openViewer(s, radio, r?.channels[0]?.n ?? 0, Math.max(0, Math.round((Number(at) - 0.5) * 1e4) / 1e4), 1);
    }));
    box.querySelectorAll("[data-seg]").forEach((sel) => sel.addEventListener("change", () => {
      if (!sel.value) return;
      const [radio] = sel.dataset.seg.split("|");
      const q = new URLSearchParams({ session: s.name, radio, name: sel.value });
      const a = document.createElement("a"); a.href = "/api/recordings/file?" + q; a.download = ""; document.body.appendChild(a); a.click(); a.remove();
      sel.value = "";
    }));
    box.querySelectorAll("a.dl").forEach((a) => a.addEventListener("click", (e) => { if (busyNow()) { e.preventDefault(); H.toast("Paused while the payload is recording or armed.", true); } }));
    if (view && view.session === s.name) renderViewer();
  }

  // ---------------- drops ----------------
  async function findDrops(s, radio) {
    if (busyNow()) return H.toast("Paused while the payload is recording or armed.", true);
    const key = s.name + "|" + radio;
    drops[key] = { busy: true };
    renderDetail();
    try {
      drops[key] = await H.api("/api/recordings/drops?" + new URLSearchParams({ session: s.name, radio }));
    } catch (e) { drops[key] = { error: e.message }; }
    renderDetail();
  }

  function dropsHtml(s, r) {
    const d = drops[s.name + "|" + r.id];
    if (!d) return "";
    if (d.busy) return `<p class="note">Reading samples.jsonl… a long recording takes up to a minute.</p>`;
    if (d.error) return `<p class="warn">${esc(d.error)}</p>`;
    if (!d.total) return `<p class="note good-note">No drops: every one of ${d.rows.toLocaleString()} blocks starts where the one before ended (${fmtLen(d.recorded_s)} checked).</p>`;
    const seg = (n) => String(n).padStart(6, "0");
    const files = (g) => d.channels.map((c) => `channel_${c.n}_${seg(g)}.${d.format || "sc8"}`).join(", ");
    return `
      <p class="warn">${d.total} drop(s), ${d.missing_samples.toLocaleString()} samples missing (${(d.missing_s * 1e3).toFixed(2)} ms) in ${fmtLen(d.recorded_s)}.
        Every channel of this radio drops at the same places. The files have no padding: the samples after a drop follow straight on.
        ${d.total > d.listed ? `The first ${d.listed} are listed.` : ""}</p>
      <div class="drops-scroll"><table class="drops-table">
        <thead><tr><th>#</th><th>In file (s)</th><th>Real time (s)</th><th>File</th><th>Before sample</th><th>Missing</th><th></th></tr></thead>
        <tbody>${d.drops.map((x, i) => `<tr>
          <td>${i + 1}</td><td>${x.at_s.toFixed(4)}</td><td>${x.device_s.toFixed(4)}</td>
          <td><code>${esc(files(x.segment))}</code></td><td>${x.segment_offset.toLocaleString()}</td>
          <td>${x.kind === "samples lost" ? `${x.missing.toLocaleString()} (${x.missing_ms} ms)` : esc(x.kind) + (x.uhd_error ? " " + x.uhd_error : "")}</td>
          <td><button type="button" class="btn quiet" data-show="${esc(r.id)}|${x.at_s}">Show</button></td></tr>`).join("")}</tbody>
      </table></div>
      <p class="note">“In file” is the time on the spectrogram (recorded samples ÷ rate). “Real time” is the device clock from the first sample; it runs ahead of “In file” by the missing time.</p>`;
  }

  // ---------------- viewer ----------------
  function openViewer(s, radio, n, start = 0, span = 0) {
    const r = s.radios.find((x) => x.id === radio);
    const c = r?.channels.find((x) => x.n === n);
    view = { session: s.name, radio, n, label: c?.label || "channel " + n, total: c?.seconds || 0, start, span, data: null, error: null, busy: false };
    renderViewer();
    draw();
    $("viewer").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function renderViewer() {
    const v = $("viewer");
    if (!v || !view) return;
    v.innerHTML = `
      <div class="viewer">
        <div class="viewer-head">
          <h3>${esc(view.label)}</h3>
          <label><span>Start</span><span class="unit"><input id="vStart" type="number" min="0" step="1" value="${view.start}" /><i>s</i></span></label>
          <label><span>Length</span><span class="unit"><input id="vSpan" type="number" min="0" step="1" value="${view.span || ""}" placeholder="all" /><i>s</i></span></label>
          <label><span>Detail</span><select id="vNfft"><option value="256">256 bins</option><option value="512" selected>512 bins</option><option value="1024">1024 bins</option></select></label>
          <button id="vDraw" type="button" class="btn primary">Draw</button>
          <button id="vSave" type="button" class="btn quiet">Save pictures</button>
          <button id="vClose" type="button" class="icon-btn" aria-label="Close viewer" title="Close"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
        </div>
        <p class="note">Recording is ${fmtLen(view.total)} long. The spectrogram samples slices across the chosen range; GNSS signals sit under the noise, so expect a smooth band shaped by the antenna filter, not satellite lines.</p>
        <div id="vBody" class="viewer-body">${view.error ? `<p class="warn">${esc(view.error)}</p>` : view.busy ? `<p class="note">Reading samples…</p>` : ""}</div>
      </div>`;
    $("vNfft").value = String(view.nfft || 512);
    $("vDraw").addEventListener("click", () => {
      view.start = Math.max(0, Number($("vStart").value) || 0);
      view.span = Math.max(0, Number($("vSpan").value) || 0);
      view.nfft = Number($("vNfft").value);
      draw();
    });
    $("vSave").addEventListener("click", savePictures);
    $("vClose").addEventListener("click", () => { view = null; v.innerHTML = ""; });
    if (view.data && !view.busy) paint();
  }

  async function draw() {
    if (busyNow()) { view.error = "Paused while the payload is recording or armed."; renderViewer(); return; }
    view.busy = true; view.error = null; renderViewer();
    const q = new URLSearchParams({ session: view.session, radio: view.radio, channel: view.n, start: view.start, rows: 240, nfft: view.nfft || 512, avg: 4 });
    if (view.span) q.set("span", view.span);
    try {
      const d = await H.api("/api/recordings/view?" + q);
      view.data = analyse(d);
    } catch (e) { view.error = e.message; view.data = null; }
    view.busy = false;
    renderViewer();
  }

  // FFT (radix-2, in place)
  function fft(re, im) {
    const n = re.length;
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const ang = -2 * Math.PI / len, wr = Math.cos(ang), wi = Math.sin(ang);
      for (let i = 0; i < n; i += len) {
        let cr = 1, ci = 0;
        for (let k = 0; k < len / 2; k++) {
          const a = i + k, b = a + len / 2;
          const tr = re[b] * cr - im[b] * ci, ti = re[b] * ci + im[b] * cr;
          re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
          const nr = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = nr;
        }
      }
    }
  }

  function analyse(d) {
    const raw = Uint8Array.from(atob(d.data), (ch) => ch.charCodeAt(0));
    const eight = d.format === "sc8";
    const vals = eight ? new Int8Array(raw.buffer) : new Int16Array(raw.buffer);
    const scale = eight ? 1 : 1 / 256;            // report sc16 on the same 8-bit scale
    const nfft = d.nfft, rows = d.rows, per = d.row_samples;
    const win = Float64Array.from({ length: nfft }, (_, i) => 0.5 - 0.5 * Math.cos(2 * Math.PI * i / nfft));
    const wpow = win.reduce((a, w) => a + w * w, 0);
    const spec = new Float32Array(rows * nfft), mean = new Float64Array(nfft);
    const re = new Float64Array(nfft), im = new Float64Array(nfft);
    const hist = new Array(64).fill(0);
    let sq = 0, clip = 0, si = 0, sqq = 0, n = 0;
    for (let r = 0; r < rows; r++) {
      const acc = new Float64Array(nfft);
      for (let a = 0; a < d.avg; a++) {
        const base = (r * per + a * nfft) * 2;
        for (let k = 0; k < nfft; k++) {
          const I = vals[base + 2 * k] * scale, Q = vals[base + 2 * k + 1] * scale;
          re[k] = I * win[k]; im[k] = Q * win[k];
          for (const v of [I, Q]) {
            sq += v * v; n++;
            if (v <= -128 || v >= 127) clip++;
            hist[Math.min(63, Math.max(0, Math.floor((v + 128) / 4)))]++;
          }
          si += I; sqq += Q;
        }
        fft(re, im);
        for (let k = 0; k < nfft; k++) acc[k] += re[k] * re[k] + im[k] * im[k];
      }
      for (let k = 0; k < nfft; k++) {
        const p = acc[(k + nfft / 2) % nfft] / (d.avg * wpow);   // fftshift: negative frequencies first
        mean[k] += p / rows;
        spec[r * nfft + k] = 10 * Math.log10(p + 1e-12);
      }
    }
    const sorted = Float32Array.from(spec).sort();
    const lo = sorted[Math.floor(sorted.length * 0.02)], hi = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.998))];
    const rms = Math.sqrt(sq / n), clipPct = 100 * clip / n;
    const verdict = clipPct > 0.1 ? ["Clipping: lower the gain", "bad"] : rms < 6 ? ["Very quiet: raise the gain or check antenna power", "warn"]
      : rms < 12 ? ["A little quiet", "ok"] : rms > 48 ? ["A little hot", "ok"] : ["Good level", "good"];
    return { ...d, spec, mean: Array.from(mean, (p) => 10 * Math.log10(p + 1e-12)), lo, hi: Math.max(hi, lo + 1), hist,
      rms, clipPct, dcI: 2 * si / n, dcQ: 2 * sqq / n, verdict };
  }

  // viridis-like colour map
  const STOPS = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]];
  function colour(t) {
    t = Math.min(1, Math.max(0, t)) * (STOPS.length - 1);
    const i = Math.min(STOPS.length - 2, Math.floor(t)), f = t - i, a = STOPS[i], b = STOPS[i + 1];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
  }

  function canvas(w, h) {
    const c = document.createElement("canvas");
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    c.width = w * dpr; c.height = h * dpr; c.style.width = "100%"; c.style.maxWidth = w + "px"; c.style.aspectRatio = w + " / " + h;
    const g = c.getContext("2d"); g.scale(dpr, dpr);
    g.font = "12px Barlow, system-ui, sans-serif"; g.fillStyle = "#fff"; g.fillRect(0, 0, w, h);
    return [c, g];
  }

  function freqTicks(g, x0, x1, y, halfMHz) {
    g.fillStyle = "#5d7071"; g.textAlign = "center"; g.textBaseline = "top";
    const step = halfMHz > 8 ? 5 : halfMHz > 3 ? 2 : halfMHz > 1.5 ? 1 : 0.5;
    for (let f = -Math.floor(halfMHz / step) * step; f <= halfMHz + 1e-9; f += step) {
      const x = x0 + (f + halfMHz) / (2 * halfMHz) * (x1 - x0);
      g.fillRect(x, y, 1, 4); g.fillText((f > 0 ? "+" : "") + +f.toFixed(1), x, y + 6);
    }
  }

  function paint() {
    const d = view.data, body = $("vBody");
    const halfMHz = d.rate_hz / 2e6, W = 760, P = { l: 58, r: 70, t: 12, b: 40 };
    // spectrogram
    const [sc, sg] = canvas(W, 380);
    const pw = W - P.l - P.r, ph = 380 - P.t - P.b;
    const img = sg.createImageData(d.nfft, d.rows);
    for (let r = 0; r < d.rows; r++) for (let k = 0; k < d.nfft; k++) {
      const [R, G, B] = colour((d.spec[r * d.nfft + k] - d.lo) / (d.hi - d.lo));
      const o = (r * d.nfft + k) * 4; img.data[o] = R; img.data[o + 1] = G; img.data[o + 2] = B; img.data[o + 3] = 255;
    }
    const tmp = document.createElement("canvas"); tmp.width = d.nfft; tmp.height = d.rows; tmp.getContext("2d").putImageData(img, 0, 0);
    sg.imageSmoothingEnabled = false; sg.drawImage(tmp, P.l, P.t, pw, ph);
    sg.strokeStyle = "#b7c4be"; sg.strokeRect(P.l + .5, P.t + .5, pw, ph);
    freqTicks(sg, P.l, P.l + pw, P.t + ph, halfMHz);
    sg.textAlign = "center"; sg.fillStyle = "#15292b";
    sg.fillText(`MHz from ${(d.freq_hz / 1e6).toFixed(2)} MHz`, P.l + pw / 2, 380 - 16);
    // Drop markers: a red line at each drop inside the drawn time range (from "Find drops").
    const known = drops[view.session + "|" + view.radio];
    const t0 = d.times[0], t1 = d.times[d.rows - 1];
    const shown = (known?.drops || []).filter((x) => x.at_s >= d.start_s && x.at_s <= d.start_s + d.span_s);
    shown.forEach((x) => {
      const f = t1 > t0 ? (x.at_s - t0) / (t1 - t0) : 0;
      const y = P.t + (Math.min(Math.max(f, 0), 1) * (d.rows - 1) + 0.5) * ph / d.rows;
      sg.fillStyle = "#e0301e"; sg.fillRect(P.l, y - 1, pw, 2);
      sg.fillRect(P.l + pw, y - 1, 10, 2);
    });
    d.dropsShown = shown.length;
    d.dropsKnown = !!known?.drops;
    sg.textAlign = "right"; sg.textBaseline = "middle"; sg.fillStyle = "#5d7071";
    for (let i = 0; i <= 4; i++) {
      const r = Math.round(i * (d.rows - 1) / 4), y = P.t + (r + 0.5) * ph / d.rows;
      sg.fillRect(P.l - 4, y, 4, 1); sg.fillText(d.times[r].toFixed(d.span_s < 10 ? 2 : 0) + " s", P.l - 7, y);
    }
    for (let i = 0; i < ph; i++) { const [R, G, B] = colour(1 - i / ph); sg.fillStyle = `rgb(${R},${G},${B})`; sg.fillRect(W - P.r + 16, P.t + i, 12, 1); }
    sg.fillStyle = "#5d7071"; sg.textAlign = "left";
    sg.fillText(d.hi.toFixed(0) + " dB", W - P.r + 32, P.t + 6); sg.fillText(d.lo.toFixed(0) + " dB", W - P.r + 32, P.t + ph - 6);
    // average spectrum
    const [ac, ag] = canvas(W, 170);
    const ah = 170 - 12 - 40, mn = Math.min(...d.mean), mx = Math.max(...d.mean), span = Math.max(3, mx - mn);
    ag.strokeStyle = "#b7c4be"; ag.strokeRect(P.l + .5, 12.5, pw, ah);
    ag.beginPath(); ag.strokeStyle = "#0b7068"; ag.lineWidth = 1.5;
    d.mean.forEach((v, k) => { const x = P.l + (k + .5) * pw / d.nfft, y = 12 + ah - (v - mn + span * .08) / (span * 1.16) * ah; k ? ag.lineTo(x, y) : ag.moveTo(x, y); });
    ag.stroke();
    freqTicks(ag, P.l, P.l + pw, 12 + ah, halfMHz);
    ag.fillStyle = "#5d7071"; ag.textAlign = "right"; ag.textBaseline = "middle";
    ag.fillText(mx.toFixed(0) + " dB", P.l - 7, 12 + ah * .08); ag.fillText(mn.toFixed(0) + " dB", P.l - 7, 12 + ah * .92);
    ag.textAlign = "center"; ag.fillStyle = "#15292b"; ag.fillText("Average spectrum", P.l + pw / 2, 170 - 16);
    // histogram
    const [hc, hg] = canvas(360, 200);
    const hx = 16, hw = 328, hh = 150, max = Math.max(...d.hist, 1), bw = hw / 64;
    hg.fillStyle = "#f8e1dd"; hg.fillRect(hx, 10, bw, hh); hg.fillRect(hx + hw - bw, 10, bw, hh);
    d.hist.forEach((v, i) => {
      const bh = Math.max(v ? 1.5 : 0, (hh - 6) * v / max);
      hg.fillStyle = (i === 0 || i === 63) && v ? "#b3372e" : "#0b7068"; hg.fillRect(hx + i * bw + .5, 10 + hh - bh, bw - 1, bh);
    });
    hg.fillStyle = "#5d7071"; hg.textBaseline = "top";
    hg.textAlign = "left"; hg.fillText("−128", hx, 10 + hh + 6); hg.textAlign = "center"; hg.fillText("0", hx + hw / 2, 10 + hh + 6);
    hg.textAlign = "right"; hg.fillText("+127", hx + hw, 10 + hh + 6);
    hg.textAlign = "center"; hg.fillStyle = "#15292b"; hg.fillText("8-bit sample values (I and Q)", hx + hw / 2, 10 + hh + 24);

    body.innerHTML = `
      <div class="viewer-plots">
        <figure class="spec"><figcaption>Spectrogram, ${d.rows} slices from ${d.start_s.toFixed(1)} s to ${(d.start_s + d.span_s).toFixed(1)} s
          ${d.dropsKnown ? `<span class="verdict ${d.dropsShown ? "bad" : "good"}">${d.dropsShown ? d.dropsShown + " drop(s) marked in red" : "no drops in this range"}</span>` : `<span class="note">Use “Find drops” to mark drops here.</span>`}</figcaption></figure>
        <figure class="histo"><figcaption>Histogram <span class="verdict ${d.verdict[1]}">${d.verdict[0]}</span></figcaption>
          <dl class="stats">
            <div><dt>RMS</dt><dd>${d.rms.toFixed(1)} <i>of 127</i></dd></div>
            <div><dt>At full scale</dt><dd>${d.clipPct.toFixed(3)} %</dd></div>
            <div><dt>DC offset I / Q</dt><dd>${d.dcI.toFixed(2)} / ${d.dcQ.toFixed(2)}</dd></div>
            <div><dt>Gain</dt><dd>${d.gain_db != null ? d.gain_db + " dB" : "—"}</dd></div>
          </dl>
          <p class="note">Aim for a wide bell (RMS about 15–35) with nothing piled up in the red end bins.</p>
        </figure>
      </div>`;
    body.querySelector(".spec").append(sc, ac);
    body.querySelector(".histo").insertBefore(hc, body.querySelector(".histo dl"));
    view.canvases = [[sc, "spectrogram"], [ac, "spectrum"], [hc, "histogram"]];
  }

  function savePictures() {
    if (!view?.canvases) return H.toast("Draw the pictures first.", true);
    view.canvases.forEach(([c, kind]) => {
      const a = document.createElement("a");
      a.href = c.toDataURL("image/png");
      a.download = `${view.session}_${view.radio}_ch${view.n}_${kind}.png`;
      document.body.appendChild(a); a.click(); a.remove();
    });
  }

  // ---------------- wiring ----------------
  $("recsRefresh").addEventListener("click", () => load(true));
  window.addEventListener("heron-state", (e) => {
    const busy = !!(e.detail.locked || e.detail.test?.running);
    if (lastBusy !== null && busy !== lastBusy) { load(!busy ? false : true); }
    lastBusy = busy;
  });
  load(false);
})();
