/* s08.js - Part 08: BPSK/BOC/MBOC modulation, the HERON signal plan in
 * frequency, space (one school bus for scale) and time.
 * A chip pulse is a list of 12 equal segments, so every BOC(m,1) with m in
 * {1, 6} fits. The spectrum of a random code with pulse p is |P(f)|^2 / Tc,
 * computed exactly for a piecewise-constant pulse. Received powers are the
 * approximate ICD minimums; they set curve levels, not detection results. */
G.section("s08", (root) => {
  const $ = (s) => root.querySelector(s);
  const NSEG = 12;
  const P = {
    bpsk: Array(NSEG).fill(1),
    boc11: Array.from({ length: NSEG }, (_, k) => (k < 6 ? 1 : -1)),
    boc61: Array.from({ length: NSEG }, (_, k) => (k % 2 ? -1 : 1)),
  };
  const a11 = Math.sqrt(10 / 11), a61 = Math.sqrt(1 / 11);
  P.cbocP = P.boc11.map((v, k) => a11 * v + a61 * P.boc61[k]);
  P.cbocM = P.boc11.map((v, k) => a11 * v - a61 * P.boc61[k]);
  /** Unit-power PSD (1/Hz) of a random code with pulse p and chip rate rc, at f (Hz). */
  const psd1 = (p, rc, f) => {
    const Tc = 1 / rc, D = Tc / NSEG; let re = 0, im = 0, pw = 0;
    for (let k = 0; k < NSEG; k++) { const a = -2 * Math.PI * f * (k + 0.5) * D; re += p[k] * Math.cos(a); im += p[k] * Math.sin(a); pw += p[k] * p[k]; }
    const s = G.sinc(f * D); return (D * D * s * s * (re * re + im * im)) / (Tc * pw / NSEG);
  };
  /** Normalised ACF of a pulse at lag x (chips), |x| < 1. */
  const acf1 = (p, x) => {
    const N = NSEG * 40, m = Math.round(x * N); let s = 0, s0 = 0;
    for (let i = 0; i < N; i++) { const v = p[Math.floor(i / 40)]; s0 += v * v; const j = i + m; if (j >= 0 && j < N) s += v * p[Math.floor(j / 40)]; }
    return s / s0;
  };
  // Modulations: psd(f) and acf(x) for unit power; mix = weighted sums of components.
  const MOD = {
    bpsk1: { name: "BPSK(1)", rc: 1.023e6, mix: [[1, "bpsk"]], use: "GPS L1 C/A" },
    boc11: { name: "BOC(1,1)", rc: 1.023e6, mix: [[1, "boc11"]], use: "L1C-D, B1C-D" },
    boc61: { name: "BOC(6,1)", rc: 1.023e6, mix: [[1, "boc61"]], use: "MBOC high-frequency part" },
    cboc: { name: "CBOC(6,1,1/11), E1-B", rc: 1.023e6, mix: [[1, "cbocP"]], use: "Galileo E1-B (data)" },
    tmboc: { name: "TMBOC / QMBOC(6,1,4/33)", rc: 1.023e6, mix: [[29 / 33, "boc11"], [4 / 33, "boc61"]], use: "L1C-P, B1C-P (pilots)" },
    mboc: { name: "MBOC(6,1,1/11), whole signal", rc: 1.023e6, mix: [[10 / 11, "boc11"], [1 / 11, "boc61"]], use: "L1C, E1, B1C (data + pilot)" },
    bpsk10: { name: "BPSK(10)", rc: 10.23e6, mix: [[1, "bpsk"]], use: "GPS L5, Galileo E5a, BeiDou B2a" },
  };
  const psd = (m, f) => m.mix.reduce((s, [w, k]) => s + w * psd1(P[k], m.rc, f), 0);
  const acf = (m, x) => m.mix.reduce((s, [w, k]) => s + w * acf1(P[k], x), 0);

  const v = G.controls($("#s08-ctl"), [
    { id: "s08-mod", key: "mod", label: "Modulation", type: "select", value: "boc11", options: Object.entries(MOD).map(([k, m]) => [k, m.name]) },
    { id: "s08-cmp", key: "cmp", label: "Compare with BPSK(1)", type: "check", value: true },
  ], () => drawMod());
  const wC = $("#s08-wave"), pC = $("#s08-psd"), aC = $("#s08-acf");
  const code4 = [1, -1, -1, 1];
  const drawMod = () => {
    const m = MOD[v.mod], C = G.col;
    $("#s08-modname").textContent = m.name + " · " + m.use;
    // time waveform: 4 chips. TMBOC shows chip 0 as BOC(6,1), as in the L1C-P pattern.
    const p = G.plot(wC, { x: [0, 4], y: [-1.6, 1.6], xlabel: "time (chips)", ylabel: "s(t)", yticks: [-1, 0, 1], margin: { b: 34 } });
    const xs = [], ys = [], cs = [];
    for (let c = 0; c < 4; c++) {
      let pulse = P.bpsk;
      if (v.mod === "boc11") pulse = P.boc11; else if (v.mod === "boc61") pulse = P.boc61; else if (v.mod === "cboc" || v.mod === "mboc") pulse = P.cbocP;
      else if (v.mod === "tmboc") pulse = c === 0 ? P.boc61 : P.boc11;
      for (let k = 0; k < NSEG; k++) { xs.push(c + k / NSEG, c + (k + 1) / NSEG); ys.push(code4[c] * pulse[k], code4[c] * pulse[k]); }
      cs.push(c, c + 1);
    }
    p.line([0, 1, 1, 2, 2, 3, 3, 4], [1, 1, -1, -1, -1, -1, 1, 1].map((y, i) => code4[Math.floor(i / 2)] * 1.35), { color: G.alpha(C.ink3, 0.6), width: 1.2, dash: [3, 3] });
    p.line(xs, ys, { color: C.gps, width: 2 });
    p.text(4, 1.42, "dashed: code chip   solid: chip × subcarrier", { align: "right", color: C.ink3, font: `11px ${C.mono}` });
    // PSD
    const q = G.plot(pC, { x: [-15, 15], y: [-100, -55], xlabel: "frequency offset (MHz)", ylabel: "dB/Hz" });
    if (v.cmp) q.fn((f) => G.db(psd(MOD.bpsk1, f * 1e6)), { color: C.ink3, width: 1.4, dash: [4, 3] }, 900);
    q.fn((f) => G.db(psd(m, f * 1e6)), { color: C.gps, width: 2 }, 900);
    q.band(-12.5, 12.5, { fill: G.alpha(C.accent, 0.05) });
    // ACF
    const r = G.plot(aC, { x: [-1.2, 1.2], y: [-0.7, 1.05], xlabel: "lag (chips)", ylabel: "R" });
    r.hline(0, { color: C.ink3, width: 1 });
    if (v.cmp) r.fn((x) => acf(MOD.bpsk1, x), { color: C.ink3, width: 1.4, dash: [4, 3] }, 240);
    r.fn((x) => acf(m, x), { color: C.gps, width: 2.2 }, 240);
    // RMS bandwidth within the HERON front end (+/-12.5 MHz) and first zero of the ACF
    let s0 = 0, s2 = 0, inner = 0;
    for (let f = -12.5e6; f <= 12.5e6; f += 2.5e4) { const g = psd(m, f); s0 += g; s2 += f * f * g; if (Math.abs(f) < 1e6) inner += g; }
    let z = 1; for (let x = 0; x < 1; x += 0.005) if (acf(m, x) <= 0) { z = x; break; }
    G.readouts($("#s08-ro"), [
      ["Chip length", G.si(G.C / m.rc)],
      ["ACF first zero", (z).toFixed(2) + " chip = " + G.si(z * G.C / m.rc)],
      ["RMS bandwidth (±12.5 MHz)", (Math.sqrt(s2 / s0) / 1e6).toFixed(2) + " MHz"],
      ["Power within ±1 MHz", (100 * inner / s0).toFixed(0) + " %"],
    ]);
  };
  [wC, pC, aC].forEach((c) => G.mount(c, drawMod));

  /* ---------- HERON signal plan in frequency ---------- */
  const L1 = [
    { n: "GPS L1 C/A", col: "gps", pw: -158.5, m: "bpsk1", dash: [] },
    { n: "GPS L1C", col: "gps", pw: -157.0, m: "mboc", dash: [6, 3] },
    { n: "Galileo E1", col: "gal", pw: -157.0, m: "mboc", dash: [] },
    { n: "BeiDou B1C", col: "bds", pw: -159.0, m: "mboc", dash: [] },
  ];
  const L5 = [
    { n: "GPS L5", col: "gps", pw: -154.9, m: "bpsk10", dash: [] },
    { n: "Galileo E5a", col: "gal", pw: -155.0, m: "bpsk10", dash: [6, 3] },
    { n: "BeiDou B2a", col: "bds", pw: -156.0, m: "bpsk10", dash: [2, 3] },
  ];
  // Each signal has an "on" flag. Checkboxes under the plots set it, so the
  // presenter can build up the band one signal at a time.
  [...L1, ...L5].forEach((s) => (s.on = true));
  const l1C = $("#s08-l1"), l5C = $("#s08-l5");
  const drawBand = (cv, list) => {
    const C = G.col, p = G.plot(cv, { x: [-20, 20], y: [-250, -198], xlabel: "offset from centre (MHz)", ylabel: "dBW/Hz" });
    p.band(-12.5, 12.5, { fill: G.alpha(C.accent, 0.07) });
    p.hline(-204, { color: C.warn, dash: [6, 4], width: 1.6 }); p.text(19.5, -203, "noise floor kT, 290 K", { align: "right", base: "bottom", color: C.warn, font: `11px ${C.mono}` });
    list.filter((s) => s.on).forEach((s) => p.fn((f) => s.pw + G.db(psd(MOD[s.m], f * 1e6)), { color: C[s.col], width: 2, dash: s.dash }, 900));
  };
  const drawBands = () => { drawBand(l1C, L1); drawBand(l5C, L5); };
  const leg = $("#s08-leg");
  leg.innerHTML = [...L1, ...L5].map((s, i) => `<label><input type="checkbox" data-i="${i}" checked><i style="background:var(--${s.col})"></i>${s.n} · ${MOD[s.m].name.split(", ")[0]} · ${s.pw} dBW${s.dash.length ? " (dashed)" : ""}</label>`).join("");
  leg.addEventListener("change", (e) => { const i = +e.target.dataset.i; if (!isNaN(i)) { [...L1, ...L5][i].on = e.target.checked; drawBands(); } });
  G.mount(l1C, drawBands); G.mount(l5C, drawBands);

  /* ---------- space domain: school buses ---------- */
  const busC = $("#s08-bus");
  const rows = [
    { n: "School bus, for scale", m: 12.2, col: "refl", bus: true },
    { n: "Wavelength (L1 19 cm, L5 25 cm)", m: 0.2548, col: "ink2" },
    { n: "BOC(6,1) half-cycle", m: G.C / 1.023e6 / 12, col: "gal" },
    { n: "L5 · E5a · B2a chip", m: G.C / 10.23e6, col: "bds" },
    { n: "GNSS-R extra path (100 m, 45°)", m: 200 * Math.sin(45 * G.DEG), col: "warn" },
    { n: "BOC(1,1) half-cycle", m: G.C / 1.023e6 / 2, col: "gal" },
    { n: "L1 C/A · L1C · E1 · B1C chip", m: G.C / 1.023e6, col: "gps" },
  ];
  const drawBus = () => {
    const { ctx: c, w, h } = G.size(busC), C = G.col;
    // Each row: the name on a text line, the to-scale bar under it. The bar
    // spans the full width, so the scale stays readable on narrow screens.
    // One school bus in the first row is the only scale object: the other
    // rows give lengths in metres.
    const L = 12, R = w - 12, top = 6, rh = (h - top - 22) / rows.length, sc = (R - L) / 300;
    c.font = `12px ${C.mono}`;
    rows.forEach((r, i) => {
      const y = top + i * rh, bh = Math.min(20, rh - 22), yb = y + 17, len = r.m * sc;
      c.fillStyle = C.ink2; c.textAlign = "left"; c.textBaseline = "middle"; c.fillText(r.n, L, y + 8);
      if (r.bus) drawOneBus(c, L, yb, len, bh, C);
      else {
        c.fillStyle = G.alpha(C[r.col], 0.18); c.fillRect(L, yb, Math.max(2, len), bh);
        c.strokeStyle = C[r.col]; c.lineWidth = 1.5; c.strokeRect(L, yb, Math.max(2, len), bh);
      }
      c.fillStyle = C.ink; c.textAlign = "right"; c.fillText(G.si(r.m), R, y + 8);
    });
    c.fillStyle = C.ink3; c.textAlign = "left"; c.fillText("0", L, h - 10); c.textAlign = "right"; c.fillText("300 m", R, h - 10);
    G.readouts($("#s08-ro2"), [
      ["L1 C/A code period", "1 ms = 299.8 km"],
      ["E1 code period", "4 ms = 1199 km"],
      ["L1C / B1C code period", "10 ms = 2998 km"],
    ]);
  };
  function drawOneBus(c, x, y, w, h, C) {
    if (w < 3) return;
    c.fillStyle = C.refl; c.fillRect(x, y, w, h * 0.8);
    c.fillStyle = C.surface; const nw = Math.max(1, Math.floor(w / 4));
    for (let k = 0; k < nw; k++) c.fillRect(x + w * 0.08 + k * (w * 0.84 / nw), y + h * 0.15, w * 0.84 / nw * 0.7, h * 0.28);
    c.fillStyle = C.ink; const r = Math.max(1, h * 0.12);
    c.beginPath(); c.arc(x + w * 0.22, y + h * 0.82, r, 0, 7); c.arc(x + w * 0.78, y + h * 0.82, r, 0, 7); c.fill();
  }
  G.mount(busC, drawBus);

  /* ---------- time domain: 3 microseconds of each signal ---------- */
  const tC = $("#s08-time");
  const rr = G.rng(21);
  const sigs = [
    { n: "GPS L1 C/A", rc: 1.023e6, pulse: () => P.bpsk, col: "gps" },
    { n: "GPS L1C-P (TMBOC)", rc: 1.023e6, pulse: (k) => ([0, 4, 6, 29].includes(k % 33) ? P.boc61 : P.boc11), col: "gps" },
    { n: "Galileo E1-C (CBOC−)", rc: 1.023e6, pulse: () => P.cbocM, col: "gal" },
    { n: "BeiDou B1C-P (QMBOC, I)", rc: 1.023e6, pulse: () => P.boc11, col: "bds" },
    { n: "GPS L5-Q", rc: 10.23e6, pulse: () => P.bpsk, col: "gps" },
    { n: "Galileo E5a-Q", rc: 10.23e6, pulse: () => P.bpsk, col: "gal" },
    { n: "BeiDou B2a-P", rc: 10.23e6, pulse: () => P.bpsk, col: "bds" },
  ].map((s) => Object.assign(s, { code: Int8Array.from({ length: 64 }, () => (rr() < 0.5 ? -1 : 1)) }));
  const drawTime = () => {
    const C = G.col, n = sigs.length, span = 3e-6;
    const p = G.plot(tC, { x: [0, 3], y: [-0.5, n - 0.5], xlabel: "time (µs)", yticks: [], margin: { l: 190 } });
    sigs.forEach((s, i) => {
      const yc = n - 1 - i, xs = [], ys = [], nch = Math.ceil(span * s.rc);
      for (let k = 0; k < nch; k++) { const pl = s.pulse(k); for (let q = 0; q < NSEG; q++) { const t0 = (k + q / NSEG) / s.rc * 1e6, t1 = (k + (q + 1) / NSEG) / s.rc * 1e6; const y = yc + 0.32 * s.code[k] * pl[q]; xs.push(t0, t1); ys.push(y, y); } }
      p.hline(yc, { color: G.alpha(C.ink3, 0.25), width: 1 });
      p.line(xs, ys, { color: C[s.col], width: 1.5 });
      p.text(0, yc, s.n, { dx: -184, color: C.ink2, font: `12px ${C.mono}` });
    });
  };
  G.mount(tC, drawTime);
});
