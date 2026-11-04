/* s04.js - Part 04: Gold code correlation, CDMA separation, and processing gain.
 * Uses the real GPS C/A codes from G.caCode. The noise demo uses one sample
 * per chip and no Doppler, so it isolates the correlation gain. */
G.section("s04", (root) => {
  const chipsC = root.querySelector("#s04-chips"), autoC = root.querySelector("#s04-auto"), crossC = root.querySelector("#s04-cross");
  const v = G.controls(root.querySelector("#s04-ctl"), [
    { id: "s04-a", key: "a", label: "PRN A", min: 1, max: 32, step: 1, value: 1, fmt: (x) => "G" + String(x).padStart(2, "0") },
    { id: "s04-b", key: "b", label: "PRN B", min: 1, max: 32, step: 1, value: 14, fmt: (x) => "G" + String(x).padStart(2, "0") },
  ], () => { drawTop(); drawBottom(); });

  const drawChips = () => {
    const a = G.caCode(v.a), b = G.caCode(v.b);
    const p = G.plot(chipsC, { x: [0, 64], y: [-0.5, 1.5], yticks: [], margin: { l: 56, b: 18 }, xticks: [0, 16, 32, 48, 64] });
    [[a, 1, G.col.gps, "A"], [b, 0, G.col.bds, "B"]].forEach(([c, y0, col, nm]) => {
      const xs = [], ys = []; for (let i = 0; i < 64; i++) { xs.push(i, i + 1); ys.push(y0 + c[i] * 0.3, y0 + c[i] * 0.3); }
      p.line(xs, ys, { color: col, width: 1.6 }); p.text(0, y0, "PRN " + nm, { dx: -50, color: col });
    });
  };
  const drawTop = () => {
    drawChips();
    const a = G.caCode(v.a), b = G.caCode(v.b);
    const ra = G.xcorr(a, a), rb = G.xcorr(a, b);
    const xs = [], ys = []; for (let k = 0; k <= 2046; k++) { xs.push(k); ys.push(ra[k % 1023]); }
    const p = G.plot(autoC, { x: [0, 2046], y: [-0.15, 1.1], xlabel: "lag (chips)", ylabel: "R", xticks: [0, 341, 682, 1023, 1364, 1705, 2046] });
    p.line(xs, ys, { color: G.col.gps, width: 1.4 });
    [0, 1023, 2046].forEach((k) => p.text(k, 1.04, (k * 293.05 / 1000).toFixed(0) + " km", { align: k ? (k > 2000 ? "right" : "center") : "left", color: G.col.ink2, font: `11px ${G.col.mono}` }));
    const ys2 = Array.from(rb);
    const p2 = G.plot(crossC, { x: [0, 1023], y: [-0.15, 1.1], xlabel: "lag (chips)", ylabel: "R" });
    [63 / 1023, -65 / 1023].forEach((l) => p2.hline(l, { color: G.col.ink3, dash: [4, 4] }));
    p2.line(Array.from({ length: 1023 }, (_, k) => k), ys2, { color: G.col.bds, width: 1.4 });
    let mx = 0; for (let k = 0; k < 1023; k++) mx = Math.max(mx, Math.abs(rb[k]));
    G.readouts(root.querySelector("#s04-ro"), [
      ["Auto peak", "1.000"],
      ["Max |cross|", v.a === v.b ? "same code" : `${(mx * 1023).toFixed(0)}/1023 = ${G.db(mx * mx).toFixed(1)} dB`, v.a === v.b ? "bad" : "good"],
      ["First 10 chips, octal", `A ${G.caOctal(v.a)} · B ${G.caOctal(v.b)}`],
    ]);
  };

  /* ---------- noise and processing gain ---------- */
  const rxC = root.querySelector("#s04-rx"), corrC = root.querySelector("#s04-corr");
  const v2 = G.controls(root.querySelector("#s04-ctl2"), [
    { id: "s04-snr", key: "snr", label: "Input SNR per chip", min: -30, max: 10, step: 1, value: -20, fmt: (x) => x + " dB" },
    { id: "s04-k", key: "k", label: "Coherent sum K", min: 1, max: 20, step: 1, value: 1, fmt: (x) => x + " ms" },
    { id: "s04-tau", key: "tau", label: "True code delay", min: 0, max: 1022, step: 1, value: 377, fmt: (x) => x + " chips" },
    { id: "s04-two", key: "two", label: "Add PRN B at delay 700 chips (same power)", type: "check", value: true },
  ], () => drawBottom());
  let seed = 3;
  const drawBottom = () => {
    const N = 1023, K = v2.k, a = G.caCode(v.a), b = G.caCode(v.b), r = G.rng(seed);
    const sig = Math.sqrt(G.lin(v2.snr)); // amplitude with unit noise variance
    const y = new Float64Array(N), first = new Float64Array(120);
    for (let k = 0; k < K; k++) for (let n = 0; n < N; n++) {
      let s = sig * a[(n - v2.tau + N) % N]; if (v2.two) s += sig * b[(n - 700 + N) % N];
      const x = s + r.gauss(); y[n] += x; if (k === 0 && n < 120) first[n] = x;
    }
    // receive plot
    const lim = Math.max(3.5, sig * 1.6 + 3);
    const p = G.plot(rxC, { x: [0, 120], y: [-lim, lim], ylabel: "x[n]", margin: { b: 20 } });
    p.line(Array.from({ length: 120 }, (_, i) => i), Array.from(first), { color: G.col.ink3, width: 1 });
    const xs = [], ys = []; for (let i = 0; i < 120; i++) { xs.push(i, i + 1); const s = sig * a[(i - v2.tau + N) % N]; ys.push(s, s); }
    p.line(xs, ys, { color: G.col.gps, width: 2 });
    // correlation
    const R = new Float64Array(N);
    for (let k = 0; k < N; k++) { let s = 0; for (let n = 0; n < N; n++) s += y[n] * a[(n - k + N) % N]; R[k] = s / (N * K); }
    let pk = 0, pi = 0; for (let k = 0; k < N; k++) if (Math.abs(R[k]) > Math.abs(pk)) { pk = R[k]; pi = k; }
    let m2 = 0, c = 0; for (let k = 0; k < N; k++) if (Math.abs(k - pi) > 2) { m2 += R[k] * R[k]; c++; }
    const nstd = Math.sqrt(m2 / c), snrOut = G.db((pk * pk) / (nstd * nstd));
    const ymax = Math.max(Math.abs(pk), 4 * nstd) * 1.15;
    const p2 = G.plot(corrC, { x: [0, 1022], y: [-ymax * 0.35, ymax], xlabel: "replica lag (chips)", ylabel: "R" });
    p2.hband(-3 * nstd, 3 * nstd, { fill: G.alpha(G.col.ink3, 0.12) });
    p2.line(Array.from({ length: N }, (_, k) => k), Array.from(R), { color: G.col.gps, width: 1.2 });
    p2.dot(pi, pk, { color: pi === v2.tau ? G.col.ok : G.col.warn });
    p2.text(pi, pk, pi === v2.tau ? "  found at " + pi : "  wrong peak at " + pi, { color: pi === v2.tau ? G.col.ok : G.col.warn, halo: true });
    const expd = v2.snr + G.db(N * K);
    G.readouts(root.querySelector("#s04-ro2"), [
      ["Equivalent C/N0", (v2.snr + 60.1).toFixed(1) + " dB-Hz"],
      ["Predicted SNR out", expd.toFixed(1) + " dB"],
      ["Measured peak/noise", snrOut.toFixed(1) + " dB", pi === v2.tau ? "good" : "bad"],
    ]);
  };
  const re = () => { seed++; drawBottom(); };
  rxC.addEventListener("click", re); corrC.addEventListener("click", re);
  G.mount(chipsC, drawTop); G.mount(autoC, drawTop); G.mount(crossC, drawTop);
  G.mount(rxC, drawBottom); G.mount(corrC, drawBottom);
  const img = root.querySelector("#s04-psd"); if (G.data && G.data.figures.raw_psd_l5) img.src = G.data.figures.raw_psd_l5;
});
