/* s03.js - Part 03: carrier x code x data, on a zoomable time axis.
 * Each trace is sampled several times per pixel. The plot draws the min-max
 * envelope per pixel, so a dense waveform shows as a band and a sparse one as
 * a line. That keeps one code path for every zoom level. */
G.section("s03", (root) => {
  const cv = root.querySelector("#s03-wave");
  const NH10 = [0, 0, 0, 0, 1, 1, 0, 1, 0, 1].map((b) => (b ? -1 : 1)); // IS-GPS-705 NH10
  const ca = G.caCode(1);
  const r = G.rng(5); const l5code = Int8Array.from({ length: 10230 }, () => (r() < 0.5 ? -1 : 1)); // display stand-in for the L5 I code
  const bits = Int8Array.from({ length: 64 }, () => (r() < 0.5 ? -1 : 1));
  const SIG = {
    l1: { name: "GPS L1 C/A, PRN 1", fc: G.F_L1, rc: 1.023e6, len: 1023, code: (k) => ca[k % 1023], sym: 20e-3, symName: "Data bit (50 bit/s)", over: null },
    l5: { name: "GPS L5 I (data), display code", fc: G.F_L5, rc: 10.23e6, len: 10230, code: (k) => l5code[k % 10230], sym: 10e-3, symName: "Data symbol (100 sym/s)", over: (t) => NH10[Math.floor(t / 1e-3) % 10] },
  };
  const v = G.controls(root.querySelector("#s03-ctl"), [
    { id: "s03-sig", key: "sig", label: "Signal", type: "select", value: "l1", options: [["l1", "GPS L1 C/A (1575.42 MHz)"], ["l5", "GPS L5 I (1176.45 MHz)"]] },
    { id: "s03-zoom", key: "z", label: "Time span shown", min: -8, max: -1.3, step: 0.02, value: -5.0, fmt: (x) => fmtT(Math.pow(10, x)) },
  ], () => draw());
  function fmtT(s) { return s < 1e-6 ? (s * 1e9).toFixed(1) + " ns" : s < 1e-3 ? (s * 1e6).toFixed(2) + " µs" : (s * 1e3).toFixed(2) + " ms"; }

  const draw = () => {
    const S = SIG[v.sig], span = Math.pow(10, v.z), C = G.col;
    root.querySelector("#s03-title").textContent = S.name;
    root.querySelector("#s03-span").textContent = "0 to " + fmtT(span);
    const rows = [
      { name: "Carrier cos(2πf t)", col: C.ink3, f: (t) => Math.cos(2 * Math.PI * S.fc * t) },
      { name: "PRN code C(t)", col: C.gps, f: (t) => S.code(Math.floor(t * S.rc)) },
      { name: S.over ? "Data × NH10 overlay" : S.symName, col: C.bds, f: (t) => bits[Math.floor(t / S.sym) % 64] * (S.over ? S.over(t) : 1) },
      { name: "Baseband D·C", col: C.gal, f: (t) => bits[Math.floor(t / S.sym) % 64] * (S.over ? S.over(t) : 1) * S.code(Math.floor(t * S.rc)) },
      { name: "Transmitted s(t)", col: C.accent, f: (t) => bits[Math.floor(t / S.sym) % 64] * (S.over ? S.over(t) : 1) * S.code(Math.floor(t * S.rc)) * Math.cos(2 * Math.PI * S.fc * t) },
    ];
    const n = rows.length;
    const p = G.plot(cv, { x: [0, span], y: [-0.5, n - 0.5], xlabel: "time", yticks: [], margin: { l: 150, b: 36 }, xfmt: (x) => (x === 0 ? "0" : fmtT(x)) });
    const c = p.ctx, W = Math.round(p.x1 - p.x0), K = 12;
    rows.forEach((row, i) => {
      const yc = n - 1 - i, amp = 0.36;
      p.hline(yc, { color: G.alpha(C.ink3, 0.25), width: 1 });
      p.text(0, yc, row.name, { px: false, dx: -144, color: C.ink2, font: `12px ${C.mono}` });
      c.strokeStyle = row.col; c.lineWidth = 1.6; c.beginPath();
      let prevHi = null;
      for (let x = 0; x < W; x++) {
        let lo = 9, hi = -9;
        for (let k = 0; k <= K; k++) { const t = span * (x + k / K) / W; const y = row.f(t); if (y < lo) lo = y; if (y > hi) hi = y; }
        const X = p.x0 + x, Ylo = p.Y(yc + lo * amp), Yhi = p.Y(yc + hi * amp);
        if (x === 0) c.moveTo(X, Ylo); else c.lineTo(X, prevHi == null ? Ylo : Ylo);
        c.lineTo(X, Yhi); prevHi = Yhi;
      }
      c.stroke();
    });
    const chips = span * S.rc, cyc = span * S.fc;
    G.readouts(root.querySelector("#s03-ro"), [
      ["Carrier cycles shown", cyc >= 1e4 ? cyc.toExponential(2) : cyc.toFixed(1)],
      ["Code chips shown", chips >= 1e4 ? chips.toExponential(2) : chips.toFixed(2)],
      ["Carrier cycles per chip", (S.fc / S.rc).toFixed(0)],
      ["Chip length", G.si(G.C / S.rc)],
      ["Code period", (S.len / S.rc * 1e3).toFixed(0) + " ms = " + G.si(G.C * S.len / S.rc)],
      ["Wavelength", G.si(G.C / S.fc)],
    ]);
  };
  G.mount(cv, draw);
});
