/* s06.js - Part 06: acquisition search space.
 * Cell statistic model: coherent output z = A*tri(dtau)*sinc(df*T)*e^{j phi} + n,
 * n ~ CN(0,1), A^2 = (C/N0)*T. K coherent powers are summed (non-coherent).
 * For a cell that is shown as a max over several real cells (zoomed-out view),
 * the noise-only maximum is drawn from the exact distribution of the max of
 * M Gamma(K,1) variables, so the picture keeps correct statistics. */
G.section("s06", (root) => {
  const c3 = root.querySelector("#s06-3d"), ch = root.querySelector("#s06-heat");
  const v = G.controls(root.querySelector("#s06-ctl"), [
    { id: "s06-cn0", key: "cn0", label: "C/N0", min: 25, max: 50, step: 0.5, value: 43, fmt: (x) => x.toFixed(1) + " dB-Hz" },
    { id: "s06-t", key: "T", label: "Coherent time T", min: 1, max: 20, step: 1, value: 1, fmt: (x) => x + " ms" },
    { id: "s06-k", key: "K", label: "Non-coherent sums K", min: 1, max: 20, step: 1, value: 4, fmt: (x) => x + "" },
    { id: "s06-pfa", key: "pfa", label: "Total false-alarm P_fa", min: -6, max: -1, step: 0.5, value: -3, fmt: (x) => "1e" + x },
    { id: "s06-tau0", key: "tau", label: "True code delay", min: 10, max: 1012, step: 0.01, value: 612.37, fmt: (x) => x.toFixed(2) + " chips" },
    { id: "s06-fd", key: "fd", label: "True Doppler", min: -5000, max: 5000, step: 10, value: 1730, fmt: (x) => x + " Hz" },
  ], () => compute());
  const st3 = {}; let full = false, seed = 1, grid = null;
  const NCODE = 2046;               // half-chip code bins over 1023 chips

  const compute = () => {
    const TAU0 = v.tau;             // true code delay, chips (slider)
    const T = v.T * 1e-3, K = v.K, df = 1 / (2 * T), nD = 2 * Math.round(5000 / df) + 1;
    const A2 = G.lin(v.cn0) * T, A = Math.sqrt(A2);
    const cells = NCODE * nD, pcell = 1 - Math.pow(1 - Math.pow(10, v.pfa), 1 / cells), thr = G.gammaQinv(K, pcell);
    const r = G.rng(seed);
    // window of cells to display
    const iD0 = Math.round((v.fd + 5000) / df);             // nearest Doppler bin
    const iC0 = Math.round(TAU0 * 2);
    let rows, cols, colPool, rowPool, c0, r0;
    if (full) { cols = 128; colPool = NCODE / cols; c0 = 0; rows = Math.min(nD, 41); rowPool = nD / rows; r0 = 0; }
    else { cols = 33; colPool = 1; c0 = iC0 - 16; rows = Math.min(nD, 31); rowPool = 1; r0 = Math.max(0, Math.min(nD - rows, iD0 - (rows >> 1))); }
    const Z = [];
    for (let ri = 0; ri < rows; ri++) {
      const row = [];
      for (let ci = 0; ci < cols; ci++) {
        // the real cells this display cell covers
        const dA = Math.floor(r0 + ri * rowPool), dB = Math.max(dA + 1, Math.floor(r0 + (ri + 1) * rowPool));
        const cA = Math.floor(c0 + ci * colPool), cB = Math.max(cA + 1, Math.floor(c0 + (ci + 1) * colPool));
        const M = (dB - dA) * (cB - cA);
        // noise-only maximum of M cells
        let best = G.gammaQinv(K, 1 - Math.pow(r(), 1 / M));
        // the signal cell(s) inside this block: simulate them explicitly
        for (let d = dA; d < dB; d++) for (let cc = cA; cc < cB; cc++) {
          const dt = Math.abs(((cc / 2 - TAU0) % 1023 + 1023 + 511.5) % 1023 - 511.5);
          if (dt >= 1) continue;
          const fd = -5000 + d * df, g = G.tri(dt) * G.sinc((fd - v.fd) * T);
          let s = 0; for (let k = 0; k < K; k++) { const ph = r() * 6.283; const re = A * g * Math.cos(ph) + r.gauss() * Math.SQRT1_2, im = A * g * Math.sin(ph) + r.gauss() * Math.SQRT1_2; s += re * re + im * im; }
          best = Math.max(best, s);
        }
        row.push(best);
      }
      Z.push(row);
    }
    // peak cell (true best bin) for detection
    let pk = 0, pr = 0, pc = 0; Z.forEach((row, ri) => row.forEach((z, ci) => { if (z > pk) { pk = z; pr = ri; pc = ci; } }));
    const peakIsTrue = Math.abs((c0 + (pc + 0.5) * colPool) / 2 - TAU0) < (full ? colPool : 1.5) && Math.abs(-5000 + (r0 + (pr + 0.5) * rowPool) * df - v.fd) < df * (full ? rowPool : 1.5);
    // Monte Carlo detection probability for the best cell (closest bins)
    const dtb = Math.abs(iC0 / 2 - TAU0), dfb = Math.abs(-5000 + iD0 * df - v.fd), gb = G.tri(dtb) * G.sinc(dfb * T);
    let hits = 0; const rr = G.rng(99);
    for (let n = 0; n < 400; n++) { let s = 0; for (let k = 0; k < K; k++) { const re = A * gb + rr.gauss() * Math.SQRT1_2, im = rr.gauss() * Math.SQRT1_2; s += re * re + im * im; } if (s > thr) hits++; }
    grid = { Z, rows, cols, c0, r0, colPool, rowPool, df, nD, thr, pk, K, T, cells, peakIsTrue, A2, gb, pd: hits / 400 };
    draw(); drawSlices();
  };

  const draw = () => {
    if (!grid) return;
    const g = grid, zmax = Math.max(g.thr * 1.3, g.pk * 1.05);
    const xa = (g.c0) / 2, xb = (g.c0 + g.cols * g.colPool) / 2;
    const ya = -5000 + g.r0 * g.df, yb = -5000 + (g.r0 + g.rows * g.rowPool) * g.df;
    G.surface(c3, g.Z, { zr: [0, zmax], thr: g.thr, state: st3, xlabel: "code delay (chips)", ylabel: "Doppler (Hz)", zlabel: "S", xr: [xa, xb], yr: [ya, yb], redraw: draw });
    const p = G.plot(ch, { x: [xa, xb], y: [ya, yb], xlabel: "code delay (chips)", ylabel: "Doppler (Hz)", margin: { l: 60 } });
    G.heat(p, g.Z, [xa, xb], [ya, yb], [0, zmax]);
    // outline cells above threshold
    const c = p.ctx; c.strokeStyle = G.col.surface; c.lineWidth = 2;
    g.Z.forEach((row, ri) => row.forEach((z, ci) => { if (z > g.thr) { const x0 = p.X(xa + ci * (xb - xa) / g.cols), x1 = p.X(xa + (ci + 1) * (xb - xa) / g.cols), y0 = p.Y(ya + ri * (yb - ya) / g.rows), y1 = p.Y(ya + (ri + 1) * (yb - ya) / g.rows); c.strokeRect(x0, y1, x1 - x0, y0 - y1); } }));
    p.axes();
    root.querySelector("#s06-win").textContent = full ? "all 1023 chips (cells max-pooled)" : "±8 chips around the peak";
    const det = g.pk > g.thr;
    G.readouts(root.querySelector("#s06-ro"), [
      ["Doppler bin", g.df.toFixed(0) + " Hz"],
      ["Cells searched", g.cells.toLocaleString("en-US")],
      ["Coherent SNR", G.db(g.A2).toFixed(1) + " dB"],
      ["Threshold γ", G.db(g.thr / g.K).toFixed(1) + " dB over noise"],
      ["Result", det ? (g.peakIsTrue ? "DETECTED" : "FALSE PEAK") : "MISSED", det && g.peakIsTrue ? "good" : "bad"],
      ["P_d of best cell", (100 * g.pd).toFixed(0) + " %", g.pd > 0.9 ? "good" : g.pd < 0.5 ? "bad" : ""],
      ["Total dwell", (g.T * g.K * 1e3).toFixed(0) + " ms"],
      ["Straddle loss here", G.db(g.gb * g.gb).toFixed(1) + " dB"],
    ]);
  };

  /* ---------- coarse vs fine slices ---------- */
  const cd = root.querySelector("#s06-dop"), cc = root.querySelector("#s06-code");
  const v2 = G.controls(root.querySelector("#s06-ctl2"), [
    { id: "s06-tf", key: "Tf", label: "Fine coherent time", min: 2, max: 20, step: 1, value: 10, fmt: (x) => x + " ms" },
    { id: "s06-tau", key: "frac", label: "True code offset", min: -0.5, max: 0.5, step: 0.01, value: 0.18, fmt: (x) => x.toFixed(2) + " chip" },
  ], () => drawSlices());
  const drawSlices = () => {
    const Tc = v.T * 1e-3, Tf = v2.Tf * 1e-3, dfc = 1 / (2 * Tc), dff = 1 / (2 * Tf);
    const span = Math.max(1.5 / Tc, 300);
    const p = G.plot(cd, { x: [-span, span], y: [0, 1.08], xlabel: "Doppler error (Hz)", ylabel: "power" });
    p.fn((f) => Math.pow(G.sinc(f * Tc), 2), { color: G.col.ink3, width: 2 });
    p.fn((f) => Math.pow(G.sinc(f * Tf), 2), { color: G.col.accent, width: 2.2 });
    for (let k = -6; k <= 6; k++) { p.vline(k * dfc + 37, { color: G.alpha(G.col.ink3, 0.5), dash: [2, 3] }); }
    const lg = p.ctx; lg.font = `11px ${G.col.mono}`; lg.fillStyle = G.col.ink3; lg.fillText(`coarse T=${v.T} ms, bins ${dfc.toFixed(0)} Hz`, p.x0 + 6, p.y1 + 12);
    lg.fillStyle = G.col.accent; lg.fillText(`fine T=${v2.Tf} ms, bins ${dff.toFixed(0)} Hz`, p.x0 + 6, p.y1 + 26);
    // code slice: samples every half chip, parabola through three
    const fr = v2.frac, ys = [-1, 0, 1].map((k) => G.tri(k * 0.5 - fr));
    const delta = (ys[0] - ys[2]) / (2 * (ys[0] - 2 * ys[1] + ys[2])) * 0.5;
    const q = G.plot(cc, { x: [-1.6, 1.6], y: [0, 1.1], xlabel: "code delay from nearest bin (chips)", ylabel: "R" });
    q.fn((x) => G.tri(x - fr), { color: G.col.gps, width: 2 });
    const a = (ys[0] + ys[2] - 2 * ys[1]) / 2 / 0.25, b = (ys[2] - ys[0]) / 2 / 0.5;
    q.fn((x) => ys[1] + b * x + a * x * x, { color: G.col.refl, width: 1.8, dash: [6, 4] });
    [-1, 0, 1].forEach((k, i) => q.dot(k * 0.5, ys[i], { color: G.col.gps }));
    q.vline(fr, { color: G.col.ok, dash: [4, 4] }); q.vline(delta, { color: G.col.refl });
    G.readouts(root.querySelector("#s06-ro2"), [
      ["Fine Doppler bin", dff.toFixed(0) + " Hz"],
      ["Parabola estimate", delta.toFixed(3) + " chip"],
      ["Fit error", ((delta - fr) * 293).toFixed(1) + " m", Math.abs(delta - fr) < 0.05 ? "good" : ""],
    ]);
  };

  root.querySelector("#s06-redo").addEventListener("click", () => { seed++; compute(); });
  const fb = root.querySelector("#s06-full");
  fb.addEventListener("click", () => { full = !full; fb.classList.toggle("on", full); fb.textContent = full ? "Zoom to the peak" : "Show whole code range"; compute(); });
  G.mount(c3, draw); G.mount(ch, draw); G.mount(cd, drawSlices); G.mount(cc, drawSlices);
  compute();
  if (G.data) {
    const f = G.data.figures;
    [["#s06-img-dwell", "acq_dwell_l1ca"], ["#s06-img-slices", "acq_slices_l1ca"], ["#s06-img-peaks", "acq_peaks_l1ca"]].forEach(([sel, k]) => { if (f[k]) root.querySelector(sel).src = f[k]; });
  }
});
