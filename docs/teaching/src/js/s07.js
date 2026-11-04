/* s07.js - Part 07: tracking loops (DLL, FLL, PLL), a live closed-loop
 * simulation, and the HERON tracking data viewer.
 * Notation follows Morton et al., Ch. 15: delta-tau = tau_hat - tau, early tap
 * Z_E = alpha R(dtau - d), late tap Z_L = alpha R(dtau + d).
 * Noise model: each correlator output has CN(0,1) noise; signal amplitude
 * alpha = sqrt(C/N0 * T), so the coherent SNR is C/N0 * T. */
G.section("s07", (root) => {
  const $ = (s) => root.querySelector(s);
  const C_ = () => G.col;
  const cn = (r) => [r.gauss() * Math.SQRT1_2, r.gauss() * Math.SQRT1_2];
  const mag = (z) => Math.hypot(z[0], z[1]);
  const Rbpsk = (x) => G.tri(x);
  const Rboc = (x) => { const a = Math.abs(x); return a <= 0.5 ? 1 - 3 * a : a <= 1 ? a - 1 : 0; };

  /* ============================== DLL ============================== */
  const dllR = $("#s07-dll-r"), dllS = $("#s07-dll-s");
  const vd = G.controls($("#s07-dll-ctl"), [
    { id: "s07-dt", key: "dt", label: "Code error δτ", min: -1.5, max: 1.5, step: 0.01, value: 0.2, fmt: (x) => x.toFixed(2) + " chip" },
    { id: "s07-d", key: "d", label: "Tap offset d", min: 0.05, max: 1, step: 0.05, value: 0.5, fmt: (x) => x.toFixed(2) + " chip" },
    { id: "s07-disc", key: "disc", label: "Discriminator", type: "select", value: "4", options: [["1", "15.24a  E − L (coherent)"], ["2", "15.24b  |E|² − |L|²"], ["3", "15.24c  |E| − |L|"], ["4", "15.24d  (|E|−|L|)/(|E|+|L|)"], ["5", "15.24e  dot product"], ["6", "15.24f  (|E|−|L|)/|P|"]] },
    { id: "s07-mod", key: "mod", label: "Modulation", type: "select", value: "bpsk", options: [["bpsk", "BPSK (L1 C/A, L5, E5a, B2a)"], ["boc", "BOC(1,1) (L1C, E1, B1C data)"]] },
    { id: "s07-dcn0", key: "cn0", label: "C/N0", min: 25, max: 50, step: 1, value: 40, fmt: (x) => x + " dB-Hz" },
    { id: "s07-dT", key: "T", label: "Integration T", min: 1, max: 20, step: 1, value: 1, fmt: (x) => x + " ms" },
    { id: "s07-dB", key: "B", label: "DLL bandwidth B_n", min: 0.1, max: 5, step: 0.1, value: 1, fmt: (x) => x.toFixed(1) + " Hz" },
  ], () => drawDll());
  const discs = {
    1: (E, P, L, a) => (E[0] - L[0]) / a,
    2: (E, P, L, a) => (mag(E) ** 2 - mag(L) ** 2) / (a * a),
    3: (E, P, L, a) => (mag(E) - mag(L)) / a,
    4: (E, P, L) => (mag(E) - mag(L)) / (mag(E) + mag(L)),
    5: (E, P, L, a) => ((E[0] - L[0]) * P[0] + (E[1] - L[1]) * P[1]) / (a * a),
    6: (E, P, L) => (mag(E) - mag(L)) / mag(P),
  };
  let dseed = 1;
  const drawDll = () => {
    const R = vd.mod === "boc" ? Rboc : Rbpsk, d = vd.d, dt = vd.dt, C = C_();
    const p = G.plot(dllR, { x: [-2, 2], y: [-0.6, 1.1], xlabel: "replica delay − true delay (chips)", ylabel: "R" });
    p.fn((x) => R(x), { color: C.ink3, width: 2 });
    // taps: E at dt - d, P at dt, L at dt + d (on the R curve)
    [[dt - d, "E", C.gps], [dt, "P", C.ink], [dt + d, "L", C.bds]].forEach(([x, n, col]) => {
      p.line([x, x], [0, R(x)], { color: col, width: 2 }); p.dot(x, R(x), { color: col }); p.text(x, R(x), n, { dy: -14, align: "center", color: col });
    });
    const f = discs[vd.disc], a = 1;
    // Normalised forms divide by zero where all taps are off the peak; show a gap there.
    const curve = (x) => { const y = f([R(x - d), 0], [R(x), 0], [R(x + d), 0], a); return isFinite(y) ? y : null; };
    let ymax = 0.2; for (let x = -1.6; x <= 1.6; x += 0.02) { const y = curve(x); if (y != null) ymax = Math.max(ymax, Math.min(5, Math.abs(y))); }
    const q = G.plot(dllS, { x: [-1.6, 1.6], y: [-ymax * 1.3, ymax * 1.3], xlabel: "δτ (chips)", ylabel: "l_τ" });
    q.band(-Math.min(d, vd.mod === "boc" ? 0.25 : 1), Math.min(d, vd.mod === "boc" ? 0.25 : 1));
    q.hline(0, { color: C.ink3 });
    // noisy outputs at the operating point
    const alpha = Math.sqrt(G.lin(vd.cn0) * vd.T * 1e-3), r = G.rng(dseed);
    const xs = [], ys = [];
    for (let k = 0; k < 120; k++) {
      const n1 = cn(r), n2 = cn(r), n3 = cn(r);
      const E = [alpha * R(dt - d) + n1[0], n1[1]], P = [alpha * R(dt) + n2[0], n2[1]], L = [alpha * R(dt + d) + n3[0], n3[1]];
      xs.push(dt + (r() - 0.5) * 0.03); ys.push(f(E, P, L, alpha));
    }
    // Each dot is the discriminator output of one integration period T
    // with noise. The cloud shows what the loop filter must average. The
    // bar shows mean ± 1 sigma, so the size of the noise reads at a glance.
    const fin = ys.filter((y) => isFinite(y));
    let m = 0, s2 = 0; fin.forEach((y) => (m += y)); m /= fin.length || 1; fin.forEach((y) => (s2 += (y - m) ** 2));
    const sd = Math.sqrt(s2 / (fin.length || 1));
    q.pts(xs, ys.map((y) => (isFinite(y) ? y : null)), { color: C.accent, r: 2.2, alpha: 0.5 });
    q.fn(curve, { color: C.gps, width: 2.2 });
    if (fin.length) {
      const yl = Math.max(-ymax * 1.3, m - sd), yh = Math.min(ymax * 1.3, m + sd), cap = 0.06;
      q.line([dt, dt], [yl, yh], { color: C.warn, width: 2.5 });
      q.line([dt - cap, dt + cap], [yl, yl], { color: C.warn, width: 2.5 }); q.line([dt - cap, dt + cap], [yh, yh], { color: C.warn, width: 2.5 });
    }
    if (curve(dt) != null) q.dot(dt, curve(dt), { color: C.warn });
    const right = dt < 0.4;
    q.text(right ? -1.55 : 1.55, ymax * 1.15, `● ${xs.length} noisy outputs, one per T`, { align: right ? "left" : "right", color: C.accent, font: `600 11px ${C.mono}`, halo: true });
    q.text(right ? -1.55 : 1.55, ymax * 0.92, "┃ mean ± 1σ", { align: right ? "left" : "right", color: C.warn, font: `600 11px ${C.mono}`, halo: true });
    // statistics
    const cn0 = G.lin(vd.cn0), T = vd.T * 1e-3, D = 2 * d;
    const sEpoch = 0.5 * Math.sqrt(1 / (T * cn0));
    const sLoop = Math.sqrt(vd.B * D / (2 * cn0) * (1 + 2 / (T * cn0 * Math.max(0.05, 2 - D))));
    G.readouts($("#s07-dll-ro"), [
      ["l_τ (noise-free)", curve(dt) == null ? "undefined" : curve(dt).toFixed(3)],
      ["1σ of one-epoch l_τ", sd.toFixed(3)],
      ["σ one epoch, L1 C/A", (sEpoch * 293.05).toFixed(1) + " m"],
      ["σ loop, L1 C/A", (sLoop * 293.05).toFixed(2) + " m"],
      ["σ loop, L5 / B2a", (sLoop * 29.305).toFixed(2) + " m"],
    ]);
  };
  dllS.addEventListener("click", () => { dseed++; drawDll(); });
  G.mount(dllR, drawDll); G.mount(dllS, drawDll);

  /* ============================== FLL ============================== */
  const fllP = $("#s07-fll-p"), fllS = $("#s07-fll-s");
  const vf = G.controls($("#s07-fll-ctl"), [
    { id: "s07-df", key: "df", label: "True frequency error", min: -800, max: 800, step: 5, value: 120, fmt: (x) => x + " Hz" },
    { id: "s07-fT", key: "T", label: "Integration T", min: 1, max: 20, step: 1, value: 1, fmt: (x) => x + " ms" },
    { id: "s07-flip", key: "flip", label: "Data bit flips between the two epochs", type: "check", value: false },
  ], () => drawFll());
  const atan2Disc = (df, T, flip) => { const ang = G.wrapPi(2 * Math.PI * df * T + (flip ? Math.PI : 0)); return ang / (2 * Math.PI * T); };
  const atanDisc = (df, T, flip) => { const ang = 2 * Math.PI * df * T + (flip ? Math.PI : 0); const cr = Math.sin(ang), dt = Math.cos(ang); return Math.atan(cr / dt) / (2 * Math.PI * T); };
  const drawFll = () => {
    const C = C_(), T = vf.T * 1e-3;
    const p = G.plot(fllP, { x: [-1.3, 1.3], y: [-1.3, 1.3], xlabel: "I", ylabel: "Q", xticks: [-1, 0, 1], yticks: [-1, 0, 1] });
    p.fn((x) => Math.sqrt(Math.max(0, 1 - x * x)), { color: C.line, width: 1 }, 200); p.fn((x) => -Math.sqrt(Math.max(0, 1 - x * x)), { color: C.line, width: 1 }, 200);
    const a1 = 20 * G.DEG, a2 = a1 + 2 * Math.PI * vf.df * T + (vf.flip ? Math.PI : 0);
    const c = p.ctx;
    c.strokeStyle = G.alpha(C.refl, 0.8); c.lineWidth = 2; c.beginPath();
    const steps = 40; for (let i = 0; i <= steps; i++) { const a = a1 + (a2 - a1) * i / steps; const x = p.X(0.45 * Math.cos(a)), y = p.Y(0.45 * Math.sin(a)); i ? c.lineTo(x, y) : c.moveTo(x, y); } c.stroke();
    [[a1, "Z[k]", C.gps], [a2, "Z[k+1]", C.bds]].forEach(([a, n, col]) => { p.line([0, Math.cos(a)], [0, Math.sin(a)], { color: col, width: 2.5 }); p.dot(Math.cos(a), Math.sin(a), { color: col }); p.text(Math.cos(a) * 1.12, Math.sin(a) * 1.12, n, { align: "center", color: col, halo: true }); });
    const span = 1.6 / T;
    const q = G.plot(fllS, { x: [-span, span], y: [-0.7 / T, 0.7 / T], xlabel: "true δf (Hz)", ylabel: "FLL output (Hz)" });
    q.band(-0.25 / T, 0.25 / T); q.fn((x) => x, { color: C.ink3, dash: [4, 4], width: 1 });
    q.fn((x) => atan2Disc(x, T, vf.flip), { color: C.gps, width: 2 }, 800);
    q.fn((x) => atanDisc(x, T, vf.flip), { color: C.refl, width: 2, dash: [6, 3] }, 800);
    q.dot(vf.df, atan2Disc(vf.df, T, vf.flip), { color: C.gps }); q.dot(vf.df, atanDisc(vf.df, T, vf.flip), { color: C.refl });
    q.text(-span, 0.62 / T, " atan2 difference", { color: C.gps, font: `11px ${C.mono}` }); q.text(-span, 0.5 / T, " atan(cross/dot)", { color: C.refl, font: `11px ${C.mono}` });
    G.readouts($("#s07-fll-ro"), [
      ["Turn per epoch", ((a2 - a1) / G.DEG).toFixed(1) + "°"],
      ["atan2 output", atan2Disc(vf.df, T, vf.flip).toFixed(1) + " Hz", Math.abs(atan2Disc(vf.df, T, vf.flip) - vf.df) < 1 ? "good" : "bad"],
      ["cross/dot output", atanDisc(vf.df, T, vf.flip).toFixed(1) + " Hz", Math.abs(atanDisc(vf.df, T, vf.flip) - vf.df) < 1 ? "good" : "bad"],
      ["Pull-in range", "±" + (0.5 / T).toFixed(0) + " / ±" + (0.25 / T).toFixed(0) + " Hz"],
    ]);
  };
  G.mount(fllP, drawFll); G.mount(fllS, drawFll);

  /* ============================== PLL ============================== */
  const pIQ = $("#s07-pll-iq"), pD = $("#s07-pll-d"), pStep = $("#s07-pll-step"), pJit = $("#s07-pll-jit");
  const vp = G.controls($("#s07-pll-ctl"), [
    { id: "s07-ph", key: "ph", label: "Phase error φ", min: -180, max: 180, step: 1, value: 30, fmt: (x) => x + "°" },
    { id: "s07-bit", key: "bit", label: "Data bit D = −1", type: "check", value: false },
    { id: "s07-pB", key: "B", label: "PLL bandwidth B_n", min: 1, max: 30, step: 0.5, value: 15, fmt: (x) => x.toFixed(1) + " Hz" },
    { id: "s07-pz", key: "z", label: "Damping ζ", min: 0.2, max: 1.2, step: 0.05, value: 0.7, fmt: (x) => x.toFixed(2) },
    { id: "s07-pcn0", key: "cn0", label: "C/N0", min: 20, max: 50, step: 1, value: 35, fmt: (x) => x + " dB-Hz" },
    { id: "s07-pT", key: "T", label: "Integration T", min: 1, max: 20, step: 1, value: 1, fmt: (x) => x + " ms" },
    { id: "s07-pa", key: "a", label: "LOS acceleration", min: 0, max: 10, step: 0.5, value: 1, fmt: (x) => x.toFixed(1) + " g" },
    { id: "s07-plam", key: "lam", label: "Carrier", type: "select", value: "l1", options: [["l1", "L1 (λ = 19.0 cm)"], ["l5", "L5 (λ = 25.5 cm)"]] },
  ], () => drawPll());
  const wn = (B, z) => 4 * B / (2 * z + 1 / (2 * z));
  const budget = (cn0dB, B, z, T, a, lam) => {
    const cn0 = G.lin(cn0dB), s = Math.sqrt(B / cn0 * (1 + 1 / (2 * T * cn0))) / G.DEG;
    const w = wn(B, z), th = (2 * Math.PI * a * 9.80665 / lam) / (w * w) / G.DEG;
    return { s, th, tot: 3 * s + th };
  };
  const drawPll = () => {
    const C = C_(), phr = vp.ph * G.DEG, D = vp.bit ? -1 : 1, T = vp.T * 1e-3, lam = vp.lam === "l5" ? G.LAM_L5 : G.LAM_L1;
    // I/Q plane
    const p = G.plot(pIQ, { x: [-1.5, 1.5], y: [-1.5, 1.5], xlabel: "I", ylabel: "Q", xticks: [-1, 0, 1], yticks: [-1, 0, 1] });
    const alpha = Math.sqrt(G.lin(vp.cn0) * T), r = G.rng(4), xs = [], ys = [];
    for (let k = 0; k < 150; k++) { const n = cn(r); xs.push(D * Math.cos(phr) + n[0] / alpha); ys.push(D * Math.sin(phr) + n[1] / alpha); }
    p.pts(xs, ys, { color: C.accent, r: 1.8, alpha: 0.4 });
    const ix = D * Math.cos(phr), qy = D * Math.sin(phr);
    p.line([0, ix], [0, qy], { color: C.gps, width: 3 }); p.dot(ix, qy, { color: C.gps });
    p.line([ix, ix], [0, qy], { color: C.ink3, dash: [3, 3], width: 1 }); p.line([0, ix], [qy, qy], { color: C.ink3, dash: [3, 3], width: 1 });
    p.text(ix, 0, "I", { dy: 12, align: "center", color: C.ink2 }); p.text(0, qy, "Q", { dx: -10, color: C.ink2 });
    // discriminators
    const q = G.plot(pD, { x: [-180, 180], y: [-180, 180], xlabel: "true phase error (deg)", ylabel: "output (deg)", xticks: [-180, -90, 0, 90, 180], yticks: [-180, -90, 0, 90, 180] });
    const dd = (f) => (x) => { const a = x * G.DEG, I = D * Math.cos(a), Q = D * Math.sin(a); return f(I, Q) / G.DEG; };
    const curves = [
      ["Costas I·Q", C.ink3, (I, Q) => I * Q],
      ["Costas atan(Q/I)", C.gps, (I, Q) => Math.atan(Q / I)],
      ["Costas Q·sign(I)", C.gal, (I, Q) => Q * Math.sign(I)],
      ["coherent atan2", C.bds, (I, Q) => Math.atan2(Q, I)],
    ];
    curves.forEach(([n, col, f], i) => {
      const xs2 = [], ys2 = []; let prev = null;
      for (let x = -180; x <= 180; x += 0.5) { const y = dd(f)(x); if (prev != null && Math.abs(y - prev) > 90) { xs2.push(x); ys2.push(null); } xs2.push(x); ys2.push(y); prev = y; }
      q.line(xs2, ys2, { color: col, width: i === 1 ? 2.6 : 1.7 });
      q.text(-176, 165 - i * 16, n, { color: col, font: `11px ${C.mono}` });
    });
    q.vline(vp.ph, { color: C.warn, dash: [4, 3] });
    // step response (15.68)
    const w = wn(vp.B, vp.z), z = vp.z;
    const step = (t) => { if (z < 1) { const wd = w * Math.sqrt(1 - z * z), th = Math.atan2(Math.sqrt(1 - z * z), z); return 1 - Math.exp(-z * w * t) / Math.sqrt(1 - z * z) * (Math.sin(wd * t + th) - 2 * z * Math.sin(wd * t)); }
      const zz = Math.min(z, 0.9999); const wd = w * Math.sqrt(1 - zz * zz), th = Math.atan2(Math.sqrt(1 - zz * zz), zz); return 1 - Math.exp(-zz * w * t) / Math.sqrt(1 - zz * zz) * (Math.sin(wd * t + th) - 2 * zz * Math.sin(wd * t)); };
    const s = G.plot(pStep, { x: [0, 0.4], y: [0, 1.7], xlabel: "time (s)", ylabel: "output" });
    s.hline(1, { color: C.ink3, dash: [4, 4] });
    s.fn((t) => { const ww = wn(5, 0.7); const wd = ww * Math.sqrt(1 - 0.49), th = Math.atan2(Math.sqrt(0.51), 0.7); return 1 - Math.exp(-0.7 * ww * t) / Math.sqrt(0.51) * (Math.sin(wd * t + th) - 1.4 * Math.sin(wd * t)); }, { color: C.ink3, width: 1.4, dash: [2, 3] });
    s.fn(step, { color: C.accent, width: 2.2 });
    s.text(0.4, 0.2, `B=${vp.B} Hz, ζ=${vp.z}`, { align: "right", color: C.accent, font: `11px ${C.mono}` });
    s.text(0.4, 0.07, "dotted: B=5 Hz, ζ=0.7", { align: "right", color: C.ink3, font: `11px ${C.mono}` });
    // jitter budget vs C/N0
    const j = G.plot(pJit, { x: [15, 50], y: [0, 120], xlabel: "C/N0 (dB-Hz)", ylabel: "deg" });
    j.hband(45, 120, { fill: G.alpha(C.warn, 0.08) }); j.hline(45, { color: C.warn, dash: [5, 4] });
    j.fn((c) => budget(c, vp.B, vp.z, T, vp.a, lam).tot, { color: C.accent, width: 2.2 });
    j.fn((c) => 3 * budget(c, vp.B, vp.z, T, vp.a, lam).s, { color: C.ink3, width: 1.4, dash: [4, 3] });
    const bb = budget(vp.cn0, vp.B, vp.z, T, vp.a, lam);
    j.dot(vp.cn0, Math.min(119, bb.tot), { color: bb.tot < 45 ? C.ok : C.warn });
    j.text(50, 50, "lock limit 45° ", { align: "right", color: C.warn, font: `11px ${C.mono}` });
    G.readouts($("#s07-pll-ro"), [
      ["ω_n", w.toFixed(1) + " rad/s"],
      ["σ_φ thermal", bb.s.toFixed(1) + "°"],
      ["θ_e dynamic", bb.th.toFixed(1) + "°"],
      ["3σ + θ_e", bb.tot.toFixed(0) + "°", bb.tot < 45 ? "good" : "bad"],
      ["Costas output", (Math.atan(Math.tan(phr)) / G.DEG).toFixed(0) + "°"],
      ["atan2 output", (Math.atan2(D * Math.sin(phr), D * Math.cos(phr)) / G.DEG).toFixed(0) + "°"],
    ]);
  };
  [pIQ, pD, pStep, pJit].forEach((c) => G.mount(c, drawPll));

  /* ============================== live simulation ============================== */
  const sIQ = $("#s07-sim-iq"), sT = $("#s07-sim-t");
  const vs = G.controls($("#s07-sim-ctl"), [
    { id: "s07-scn0", key: "cn0", label: "C/N0", min: 20, max: 50, step: 1, value: 40, fmt: (x) => x + " dB-Hz" },
    { id: "s07-sB", key: "Bp", label: "PLL bandwidth", min: 2, max: 30, step: 1, value: 15, fmt: (x) => x + " Hz" },
    { id: "s07-sF", key: "Bf", label: "FLL bandwidth", min: 2, max: 30, step: 1, value: 10, fmt: (x) => x + " Hz" },
    { id: "s07-sD", key: "Bd", label: "DLL bandwidth", min: 0.2, max: 5, step: 0.1, value: 1, fmt: (x) => x.toFixed(1) + " Hz" },
    { id: "s07-sdf", key: "df0", label: "Initial Doppler error", min: -200, max: 200, step: 5, value: 60, fmt: (x) => x + " Hz" },
    { id: "s07-sdt", key: "dt0", label: "Initial code error", min: -0.5, max: 0.5, step: 0.05, value: 0.3, fmt: (x) => x.toFixed(2) + " chip" },
    { id: "s07-sdyn", key: "dyn", label: "Dynamics", type: "select", value: "static", options: [["static", "Static (constant Doppler)"], ["step", "Doppler step +30 Hz at 1.5 s"], ["g1", "1 g LOS acceleration from 1.5 s"], ["g5", "5 g LOS acceleration from 1.5 s"]] },
    { id: "s07-sbits", key: "bits", label: "50 bit/s data bits on", type: "check", value: true },
  ], () => {});
  const DUR = 3.0, Tep = 1e-3, Nep = Math.round(DUR / Tep), dSp = 0.5;
  let sim = null, raf = null;
  const reset = () => {
    sim = { k: 0, fT: 0, phT: 0.7, tauT: 0, fH: vs.df0, phH: 0, tauH: vs.dt0, integ: 2 * Math.PI * vs.df0, mode: "FLL", prev: null, D: 1,
      r: G.rng(Math.floor(Math.random() * 1e6)), iq: [], tt: [], ep: [], ef: [], et: [], lock: [] };
    drawSim();
  };
  const stepSim = () => {
    const S = sim, T = Tep, A = Math.sqrt(G.lin(vs.cn0) * T), t = S.k * T;
    let fdot = 0;
    if (vs.dyn === "g1" && t >= 1.5) fdot = 9.80665 / G.LAM_L1;
    if (vs.dyn === "g5" && t >= 1.5) fdot = 5 * 9.80665 / G.LAM_L1;
    if (vs.dyn === "step" && Math.abs(t - 1.5) < T / 2) S.fT += 30;
    if (vs.bits && S.k % 20 === 0) S.D = S.r() < 0.5 ? -1 : 1;
    if (!vs.bits) S.D = 1;
    // average errors over the epoch
    const fTavg = S.fT + 0.5 * fdot * T, df = fTavg - S.fH;
    const dph = (S.phT + Math.PI * S.fT * T) - (S.phH + Math.PI * S.fH * T);
    const dtau = S.tauH - S.tauT, amp = A * S.D * G.sinc(df * T);
    const Z = (x) => { const n = cn(S.r), g = amp * G.tri(x); return [g * Math.cos(dph) + n[0], g * Math.sin(dph) + n[1]]; };
    const E = Z(dtau - dSp), P = Z(dtau), L = Z(dtau + dSp);
    // discriminators
    const ePh = Math.atan(P[1] / P[0]);                               // Costas, rad
    let eF = 0; if (S.prev) { const dot = S.prev[0] * P[0] + S.prev[1] * P[1], cr = S.prev[0] * P[1] - S.prev[1] * P[0]; eF = Math.atan(cr / dot) / (2 * Math.PI * T); }
    const eTau = (mag(E) - mag(L)) / (mag(E) + mag(L)) * (1 - dSp);   // chips, estimate of dtau
    S.prev = P;
    // mode hand-over (Fig. 15.28): FLL first, PLL after 0.3 s
    if (S.mode === "FLL" && t > 0.3) { S.mode = "PLL"; S.integ = 2 * Math.PI * S.fH; }
    if (S.mode === "FLL") { S.fH += 4 * vs.Bf * T * eF; }
    else { const w = wn(vs.Bp, 0.707); S.integ += w * w * T * ePh; S.fH = (S.integ + 2 * 0.707 * w * ePh) / (2 * Math.PI); }
    // advance truth and replica
    S.phT += 2 * Math.PI * (S.fT * T + 0.5 * fdot * T * T); S.fT += fdot * T; S.tauT += (S.fT / 1540) * T;
    S.phH += 2 * Math.PI * S.fH * T; S.tauH += (S.fH / 1540) * T - 4 * vs.Bd * T * eTau;
    S.iq.push([P[0] / A, P[1] / A]); if (S.iq.length > 250) S.iq.shift();
    S.tt.push(t); S.ep.push(G.wrapPi(2 * dph) / 2 / G.DEG); // Costas: error is defined modulo 180° S.ef.push(df); S.et.push(dtau * 293.05);
    S.lock.push((P[0] * P[0] - P[1] * P[1]) / (P[0] * P[0] + P[1] * P[1]));
    S.k++;
  };
  const drawSim = () => {
    if (!sim) return; const C = C_(), S = sim;
    const p = G.plot(sIQ, { x: [-1.8, 1.8], y: [-1.8, 1.8], xlabel: "I / A", ylabel: "Q / A", xticks: [-1, 0, 1], yticks: [-1, 0, 1] });
    const n = S.iq.length; S.iq.forEach(([i, q], k) => p.pts([i], [q], { color: G.cmapStr(0.15 + 0.8 * k / Math.max(1, n)), r: 2.2 }));
    const [a, b, c] = G.stack(sT, [
      { x: [0, DUR], y: [-90, 90], ylabel: "phase°", yticks: [-90, 0, 90] },
      { x: [0, DUR], y: [-Math.max(20, Math.abs(vs.df0) * 1.1), Math.max(20, Math.abs(vs.df0) * 1.1)], ylabel: "freq Hz" },
      { x: [0, DUR], y: [-120, 120], ylabel: "code m", xlabel: "time (s)" },
    ]);
    a.band(0, 0.3, { fill: G.alpha(C.refl, 0.12) }); b.band(0, 0.3, { fill: G.alpha(C.refl, 0.12) });
    a.pts(S.tt, S.ep, { color: C.gps, r: 1, alpha: 0.6 }); b.line(S.tt, S.ef, { color: C.bds, width: 1.3 }); c.line(S.tt, S.et, { color: C.gal, width: 1.3 });
    a.hline(0, { color: C.ink3, width: 1 }); b.hline(0, { color: C.ink3, width: 1 }); c.hline(0, { color: C.ink3, width: 1 });
    a.text(0.02, 75, "FLL", { color: C.refl, font: `11px ${C.mono}` });
    $("#s07-sim-mode").textContent = S.mode + " · t = " + (S.k * Tep).toFixed(2) + " s";
    const last = (arr, m) => arr.slice(Math.max(0, arr.length - m));
    const sd = (arr) => { const m = arr.reduce((s, x) => s + x, 0) / arr.length; return Math.sqrt(arr.reduce((s, x) => s + (x - m) ** 2, 0) / arr.length); };
    const lk = last(S.lock, 200), lkm = lk.length ? lk.reduce((s, x) => s + x, 0) / lk.length : 0;
    G.readouts($("#s07-sim-ro"), [
      ["Loop mode", S.mode],
      ["Phase lock indicator", lkm.toFixed(2), lkm > 0.8 ? "good" : "bad"],
      ["σ phase (last 0.5 s)", S.ep.length > 50 ? sd(last(S.ep, 500)).toFixed(1) + "°" : "-"],
      ["σ code (last 0.5 s)", S.et.length > 50 ? sd(last(S.et, 500)).toFixed(2) + " m" : "-"],
    ]);
  };
  const loop = () => {
    for (let i = 0; i < 25 && sim.k < Nep; i++) stepSim();
    drawSim();
    if (sim.k < Nep) raf = requestAnimationFrame(loop); else { raf = null; $("#s07-run").textContent = "Run again"; }
  };
  $("#s07-run").addEventListener("click", () => { if (raf) cancelAnimationFrame(raf); reset(); $("#s07-run").textContent = "Running…"; raf = requestAnimationFrame(loop); });
  $("#s07-reset").addEventListener("click", () => { if (raf) cancelAnimationFrame(raf); raf = null; reset(); $("#s07-run").textContent = "Run"; });
  G.mount(sIQ, drawSim); G.mount(sT, drawSim);
  reset();

  /* ============================== HERON data ============================== */
  if (!G.data) return;
  const sigs = G.data.signals;
  const vr = G.controls($("#s07-real-ctl"), [
    { id: "s07-rs", key: "sig", label: "Signal", type: "select", value: "0", options: sigs.map((s, i) => [String(i), `${s.label} · ${s.prn} · ${s.cn0_mean} dB-Hz`]) },
    { id: "s07-rw", key: "win", label: "I/Q window", type: "select", value: "steady", options: [["pull_in", "First 3 s (pull-in)"], ["steady", "60 to 63 s (steady state)"]] },
    { id: "s07-rc", key: "comp", label: "Component", type: "select", value: "0", options: [["0", "Component 0"], ["1", "Component 1 (if present)"]] },
  ], () => drawReal());
  const rIQ = $("#s07-real-iq"), rCn = $("#s07-real-cn0"), rDop = $("#s07-real-dop"), rRng = $("#s07-real-rng"), rCe = $("#s07-real-cerr"), rPe = $("#s07-real-perr");
  const range = (a) => { let lo = Infinity, hi = -Infinity; a.forEach((v) => { if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }); const pad = (hi - lo) * 0.08 || 1; return [lo - pad, hi + pad]; };
  const drawReal = () => {
    const S = sigs[+vr.sig], C = C_(), comp = S.iq[Math.min(+vr.comp, S.iq.length - 1)];
    const pts = comp[vr.win];
    $("#s07-real-win").textContent = (vr.win === "steady" ? "60–63 s" : "0–3 s") + (S.iq.length > 1 ? " · comp " + Math.min(+vr.comp, S.iq.length - 1) : "");
    const p = G.plot(rIQ, { x: [-1.8, 1.8], y: [-1.8, 1.8], xlabel: "I", ylabel: "Q", xticks: [-1, 0, 1], yticks: [-1, 0, 1] });
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
    pts.forEach(([t, i, q]) => p.pts([i], [q], { color: G.cmapStr(0.1 + 0.85 * (t - t0) / (t1 - t0 || 1)), r: 1.8 }));
    const se = S.series, tt = se.t;
    // The last C/N0 window can be partial and read near zero; treat values under 15 dB-Hz as gaps.
    const cnv = S.cn0.v.map((x) => (x != null && x > 15 ? x : null));
    const q1 = G.plot(rCn, { x: [0, 120], y: range(cnv), xlabel: "time (s)", ylabel: "dB-Hz" }); q1.line(S.cn0.t, cnv, { color: C.accent });
    const q2 = G.plot(rDop, { x: [0, 120], y: range(se.doppler_hz), xlabel: "time (s)", ylabel: "Hz" }); q2.line(tt, se.doppler_hz, { color: C.bds });
    const km = se.carr_range_m.map((m) => m / 1000);
    const q3 = G.plot(rRng, { x: [0, 120], y: range(km), xlabel: "time (s)", ylabel: "km" }); q3.line(tt, km, { color: C.gal });
    const lim = (a, m) => { const r = range(a.slice(8)); return [Math.max(-m, r[0]), Math.min(m, r[1])]; };
    const q4 = G.plot(rCe, { x: [0, 120], y: lim(se.code_err_chips, 0.3), xlabel: "time (s)", ylabel: "chips" }); q4.hline(0, { color: C.ink3, width: 1 }); q4.line(tt, se.code_err_chips, { color: C.gps, width: 1.2 });
    const q5 = G.plot(rPe, { x: [0, 120], y: lim(se.carr_err_cyc, 0.25), xlabel: "time (s)", ylabel: "cycles" }); q5.hline(0, { color: C.ink3, width: 1 }); q5.line(tt, se.carr_err_cyc, { color: C.refl, width: 1.2 });
    const chipM = G.C / (S.f_carrier_hz > 1.4e9 ? 1.023e6 : 10.23e6);
    const med = (a) => { const b = a.filter((x) => x != null).slice().sort((x, y) => x - y); return b[Math.floor(b.length / 2)]; };
    G.readouts($("#s07-real-ro"), [
      ["Satellite", S.prn + " · " + S.n_components + " comp."],
      ["Mean C/N0", S.cn0_mean + " dB-Hz"],
      ["Code error σ (per epoch)", S.code_err_std_chips.toFixed(3) + " chip = " + (S.code_err_std_chips * chipM).toFixed(2) + " m"],
      ["Carrier error σ", S.carr_err_std_cyc.toFixed(3) + " cyc = " + (S.carr_err_std_cyc * S.wavelength_m * 1000).toFixed(1) + " mm"],
      ["Doppler span", (Math.max(...se.doppler_hz) - Math.min(...se.doppler_hz)).toFixed(0) + " Hz in 120 s"],
      ["E/P and L/P", med(se.e_over_p).toFixed(2) + " / " + med(se.l_over_p).toFixed(2)],
    ]);
  };
  [rIQ, rCn, rDop, rRng, rCe, rPe].forEach((c) => G.mount(c, drawReal));
  if (G.data.figures.code_doppler_g01) $("#s07-img-cd").src = G.data.figures.code_doppler_g01;
});
