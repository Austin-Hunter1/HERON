/* s09.js - Part 09: pseudorange and carrier models, term sizes, code-carrier
 * divergence, and dual-frequency combinations for L1/L5.
 * Typical ranges are textbook values (Misra & Enge; Kaplan & Hegarty). The
 * ionosphere and troposphere dots use simple models: thin-shell obliquity at
 * 350 km and a 1/sin(e)-style tropo mapping. Good for size, not for correction. */
G.section("s09", (root) => {
  const $ = (s) => root.querySelector(s);
  const v = G.controls($("#s09-ctl"), [
    { id: "s09-tec", key: "tec", label: "Vertical TEC", min: 2, max: 120, step: 1, value: 20, fmt: (x) => x + " TECU" },
    { id: "s09-el", key: "el", label: "Elevation", min: 5, max: 90, step: 1, value: 30, fmt: (x) => x + "°" },
    { id: "s09-f", key: "f", label: "Signal", type: "select", value: "l1", options: [["l1", "L1 / E1 / B1C"], ["l5", "L5 / E5a / B2a"]] },
    { id: "s09-iffree", key: "iff", label: "Apply the iono-free combination", type: "check", value: false },
  ], () => { drawBars(); drawPass(); drawIF(); });
  const obl = (el) => { const Re = 6371, H = 350, z = Math.asin(Re / (Re + H) * Math.cos(el * G.DEG)); return 1 / Math.cos(z); };
  const tropMap = (el) => 1.001 / Math.sqrt(0.002001 + Math.sin(el * G.DEG) ** 2);
  const iono = (tec, f) => 40.3 * tec * 1e16 / (f * f);
  const K_IF = 2.2606, K5 = 1.2606, AMP = Math.sqrt(K_IF * K_IF + K5 * K5);
  const barsC = $("#s09-bars");
  const drawBars = () => {
    const C = G.col, f = v.f === "l5" ? G.F_L5 : G.F_L1;
    const I = iono(v.tec, f) * obl(v.el), Tr = 2.4 * tropMap(v.el);
    const nf = v.iff ? AMP : 1;
    const terms = [
      { n: "Geometric range r", lo: 2.0e7, hi: 2.6e7, dot: 2.2e7, col: "ink" },
      { n: "Receiver clock c·δt_u", lo: 1, hi: 3e5, col: "refl", note: "solved as 4th unknown" },
      { n: "Satellite clock (raw)", lo: 1e2, hi: 3e5, col: "bds", note: "broadcast model removes it" },
      { n: "Satellite clock after model", lo: 0.5, hi: 2, col: "bds" },
      { n: "Orbit error (broadcast)", lo: 0.3, hi: 2, col: "bds" },
      { n: "Ionosphere I", lo: 1, hi: 50, dot: v.iff ? null : I, col: "gps", gone: v.iff },
      { n: "Troposphere T", lo: 2.3, hi: 25, dot: Tr, col: "gal" },
      { n: "Code multipath M_ρ", lo: 0.2 * nf, hi: 5 * nf, col: "warn" },
      { n: "Code noise ε_ρ", lo: 0.1 * nf, hi: 3 * nf, col: "ink2" },
      { n: "Carrier multipath M_Φ", lo: 0.001 * nf, hi: 0.05 * nf, col: "warn" },
      { n: "Carrier noise ε_Φ", lo: 0.0005 * nf, hi: 0.005 * nf, col: "ink2" },
    ];
    const p = G.plot(barsC, { x: [1e-4, 1e8], y: [-0.5, terms.length - 0.5], yticks: [], margin: { l: Math.min(205, barsC.clientWidth * 0.4), b: 34 }, xlabel: "metres (log)", xticks: [] });
    // log x: remap manually
    const LX = (m) => p.x0 + (Math.log10(m) + 4) / 12 * (p.x1 - p.x0);
    const c = p.ctx; p.ctx.save();
    // redraw x grid in log positions
    terms.forEach((t, i) => {
      const y = p.Y(terms.length - 1 - i), bh = Math.min(16, (p.y0 - p.y1) / terms.length * 0.55);
      c.fillStyle = C.ink2; c.font = `12px ${C.mono}`; c.textAlign = "right"; c.textBaseline = "middle"; c.fillText(t.n, p.x0 - 8, y);
      c.globalAlpha = t.gone ? 0.15 : 1;
      c.fillStyle = G.alpha(C[t.col].startsWith("#") ? C[t.col] : "#888888", 0.35); c.fillRect(LX(t.lo), y - bh / 2, Math.max(3, LX(t.hi) - LX(t.lo)), bh);
      c.strokeStyle = C[t.col]; c.lineWidth = 1.4; c.strokeRect(LX(t.lo), y - bh / 2, Math.max(3, LX(t.hi) - LX(t.lo)), bh);
      c.globalAlpha = 1;
      if (t.dot) { c.fillStyle = C[t.col]; c.beginPath(); c.arc(LX(t.dot), y, 5.5, 0, 7); c.fill(); c.strokeStyle = C.surface; c.lineWidth = 2; c.stroke(); }
      if (t.gone) { c.fillStyle = C.ok; c.textAlign = "left"; c.fillText("removed by L1/L5 combination", LX(t.hi) + 8, y); }
      else if (t.note) { c.fillStyle = C.ink3; c.textAlign = "left"; c.font = `11px ${C.mono}`; c.fillText(t.note, LX(t.hi) + 8, y); }
    });
    c.restore();
    // x ticks were placed linearly by Plot; overwrite the tick row with log ticks
    c.fillStyle = C.ink3; c.font = `11px ${C.mono}`; c.textAlign = "center"; c.textBaseline = "top";
    [1e-4, 1e-2, 1, 1e2, 1e4, 1e6, 1e8].forEach((m) => { c.fillText(["0.1mm", "1cm", "1m", "100m", "10km", "1e3km", "1e5km"][Math.round(Math.log10(m) / 2) + 2], LX(m), p.y0 + 4); c.strokeStyle = G.alpha(C.ink3, 0.2); c.beginPath(); c.moveTo(LX(m), p.y1); c.lineTo(LX(m), p.y0); c.stroke(); });
    G.readouts($("#s09-ro"), [
      ["Slant iono, L1", (iono(v.tec, G.F_L1) * obl(v.el)).toFixed(2) + " m"],
      ["Slant iono, L5", (iono(v.tec, G.F_L5) * obl(v.el)).toFixed(2) + " m"],
      ["Slant tropo", Tr.toFixed(2) + " m"],
      ["Obliquity / tropo map", obl(v.el).toFixed(2) + " / " + tropMap(v.el).toFixed(2)],
    ]);
  };
  G.mount(barsC, drawBars);

  /* ---------- pass: code vs carrier divergence ---------- */
  const passC = $("#s09-pass");
  const drawPass = () => {
    const C = G.col, f = G.F_L1, lam = G.LAM_L1, N = 7, ts = [], Ic = [], Ip = [], cmc = [], r = G.rng(3);
    for (let t = 0; t <= 360; t += 1) {
      const el = 10 + 70 * Math.sin(Math.PI * t / 360);
      const tecT = v.tec * (1 + 0.25 * Math.sin(2 * Math.PI * t / 300));
      const I = iono(tecT, f) * obl(el);
      ts.push(t / 60); Ic.push(I); Ip.push(-I); cmc.push(2 * I - lam * N + 0.6 * r.gauss() / Math.sqrt(Math.sin(el * G.DEG)));
    }
    const hi = Math.max(...cmc, ...Ic) * 1.1;
    const p = G.plot(passC, { x: [0, 6], y: [-hi * 0.6, hi], xlabel: "time (h)", ylabel: "metres" });
    p.hline(0, { color: G.col.ink3, width: 1 });
    p.pts(ts, cmc, { color: G.alpha(C.ink3, 0.9), r: 1.4 });
    p.line(ts, Ic, { color: C.gps, width: 2.2 }); p.line(ts, Ip, { color: C.refl, width: 2.2 });
    p.text(0.1, hi * 0.92, "code: +I", { color: C.gps }); p.text(0.1, -hi * 0.3, "carrier: −I", { color: C.refl }); p.text(5.9, -hi * 0.5, "dots: code − carrier = 2I − λN + noise", { color: C.ink2, align: "right", font: `11px ${C.mono}` });
  };
  G.mount(passC, drawPass);

  /* ---------- iono-free and geometry-free ---------- */
  const ifC = $("#s09-if"), gfC = $("#s09-gf");
  const v2 = G.controls($("#s09-ctl2"), [
    { id: "s09-s1", key: "s1", label: "Code noise σ at L1", min: 0.1, max: 3, step: 0.05, value: 1.0, fmt: (x) => x.toFixed(2) + " m" },
    { id: "s09-s5", key: "s5", label: "Code noise σ at L5", min: 0.05, max: 3, step: 0.05, value: 0.3, fmt: (x) => x.toFixed(2) + " m" },
  ], () => drawIF());
  const drawIF = () => {
    const C = G.col, el = v.el, I1 = iono(v.tec, G.F_L1) * obl(el), I5 = iono(v.tec, G.F_L5) * obl(el);
    const sIF = Math.hypot(K_IF * v2.s1, K5 * v2.s5);
    const items = [["L1", I1, v2.s1, C.gps], ["L5", I5, v2.s5, C.bds], ["IF", 0, sIF, C.ok]];
    const ymax = Math.max(I5, sIF * 3) * 1.15;
    const p = G.plot(ifC, { x: [-0.5, 2.5], y: [0, ymax], xticks: [], ylabel: "metres" });
    items.forEach(([n, I, s, col], i) => {
      const c = p.ctx, x0 = p.X(i - 0.32), x1 = p.X(i - 0.02), x2 = p.X(i + 0.02), x3 = p.X(i + 0.32);
      c.fillStyle = G.alpha(C.gps, 0.6); c.fillRect(x0, p.Y(I), x1 - x0, p.Y(0) - p.Y(I));
      c.fillStyle = G.alpha(C.ink3, 0.6); c.fillRect(x2, p.Y(3 * s), x3 - x2, p.Y(0) - p.Y(3 * s));
      p.text(i, 0, n, { dy: 12, align: "center", color: col, font: `600 12px ${C.mono}` });
    });
    p.text(-0.45, ymax * 0.95, "■ iono delay", { color: C.gps, font: `11px ${C.mono}` }); p.text(-0.45, ymax * 0.87, "■ 3σ noise", { color: C.ink3, font: `11px ${C.mono}` });
    // geometry-free: estimated TEC vs truth with noise
    const r = G.rng(8), xs = [], ys = [];
    for (let k = 0; k < 120; k++) { const n1 = r.gauss() * v2.s1, n5 = r.gauss() * v2.s5; const gf = (I5 - I1) + n5 - n1; xs.push(k); ys.push(gf / (1.7933 - 1) / (40.3e16 / (G.F_L1 * G.F_L1)) / obl(el)); }
    const q = G.plot(gfC, { x: [0, 120], y: [0, v.tec * 2.5 + 5], xlabel: "epoch", ylabel: "VTEC (TECU)" });
    q.pts(xs, ys, { color: C.accent, r: 1.8, alpha: 0.7 }); q.hline(v.tec, { color: C.ok, width: 2 });
    q.text(118, v.tec, "truth", { align: "right", dy: -10, color: C.ok });
    G.readouts($("#s09-ro2"), [
      ["Iono at L1 / L5", I1.toFixed(2) + " / " + I5.toFixed(2) + " m"],
      ["σ iono-free", sIF.toFixed(2) + " m"],
      ["Noise growth", (sIF / v2.s1).toFixed(2) + "× vs L1 alone"],
      ["Slant TEC σ from GF code", ((Math.hypot(v2.s1, v2.s5)) / (0.7933 * 0.1624)).toFixed(1) + " TECU σ"],
    ]);
  };
  G.mount(ifC, drawIF); G.mount(gfC, drawIF);
});
