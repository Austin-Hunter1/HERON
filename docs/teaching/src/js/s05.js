/* s05.js - Part 05: transmit time hierarchy and the five steps to a fix.
 * The satellite geometry here is a fixed teaching set (same idea as Part 01).
 * The least-squares solver is the textbook 4-state Gauss-Newton iteration. */
G.section("s05", (root) => {
  /* ---------- timing hierarchy (Fig. 15.1 style) ---------- */
  const tc = root.querySelector("#s05-time");
  const v = G.controls(root.querySelector("#s05-ctl"), [
    { id: "s05-n", key: "N", label: "Bit N in subframe", min: 1, max: 300, step: 1, value: 137, fmt: (x) => x + " / 300" },
    { id: "s05-m", key: "M", label: "Code period M in bit", min: 1, max: 20, step: 1, value: 8, fmt: (x) => x + " / 20" },
    { id: "s05-tau", key: "tau", label: "Code phase τ", min: 0, max: 1022.9, step: 0.1, value: 412.3, fmt: (x) => x.toFixed(1) + " chips" },
  ], () => drawTime());
  const drawTime = () => {
    const { ctx: c, w, h } = G.size(tc), C = G.col;
    const L = 16, R = w - 16, rowH = Math.min(46, h / 5.2), gap = (h - 3 * rowH - 20) / 3;
    const rows = [
      { y: 14, n: 300, k: v.N, name: "Subframe: 300 bits = 6 s", col: C.bds, lab: (i) => "Bit " + i },
      { y: 14 + rowH + gap, n: 20, k: v.M, name: "Bit: 20 codes = 20 ms", col: C.gps, lab: (i) => "CA " + i },
      { y: 14 + 2 * (rowH + gap), n: 1023, k: Math.floor(v.tau) + 1, name: "Code: 1023 chips = 1 ms", col: C.gal, lab: (i) => "Chip " + i },
    ];
    c.font = `600 11px ${C.mono}`;
    rows.forEach((r, ri) => {
      const cw = (R - L) / r.n;
      c.fillStyle = G.alpha(r.col, 0.12); c.fillRect(L, r.y, R - L, rowH);
      c.fillStyle = r.col; c.fillRect(L + (r.k - 1) * cw, r.y, Math.max(3, cw), rowH);
      c.strokeStyle = C.line; c.strokeRect(L, r.y, R - L, rowH);
      if (r.n <= 20) for (let i = 1; i < r.n; i++) { c.beginPath(); c.moveTo(L + i * cw, r.y); c.lineTo(L + i * cw, r.y + rowH); c.stroke(); }
      c.fillStyle = C.ink2; c.textAlign = "left"; c.fillText(r.name, L + 4, r.y - 4 + 0 * ri);
      c.fillStyle = C.ink; c.textAlign = r.k / r.n > 0.8 ? "right" : "left";
      c.fillText(r.lab(r.k), L + (r.k - 1) * cw + (r.k / r.n > 0.8 ? -6 : Math.max(3, cw) + 6), r.y + rowH / 2 + 4);
      if (ri < 2) { // zoom lines to the next row
        const nx = rows[ri + 1];
        c.strokeStyle = G.alpha(r.col, 0.6); c.setLineDash([3, 3]); c.beginPath();
        c.moveTo(L + (r.k - 1) * cw, r.y + rowH); c.lineTo(L, nx.y);
        c.moveTo(L + r.k * cw, r.y + rowH); c.lineTo(R, nx.y); c.stroke(); c.setLineDash([]);
      }
    });
    const tx = (v.N - 1) * 20 + (v.M - 1) * 1 + v.tau / 1023;
    G.readouts(root.querySelector("#s05-ro"), [
      ["t_tx − t_b", tx.toFixed(4) + " ms"],
      ["From τ only", (v.tau / 1023).toFixed(4) + " ms"],
      ["1 chip of τ", "977.5 ns = 293 m"],
    ]);
  };
  G.mount(tc, drawTime);

  /* ---------- five steps to a fix ---------- */
  const sc = root.querySelector("#s05-step");
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const lat = 40 * G.DEG, lon = -105.25 * G.DEG;
  const E0 = [-Math.sin(lon), Math.cos(lon), 0], N0 = [-Math.sin(lat) * Math.cos(lon), -Math.sin(lat) * Math.sin(lon), Math.cos(lat)];
  const U0 = [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
  const ru = U0.map((x) => x * 6378137);
  const RS = 26560e3, bias = 0.000317; // receiver clock bias, s
  const sats = [[35, 68], [115, 32], [205, 48], [290, 24], [165, 14], [330, 40]].map(([az, el], i) => {
    const a = az * G.DEG, e = el * G.DEG;
    const u = [0, 1, 2].map((k) => E0[k] * Math.sin(a) * Math.cos(e) + N0[k] * Math.cos(a) * Math.cos(e) + U0[k] * Math.sin(e));
    const q = dot(ru, u), rho = -q + Math.sqrt(q * q - dot(ru, ru) + RS * RS);
    const pos = [0, 1, 2].map((k) => ru[k] + u[k] * rho);
    return { name: "G" + [5, 12, 13, 20, 25, 29][i], pos, range: rho, el };
  });
  const travel = sats.map((s) => s.range / G.C + bias);           // s
  const lsqIter = () => { // Gauss-Newton from Earth centre
    let x = [0, 0, 0, 0]; const hist = [];
    for (let it = 0; it < 8; it++) {
      const Gm = [], y = [];
      sats.forEach((s, i) => {
        const d = [s.pos[0] - x[0], s.pos[1] - x[1], s.pos[2] - x[2]], r = Math.sqrt(dot(d, d));
        Gm.push([-d[0] / r, -d[1] / r, -d[2] / r, 1]); y.push(G.C * travel[i] - (r + x[3]));
      });
      const A = [0, 1, 2, 3].map((r) => [0, 1, 2, 3].map((c) => Gm.reduce((s, g) => s + g[r] * g[c], 0)));
      const b = [0, 1, 2, 3].map((r) => Gm.reduce((s, g, i) => s + g[r] * y[i], 0));
      const dx = solve4(A, b); x = x.map((v, k) => v + dx[k]);
      const err = Math.hypot(x[0] - ru[0], x[1] - ru[1], x[2] - ru[2]);
      hist.push({ err, b: x[3] / G.C, A });
    }
    return hist;
  };
  function solve4(A, b) { const n = 4, M = A.map((r, i) => [...r, b[i]]);
    for (let c = 0; c < n; c++) { let p = c; for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r; [M[c], M[p]] = [M[p], M[c]];
      for (let r = c + 1; r < n; r++) { const f = M[r][c] / M[c][c]; for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k]; } }
    const x = [0, 0, 0, 0]; for (let r = n - 1; r >= 0; r--) { let s = M[r][n]; for (let k = r + 1; k < n; k++) s -= M[r][k] * x[k]; x[r] = s / M[r][r]; } return x; }
  const inv4diag = (A) => { const d = []; for (let k = 0; k < 4; k++) { const e = [0, 0, 0, 0]; e[k] = 1; d.push(solve4(A, e)[k]); } return d; };
  const hist = lsqIter();
  let step = 0;
  const caps = ["Fractional code phase per satellite (0 to 1 ms)", "Add the integer milliseconds from the message", "Pseudorange = c × travel time on the receiver clock", "Satellite position from Kepler elements", "Gauss-Newton iterations from the Earth's centre"];
  const drawStep = () => {
    root.querySelector("#s05-cap").textContent = caps[step];
    const C = G.col, n = sats.length;
    if (step <= 2) {
      const xmax = step === 0 ? 1 : 95, p = G.plot(sc, { x: [0, step === 2 ? 95 * G.C / 1e6 : xmax], y: [-0.6, n - 0.4], yticks: [], margin: { l: 50 }, xlabel: step === 2 ? "pseudorange (thousand km)" : "travel time (ms)" });
      sats.forEach((s, i) => {
        const tms = travel[i] * 1e3, frac = tms % 1, whole = Math.floor(tms), c = p.ctx, y = n - 1 - i;
        const k = step === 2 ? G.C / 1e6 : 1;
        p.text(0, y, s.name, { dx: -44, color: C.ink2 });
        if (step >= 1) { c.fillStyle = G.alpha(C.gps, 0.35); c.fillRect(p.X(0), p.Y(y + 0.3), p.X(whole * k) - p.X(0), p.Y(y - 0.3) - p.Y(y + 0.3)); }
        const x0 = step >= 1 ? whole : 0;
        c.fillStyle = C.gal; c.fillRect(p.X(x0 * k), p.Y(y + 0.3), Math.max(2, p.X((x0 + frac) * k) - p.X(x0 * k)), p.Y(y - 0.3) - p.Y(y + 0.3));
        const lab = step === 0 ? frac.toFixed(3) + " ms" : step === 1 ? tms.toFixed(3) + " ms" : (tms * G.C / 1e6).toFixed(0) + " km";
        p.text((x0 + frac) * k, y, " " + lab, { color: C.ink, font: `11px ${C.mono}` });
      });
    } else if (step === 3) {
      const { ctx: c, w, h } = G.size(sc), cx = w * 0.42, cy = h / 2, a = Math.min(w * 0.36, h * 0.44), e = 0.35, b = a * Math.sqrt(1 - e * e);
      c.strokeStyle = C.line; c.lineWidth = 1.5; c.beginPath(); c.ellipse(cx, cy, a, b, 0, 0, 7); c.stroke();
      const fx = cx + a * e; c.fillStyle = G.alpha(C.gps, 0.5); c.beginPath(); c.arc(fx, cy, 14, 0, 7); c.fill();
      const E = 2.1, px = cx + a * Math.cos(E), py = cy - b * Math.sin(E);
      c.strokeStyle = C.gal; c.beginPath(); c.moveTo(fx, cy); c.lineTo(px, py); c.stroke();
      c.fillStyle = C.gal; c.beginPath(); c.arc(px, py, 6, 0, 7); c.fill();
      c.strokeStyle = C.ink3; c.setLineDash([3, 3]); c.beginPath(); c.moveTo(cx - a - 10, cy); c.lineTo(cx + a + 10, cy); c.stroke(); c.setLineDash([]);
      c.font = `600 12px ${C.mono}`; c.fillStyle = C.ink;
      c.fillText("satellite at t_tx", px + 10, py - 8); c.fillText("Earth (focus)", fx + 18, cy + 18); c.fillText("r = a(1 − e cos E)", (fx + px) / 2 + 8, (cy + py) / 2);
      c.fillStyle = C.ink2; c.font = `12px ${C.mono}`;
      const lines = ["√a  e  i₀  Ω₀  ω  M₀", "Δn  di/dt  dΩ/dt", "C_uc C_us C_rc C_rs C_ic C_is", "t_oe   a_f0 a_f1 a_f2   t_oc"];
      lines.forEach((t, i) => c.fillText(t, w * 0.72 > w - 200 ? 12 : w * 0.72, 22 + i * 18));
      c.fillStyle = C.ink3; c.fillText("e exaggerated (GPS e < 0.02)", 12, h - 10);
    } else {
      const p = G.plot(sc, { x: [1, 8], y: [1e-3, 1e8], logy: true, xlabel: "iteration", ylabel: "position error (m)", xticks: [1, 2, 3, 4, 5, 6, 7, 8], yfmt: (v) => G.si(v).replace(" ", "") });
      p.line(hist.map((_, i) => i + 1), hist.map((h) => Math.max(1.1e-3, h.err)), { color: C.accent, width: 2.2 });
      hist.forEach((h, i) => p.dot(i + 1, Math.max(1.1e-3, h.err), { color: C.accent, r: 4 }));
    }
    const last = hist[hist.length - 1], d = inv4diag(last.A);
    G.readouts(root.querySelector("#s05-ro2"), [
      ["Satellites", sats.length + ""],
      ["Solved clock bias", (last.b * 1e3).toFixed(4) + " ms"],
      ["True clock bias", (bias * 1e3).toFixed(4) + " ms"],
      ["GDOP / PDOP", Math.sqrt(d[0] + d[1] + d[2] + d[3]).toFixed(2) + " / " + Math.sqrt(d[0] + d[1] + d[2]).toFixed(2)],
    ]);
  };
  G.stepper(root.querySelector("#s05-step").closest(".panel"), (i) => { step = i; drawStep(); });
  G.mount(sc, drawStep);
});
