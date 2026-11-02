// "Automated tests" panel of the Payload Setup page: the gain sweep and the buffer sweep (sweeps.py).
// The NUC runs the trials; this page starts them, shows progress from /api/state, and offers "Use" buttons
// that copy a result into the setup being edited (the operator then saves and loads it).
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const H = window.HERON;
  const esc = H.esc;
  let sweep = null, lastKey = "";

  const list = (text) => text.split(/[,\s]+/).map((v) => v.trim()).filter(Boolean).map(Number);
  const pct = (f) => f == null ? "—" : (100 * f).toFixed(f < 0.001 ? 4 : 2) + " %";
  const clock = (s) => { s = Math.max(0, Math.round(s)); return s < 60 ? s + " s" : Math.floor(s / 60) + " min " + String(s % 60).padStart(2, "0") + " s"; };

  function bufferParams() {
    const modes = [["bDirect", "direct"], ["bThreaded", "threaded"]].filter(([id]) => $(id).checked).map(([, m]) => m);
    // One USB-buffer entry per line; "-" (or an empty line) keeps the setup's own value.
    const args = $("bArgs").value.split("\n").map((l) => l.trim()).map((l) => (l === "-" ? "" : l));
    const unique = [...new Set(args.length ? args : [""])];
    return { write_modes: modes, spb: list($("bSpb").value), device_args: unique, file_buffer_bytes: list($("bFile").value),
      seconds: Number($("bSeconds").value), keep: $("bKeep").checked };
  }

  function estimate() {
    const g = list($("gGains").value).length, gs = Number($("gSeconds").value) || 0;
    $("gEstimate").textContent = g ? `${g} coarse + 1 predicted + a few fine trials, about ${clock((g + 4) * (gs + 6))}.` : "";
    const b = bufferParams();
    const n = b.write_modes.length * b.spb.length * b.device_args.length * b.file_buffer_bytes.length;
    $("bEstimate").textContent = n ? `${n} trial(s), about ${clock(n * ((b.seconds || 0) + 6))}.` : "";
  }

  async function start(kind) {
    const params = kind === "gain"
      ? { gains: list($("gGains").value), seconds: Number($("gSeconds").value), fine_step_db: Number($("gFine").value),
          target_sigma_multiple: Number($("gTarget").value), keep: $("gKeep").checked }
      : bufferParams();
    try { sweep = (await H.api("/api/sweep/" + kind, { params })).sweep; render(); }
    catch (e) { H.toast(e.message, true); }
  }

  function render() {
    const s = sweep, running = !!s?.running, state = H.state;
    const blocked = running || !state || state.locked || state.test?.running || state.status?.state !== "READY";
    $("runGain").disabled = blocked; $("runBuffer").disabled = blocked;
    $("stopSweep").classList.toggle("hidden", !running);
    const st = $("sweepStatus");
    if (running) {
      const total = Math.max(s.trials || 1, s.trial || 0);
      st.innerHTML = `<p class="note"><b>${s.kind === "gain" ? "Gain" : "Buffer"} sweep</b>: trial ${s.trial} of about ${total} · ${clock(s.elapsed_s)}<br>${esc(s.step || "")}</p>
        <div class="progress"><i style="width:${Math.min(100, 100 * Math.max(0, s.trial - 1) / total)}%"></i></div>`;
    } else if (state && state.status?.state !== "READY" && !state.locked) {
      st.innerHTML = `<p class="note">Load a setup onto the payload first. Sweeps use the loaded setup.</p>`;
    } else st.innerHTML = s?.error ? `<p class="warn">${esc(s.error)}</p>` : "";
    // Draw results only when they change, so the buttons stay clickable between polls.
    const key = JSON.stringify([s?.kind, s?.result, s?.saved]);
    if (key === lastKey) return;
    lastKey = key;
    const box = $("sweepResult");
    if (!s?.result) { box.innerHTML = ""; return; }
    box.innerHTML = (s.kind === "gain" ? gainHtml(s) : bufferHtml(s)) +
      (s.saved ? `<p class="note">Saved on the NUC: <code>${esc(s.saved)}</code></p>` : "");
    box.querySelectorAll("[data-gain]").forEach((b) => b.addEventListener("click", () => useGains(s, b.dataset.gain)));
    box.querySelectorAll("[data-buffer]").forEach((b) => b.addEventListener("click", () => useBuffer(s, Number(b.dataset.buffer))));
  }

  function gainHtml(s) {
    const r = s.result, best = r.best || [];
    const cards = best.map((b, i) => `<div class="sweep-best">
        <b>${esc(b.label)}</b>
        ${b.gain == null ? `<span class="verdict clipping">No usable gain</span>`
          : `<span>${b.gain} dB · ${b.sigma_multiple ?? "—"} σ to full scale · ${b.clip_pct} % clipped (${esc(b.phase)})</span>
             <button type="button" class="btn quiet" data-gain="${i}">Use ${b.gain} dB</button>`}
      </div>`).join("");
    const rows = (r.trials || []).map((t) => `<tr class="${best.some((b) => b.radio === t.radio && b.index === t.index && b.gain === t.gain && b.phase === t.phase) ? "on" : ""}">
        <td>${esc(t.phase)}</td><td>${esc(t.label)}</td><td>${t.gain}</td>
        <td>${t.sigma_multiple ?? "—"}</td><td>${pct(t.clip_fraction)}</td><td>${t.score == null ? "—" : t.score.toFixed(4)}</td>
        <td>${t.error ? `<span class="bad">${esc(t.error)}</span>` : ""}</td></tr>`).join("");
    return `${best.length && !s.running ? `<div class="sweep-bests">${cards}</div>
        ${best.filter((b) => b.gain != null).length > 1 ? `<button type="button" class="btn" data-gain="all">Use all best gains</button>` : ""}` : ""}
      <p class="note">Target: ${s.params?.target_sigma_multiple ?? 4.5} σ to full scale (σ from the 16th–84th percentile), no clipping. Best = highest score.</p>
      <div class="sweep-scroll"><table class="sweep-table"><thead><tr><th>Phase</th><th>Channel</th><th>Gain dB</th><th>σ to FS</th><th>Clipped</th><th>Score</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  }

  function bufferHtml(s) {
    const r = s.result;
    const rows = (r.trials || []).map((t) => `<tr class="${t.n === r.recommended ? "on" : ""}">
        <td>${t.n}</td><td>${esc(t.write_mode)}</td><td>${t.spb}</td><td><code>${esc(t.device_args || "as set up")}</code></td><td>${t.file_buffer_bytes}</td>
        <td>${t.overflows ?? "—"}</td><td>${t.gaps ?? "—"}</td><td>${t.drops ?? "—"}</td><td>${t.missing_samples ?? "—"}</td><td>${t.mbps ?? "—"}</td>
        <td>${t.clean ? `<span class="verdict good">clean</span>` : `<span class="verdict clipping">${esc(t.error || "loss")}</span>`}</td>
        <td>${t.clean && !s.running ? `<button type="button" class="btn quiet" data-buffer="${t.n}">Use</button>` : ""}</td></tr>`).join("");
    const rec = (r.trials || []).find((t) => t.n === r.recommended);
    return `${s.running ? "" : rec ? `<p class="note">Suggested: trial ${rec.n} (the collect wizard's direct method first, then the smallest clean buffers).</p>`
        : `<p class="warn">No trial was clean.</p>`}
      <div class="sweep-scroll"><table class="sweep-table"><thead><tr><th>#</th><th>Write</th><th>spb</th><th>USB buffers</th><th>File buffer</th>
        <th>Overflows</th><th>Gaps</th><th>Drops</th><th>Missing</th><th>MB/s</th><th></th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="note">“Drops” and “Missing” come from samples.jsonl (the same scan as “Find drops”); simulated radios have none.</p>`;
  }

  function useGains(s, which) {
    const best = (s.result.best || []).filter((b, i) => b.gain != null && (which === "all" || i === Number(which)));
    let n = 0;
    H.editDraft((draft) => {
      for (const b of best) {
        const ch = draft.radios.find((r) => r.id === b.radio)?.channels.find((c) => c.index === b.index);
        if (ch) { ch.gain_db = b.gain; n++; }
      }
    });
    H.toast(n ? `Set ${n} gain(s). Save the setup and load it onto the payload.` : "Those channels are not in the setup being edited.", !n);
  }

  function useBuffer(s, n) {
    const t = s.result.trials.find((x) => x.n === n);
    if (!t) return;
    H.editDraft((draft) => {
      draft.write_mode = t.write_mode; draft.spb = t.spb; draft.file_buffer_bytes = t.file_buffer_bytes;
      if (t.device_args) draft.device_args = t.device_args;
    });
    H.toast(`Buffer settings of trial ${n} copied. Save the setup and load it onto the payload.`);
  }

  $("runGain").addEventListener("click", () => start("gain"));
  $("runBuffer").addEventListener("click", () => start("buffer"));
  $("stopSweep").addEventListener("click", async () => {
    try { sweep = (await H.api("/api/sweep-stop", {})).sweep; render(); } catch (e) { H.toast(e.message, true); }
  });
  ["gGains", "gSeconds", "bSpb", "bArgs", "bFile", "bSeconds", "bDirect", "bThreaded"].forEach((id) =>
    $(id).addEventListener(id.startsWith("bD") || id.startsWith("bT") ? "change" : "input", estimate));
  window.addEventListener("heron-state", (e) => { sweep = e.detail.sweep || null; render(); });
  estimate();
})();
