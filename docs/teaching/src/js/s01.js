/* s01.js - Part 01: trilateration.
 * Panel A: 2D forest with three chime towers and an unknown watch error.
 * Panel B: 3D satellite version. Shows that 3 satellites leave a family of
 * solutions (one per clock bias) and 4 satellites fix the clock bias.
 * All geometry is simple and display-scale; it is for teaching, not navigation. */
G.section("s01", (root) => {
  /* ------------------------------------------------------------ helpers */
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  const mul = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const norm = (a) => Math.sqrt(dot(a, a));
  const unit = (a) => mul(a, 1 / norm(a));
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  /** Solve A x = b by Gaussian elimination with partial pivoting (small systems). */
  const solve = (A, b) => {
    const n = b.length, M = A.map((r, i) => [...r, b[i]]);
    for (let c = 0; c < n; c++) {
      let p = c; for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
      [M[c], M[p]] = [M[p], M[c]];
      if (Math.abs(M[c][c]) < 1e-14) return null;
      for (let r = c + 1; r < n; r++) { const f = M[r][c] / M[c][c]; for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k]; }
    }
    const x = new Array(n).fill(0);
    for (let r = n - 1; r >= 0; r--) { let s = M[r][n]; for (let k = r + 1; k < n; k++) s -= M[r][k] * x[k]; x[r] = s / M[r][r]; }
    return x;
  };
  /** Least-squares step: rows H (m x n), residuals y. Returns dx. */
  const lsq = (H, y) => {
    const n = H[0].length, A = Array.from({ length: n }, () => new Array(n).fill(0)), b = new Array(n).fill(0);
    H.forEach((h, i) => { for (let r = 0; r < n; r++) { b[r] += h[r] * y[i]; for (let c = 0; c < n; c++) A[r][c] += h[r] * h[c]; } });
    return solve(A, b);
  };

  /* ================================================== Panel A: forest */
  const V = 343;                         // speed of sound, m/s
  const towers = [
    { x: 140, y: 110, f: 523, col: "gps", name: "A" },
    { x: 870, y: 170, f: 659, col: "gal", name: "B" },
    { x: 470, y: 530, f: 784, col: "bds", name: "C" },
  ];
  const L = { x: 600, y: 300 };          // listener (true)
  let sol = null;                        // solved position, or null
  // Play animation: elapsed true time since the bells rang (s), or null.
  // Why: the expanding wavefronts show that each arrival time is a travel
  // distance, and the moving line on the spectrogram ties map and plot.
  let anim = null;
  const rnd = G.rng(7), trees = Array.from({ length: 140 }, () => [rnd() * 1000, rnd() * 600, 3 + rnd() * 5]);
  const mapC = root.querySelector("#s01-map"), tfC = root.querySelector("#s01-tf");
  const vals = G.controls(root.querySelector("#s01-ctl"), [
    { id: "s01-b", key: "b", label: "True watch error b (hidden)", min: -1, max: 1, step: 0.01, value: 0.4, fmt: (v) => v.toFixed(2) + " s" },
    { id: "s01-bh", key: "bh", label: "Assumed watch error", min: -1.5, max: 1.5, step: 0.01, value: 0, fmt: (v) => v.toFixed(2) + " s" },
  ], () => { sol = null; drawA(); });
  const arrivals = () => towers.map((t) => Math.hypot(L.x - t.x, L.y - t.y) / V + vals.b);

  const drawMap = () => {
    const p = G.plot(mapC, { x: [0, 1000], y: [600, 0], noAxes: true, margin: { l: 2, r: 2, t: 2, b: 2 } });
    const c = p.ctx, C = G.col, sx = (p.x1 - p.x0) / 1000;
    c.fillStyle = G.alpha(C.ok, 0.07); c.fillRect(p.x0, p.y1, p.x1 - p.x0, p.y0 - p.y1);
    for (const [x, y, r] of trees) { c.fillStyle = G.alpha(C.ok, 0.22); c.beginPath(); c.arc(p.X(x), p.Y(y), r * sx * 2.2, 0, 7); c.fill(); }
    const T = arrivals();
    towers.forEach((t, i) => {
      const r = V * (T[i] - vals.bh);
      if (r > 0) { c.strokeStyle = C[t.col]; c.lineWidth = 2; c.setLineDash([6, 4]); c.beginPath(); c.arc(p.X(t.x), p.Y(t.y), r * sx, 0, 7); c.stroke(); c.setLineDash([]); }
    });
    if (anim !== null) {
      towers.forEach((t) => {
        // The wavefront grows at the speed of sound and stops at the listener.
        const d = Math.hypot(L.x - t.x, L.y - t.y), r = Math.min(V * anim, d), done = V * anim >= d;
        c.fillStyle = G.alpha(C[t.col], 0.07); c.strokeStyle = C[t.col]; c.lineWidth = done ? 3 : 2;
        c.beginPath(); c.arc(p.X(t.x), p.Y(t.y), r * sx, 0, 7); c.fill(); c.stroke();
        if (done) { c.lineWidth = 1.5; c.setLineDash([2, 3]); c.beginPath(); c.moveTo(p.X(t.x), p.Y(t.y)); c.lineTo(p.X(L.x), p.Y(L.y)); c.stroke(); c.setLineDash([]); }
      });
    }
    towers.forEach((t) => {
      const X = p.X(t.x), Y = p.Y(t.y);
      c.fillStyle = C[t.col]; c.beginPath(); c.moveTo(X, Y - 13); c.lineTo(X - 10, Y + 8); c.lineTo(X + 10, Y + 8); c.closePath(); c.fill();
      const right = t.x > 700; // keep labels inside the map
      p.text(t.x, t.y, `${t.name} · ${t.f} Hz`, { dx: right ? -14 : 14, dy: -6, align: right ? "right" : "left", halo: true, color: C.ink });
    });
    p.dot(L.x, L.y, { color: C.ink, r: 7 });
    p.text(L.x, L.y, "you", { dx: 11, dy: 0, halo: true });
    if (sol) {
      c.strokeStyle = C.warn; c.lineWidth = 2.5; const X = p.X(sol[0]), Y = p.Y(sol[1]);
      c.beginPath(); c.moveTo(X - 9, Y - 9); c.lineTo(X + 9, Y + 9); c.moveTo(X + 9, Y - 9); c.lineTo(X - 9, Y + 9); c.stroke();
    }
    // 100 m scale bar
    c.strokeStyle = C.ink; c.lineWidth = 2; const y = p.y0 - 14, x = p.x0 + 14;
    c.beginPath(); c.moveTo(x, y); c.lineTo(x + 100 * sx, y); c.stroke(); p.text(x + 50 * sx, y - 9, "100 m", { px: true, align: "center", halo: true });
  };
  const drawTF = () => {
    const T = arrivals();
    const p = G.plot(tfC, { x: [-1.6, 4.5], y: [450, 850], xlabel: "time on your watch (s)", ylabel: "Hz", ny: 3 });
    const c = p.ctx;
    p.vline(vals.bh, { color: G.col.ink2, dash: [4, 4] });
    p.text(vals.bh, 838, "assumed ring time", { dx: 4, color: G.col.ink2, font: `11px ${G.col.mono}` });
    towers.forEach((t, i) => {
      for (let k = 0; k < 24; k++) { // decaying chime
        const a = 0.95 * Math.exp(-k / 7);
        c.fillStyle = G.alpha(G.col[t.col], a); const x0 = p.X(T[i] + k * 0.04), x1 = p.X(T[i] + (k + 1) * 0.04);
        c.fillRect(x0, p.Y(t.f + 9), x1 - x0 + 0.5, p.Y(t.f - 9) - p.Y(t.f + 9));
      }
      p.text(T[i], t.f, t.name, { dx: -12, align: "center", color: G.col[t.col] });
    });
    if (anim !== null) {
      // Watch time now = true time since the ring + watch error b.
      const tw = anim + vals.b;
      p.vline(tw, { color: G.col.warn, width: 2 });
      p.text(tw, 470, `now ${anim.toFixed(2)} s after ring`, { dx: 5, color: G.col.warn, font: `600 11px ${G.col.mono}`, halo: true });
    }
  };
  const roA = root.querySelector("#s01-ro");
  const drawA = () => {
    drawMap(); drawTF();
    const T = arrivals();
    const items = towers.map((t, i) => [`Chime ${t.name} heard`, T[i].toFixed(3) + " s"]);
    if (sol) items.push(["Solved watch error", sol[2].toFixed(3) + " s", Math.abs(sol[2] - vals.b) < 0.01 ? "good" : "bad"]);
    G.readouts(roA, items);
  };
  G.mount(mapC, drawA); G.mount(tfC, drawTF);

  // Drag the listener.
  let drag = false;
  const toMap = (e) => { const r = mapC.getBoundingClientRect(); return [(e.clientX - r.left - 2) / (r.width - 4) * 1000, (e.clientY - r.top - 2) / (r.height - 4) * 600]; };
  mapC.addEventListener("pointerdown", (e) => { drag = true; mapC.setPointerCapture(e.pointerId); const [x, y] = toMap(e); L.x = x; L.y = y; sol = null; drawA(); });
  mapC.addEventListener("pointermove", (e) => { if (!drag) return; const [x, y] = toMap(e); L.x = Math.min(990, Math.max(10, x)); L.y = Math.min(590, Math.max(10, y)); sol = null; drawA(); });
  mapC.addEventListener("pointerup", () => (drag = false));
  mapC.style.cursor = "crosshair";

  // Gauss-Newton for (x, y, b) from three arrival times.
  root.querySelector("#s01-solve").addEventListener("click", () => {
    const T = arrivals(); let x = [500, 300, 0];
    for (let it = 0; it < 20; it++) {
      const H = [], y = [];
      towers.forEach((t, i) => { const d = Math.hypot(x[0] - t.x, x[1] - t.y) || 1; H.push([(x[0] - t.x) / d / V, (x[1] - t.y) / d / V, 1]); y.push(T[i] - (d / V + x[2])); });
      const dx = lsq(H, y); if (!dx) break; x = x.map((v, k) => v + dx[k]);
    }
    sol = x; vals.bh = x[2];
    const s = root.querySelector("#s01-bh"); s.value = x[2].toFixed(2); s.dispatchEvent(new Event("input"));
    sol = x; drawA();
  });

  // Play the chimes with Web Audio. Relative arrival times are in real seconds.
  root.querySelector("#s01-play").addEventListener("click", () => {
    const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
    const ac = G._ac || (G._ac = new AC()); ac.resume && ac.resume();
    const D = towers.map((t) => Math.hypot(L.x - t.x, L.y - t.y) / V), now = ac.currentTime + 0.15;
    // Animate in real time. The bells ring at now; each chime sounds when
    // its wavefront reaches the listener on the map.
    const tEnd = Math.max(...D) + 1.5, start = performance.now() + 150;
    const step = (ms) => {
      anim = Math.max(0, (ms - start) / 1000);
      if (anim > tEnd) { anim = null; drawA(); return; }
      drawA(); requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
    towers.forEach((t, i) => {
      const o = ac.createOscillator(), g = ac.createGain(); o.type = "sine"; o.frequency.value = t.f;
      const s = now + D[i];
      g.gain.setValueAtTime(0, s); g.gain.linearRampToValueAtTime(0.25, s + 0.01); g.gain.exponentialRampToValueAtTime(0.001, s + 1.4);
      o.connect(g).connect(ac.destination); o.start(s); o.stop(s + 1.5);
    });
  });

  /* ================================================== Panel B: satellites */
  const RS = 26560e3, VS = 3874;                  // GPS orbit radius (m) and speed (m/s)
  const lat0 = 40.0 * G.DEG, lon0 = -105.25 * G.DEG; // Boulder, display only
  const E0 = [-Math.sin(lon0), Math.cos(lon0), 0];
  const N0 = [-Math.sin(lat0) * Math.cos(lon0), -Math.sin(lat0) * Math.sin(lon0), Math.cos(lat0)];
  const U0 = [Math.cos(lat0) * Math.cos(lon0), Math.cos(lat0) * Math.sin(lon0), Math.sin(lat0)];
  const r0 = mul(U0, G.RE);
  const azel = [[35, 68], [115, 32], [205, 48], [290, 24], [165, 14], [330, 40]];
  const satCols = ["gps", "gal", "bds", "refl", "accent", "ok"];
  const rs = G.rng(11);
  const sats = azel.map(([az, el], i) => {
    const a = az * G.DEG, e = el * G.DEG;
    const u = add(add(mul(E0, Math.sin(a) * Math.cos(e)), mul(N0, Math.cos(a) * Math.cos(e))), mul(U0, Math.sin(e)));
    const ru = dot(r0, u), rho = -ru + Math.sqrt(ru * ru - G.RE * G.RE + RS * RS);
    const pos = add(r0, mul(u, rho));
    const k = unit([Math.cos(rs() * 6.28), Math.sin(rs() * 6.28), rs() - 0.5]);
    const vel = mul(unit(cross(pos, k)), VS);
    return { pos, vel, name: "SV" + (i + 1), col: satCols[i] };
  });
  const view = { az: lon0 + 0.85, el: lat0 - 0.2 }; // side-on: shows the up/down solution curve
  const globeC = root.querySelector("#s01-globe"), pulseC = root.querySelector("#s01-pulse"), resC = root.querySelector("#s01-resid");
  const v2 = G.controls(root.querySelector("#s01-ctl2"), [
    { id: "s01-n", key: "n", label: "Satellites used", min: 3, max: 6, step: 1, value: 3, fmt: (v) => v + "" },
    { id: "s01-e", key: "e", label: "Receiver east offset", min: -800, max: 800, step: 10, value: 250, fmt: (v) => v + " km" },
    { id: "s01-nn", key: "nn", label: "Receiver north offset", min: -800, max: 800, step: 10, value: -150, fmt: (v) => v + " km" },
    { id: "s01-bt", key: "bt", label: "True clock bias", min: -3, max: 3, step: 0.05, value: 1.2, fmt: (v) => v.toFixed(2) + " ms" },
    { id: "s01-ba", key: "ba", label: "Assumed clock bias", min: -6, max: 6, step: 0.05, value: 0, fmt: (v) => v.toFixed(2) + " ms" },
    { id: "s01-dop", key: "dop", label: "Show Doppler shift", type: "check", value: false },
  ], () => drawB());

  const truth = () => {
    const p = add(add(r0, mul(E0, v2.e * 1e3)), mul(N0, v2.nn * 1e3));
    return mul(unit(p), G.RE); // keep it on the sphere
  };
  const pseudo = () => { const r = truth(); return sats.map((s) => norm(sub(s.pos, r)) + G.C * v2.bt * 1e-3); };
  /** Best position for a fixed clock bias (ms) using the first n satellites. */
  const fixFor = (bms, n, rho) => {
    let x = r0.slice(); let rms = 0;
    for (let it = 0; it < 12; it++) {
      const H = [], y = [];
      for (let i = 0; i < n; i++) { const d = sub(x, sats[i].pos), r = norm(d); H.push(mul(d, 1 / r)); y.push(rho[i] - G.C * bms * 1e-3 - r); }
      const dx = lsq(H, y); if (!dx) break; x = add(x, dx);
      rms = Math.sqrt(y.reduce((a, v) => a + v * v, 0) / n);
    }
    // final residual
    let s2 = 0; for (let i = 0; i < n; i++) { const r = norm(sub(x, sats[i].pos)); const e = rho[i] - G.C * bms * 1e-3 - r; s2 += e * e; }
    rms = Math.sqrt(s2 / n);
    return { x, rms };
  };

  const proj = (w, h) => {
    const d = [Math.cos(view.el) * Math.cos(view.az), Math.cos(view.el) * Math.sin(view.az), Math.sin(view.el)];
    const right = unit(cross([0, 0, 1], d)), up = cross(d, right);
    // Zoomed view: Earth fills about 40 % of the frame. Satellites outside the
    // frame are drawn on the frame edge, in their true direction.
    const rmax = Math.min(w, h) / 2 - 18, k = rmax / (G.RE * 1.45);
    return (p) => {
      let x = k * dot(p, right), y = -k * dot(p, up); const r = Math.hypot(x, y), far = r > rmax;
      if (far) { x *= rmax / r; y *= rmax / r; }
      return [w / 2 + x, h / 2 + y, dot(p, d), k, far];
    };
  };
  const drawGlobe = () => {
    const { ctx: c, w, h } = G.size(globeC); const C = G.col, P = proj(w, h), k = P([0, 0, 0])[3];
    // Earth disk + graticule (front side only)
    c.fillStyle = G.alpha(C.gps, 0.08); c.strokeStyle = C.line; c.lineWidth = 1;
    c.beginPath(); c.arc(w / 2, h / 2, G.RE * k, 0, 7); c.fill(); c.stroke();
    c.strokeStyle = G.alpha(C.ink3, 0.35);
    const gline = (f) => { c.beginPath(); let pen = false; for (let t = 0; t <= 72; t++) { const q = P(f(t / 72 * 2 * Math.PI)); if (q[2] < 0) { pen = false; continue; } pen ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1]); pen = true; } c.stroke(); };
    for (let la = -60; la <= 60; la += 30) gline((t) => mul([Math.cos(la * G.DEG) * Math.cos(t), Math.cos(la * G.DEG) * Math.sin(t), Math.sin(la * G.DEG)], G.RE));
    for (let lo = 0; lo < 180; lo += 30) gline((t) => mul([Math.cos(t) * Math.cos(lo * G.DEG), Math.cos(t) * Math.sin(lo * G.DEG), Math.sin(t)], G.RE));
    // Frame edge = direction to satellites (they are 4x further than drawn).
    c.strokeStyle = G.alpha(C.ink3, 0.25); c.setLineDash([3, 5]); c.beginPath(); c.arc(w / 2, h / 2, Math.min(w, h) / 2 - 18, 0, 7); c.stroke(); c.setLineDash([]);
    const n = v2.n, rho = pseudo(), rt = truth(), qt = P(rt);
    // Solution curve for 3 satellites: one position per assumed bias.
    if (n === 3) {
      c.strokeStyle = C.warn; c.lineWidth = 2.5; c.beginPath(); let pen = false;
      for (let b = -6; b <= 6.001; b += 0.25) { const q = P(fixFor(b, 3, rho).x); if (q[2] < 0) { pen = false; continue; } pen ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1]); pen = true; }
      c.stroke();
    }
    sats.forEach((s, i) => {
      const q = P(s.pos), used = i < n;
      c.strokeStyle = G.alpha(C[s.col], used ? 0.8 : 0.15); c.lineWidth = used ? 1.5 : 1; c.beginPath(); c.moveTo(q[0], q[1]); c.lineTo(qt[0], qt[1]); c.stroke();
      c.fillStyle = used ? C[s.col] : G.alpha(C.ink3, 0.5); c.beginPath(); c.arc(q[0], q[1], used ? 6 : 4, 0, 7); c.fill();
      c.fillStyle = C.ink; c.font = `600 11px ${C.mono}`; c.fillText(s.name, q[0] + 8, q[1] - 6);
    });
    c.fillStyle = C.ink; c.beginPath(); c.arc(qt[0], qt[1], 5, 0, 7); c.fill();
    const est = fixFor(v2.ba, n, rho), qe = P(est.x);
    c.strokeStyle = C.warn; c.lineWidth = 2.5; c.beginPath(); c.moveTo(qe[0] - 7, qe[1] - 7); c.lineTo(qe[0] + 7, qe[1] + 7); c.moveTo(qe[0] + 7, qe[1] - 7); c.lineTo(qe[0] - 7, qe[1] + 7); c.stroke();
    c.font = `11px ${C.mono}`; c.fillStyle = C.ink2;
    c.fillText("● truth  ✕ estimate" + (n === 3 ? "  ━ all exact fits" : ""), 10, h - 10);
    c.fillText("satellites ~20 200 km up, drawn at the frame edge", 10, h - 26);
    return { est, rho };
  };
  const drawPulse = (rho) => {
    const ms = rho.map((r) => r / G.C * 1e3), lo = Math.floor(Math.min(...ms) - 3), hi = Math.ceil(Math.max(...ms) + 3);
    const p = G.plot(pulseC, { x: [lo, hi], y: [-0.5, sats.length - 0.5], xlabel: "arrival time on receiver clock (ms)", yticks: [], margin: { l: 44 } });
    const c = p.ctx, rt = truth();
    sats.forEach((s, i) => {
      const used = i < v2.n; const los = unit(sub(s.pos, rt)); const fd = -dot(s.vel, los) / G.LAM_L1; // Hz
      const yc = i + (v2.dop ? fd / 5000 * 0.42 : 0);
      c.fillStyle = used ? G.col[s.col] : G.alpha(G.col.ink3, 0.35);
      const x0 = p.X(ms[i]), x1 = p.X(ms[i] + 1.0);
      c.fillRect(x0, p.Y(yc + 0.12), Math.max(2, x1 - x0), p.Y(yc - 0.12) - p.Y(yc + 0.12));
      p.text(lo, i, s.name, { dx: -38, color: G.col.ink2, font: `11px ${G.col.mono}` });
      if (v2.dop) p.text(ms[i] + 1.2, yc, (fd >= 0 ? "+" : "") + (fd / 1000).toFixed(2) + " kHz", { font: `11px ${G.col.mono}`, color: G.col.ink2 });
      p.hline(i, { color: G.alpha(G.col.ink3, 0.3), width: 1, dash: [2, 4] });
    });
  };
  const drawRes = (rho) => {
    // Floor at 1 m: below that the fit is exact for teaching purposes.
    const FL = 1e-3;
    const p = G.plot(resC, { x: [-6, 6], y: [3e-4, 1e4], logy: true, xlabel: "assumed clock bias (ms)", ylabel: "km", yfmt: (v) => (v >= 1 ? v.toFixed(0) : v.toString()) });
    p.hband(3e-4, FL, { fill: G.alpha(G.col.ok, 0.12) });
    p.text(-5.8, 5.5e-4, "exact fit (< 1 m)", { color: G.col.ok, font: `11px ${G.col.mono}` });
    // Two curves. The residual says how well the ranges agree. The position
    // error says how far the fit is from the truth. With 3 satellites the
    // residual is zero at every bias (3 equations, 3 position unknowns), so
    // only the position error shows that most of those fits are wrong.
    const rt = truth(), xs = [], ys = [], es = [];
    for (let b = -6; b <= 6.001; b += 0.05) {
      const f = fixFor(b, v2.n, rho); xs.push(b);
      ys.push(Math.max(FL, f.rms / 1e3)); es.push(Math.max(FL, norm(sub(f.x, rt)) / 1e3));
    }
    p.line(xs, es, { color: G.col.warn, width: 1.8, dash: [6, 4] });
    p.line(xs, ys, { color: G.col.accent, width: 2.6 });
    p.text(-5.8, 0.3, "— fit residual", { align: "left", color: G.col.accent, font: `600 11px ${G.col.mono}`, halo: true });
    p.text(-5.8, 0.07, "- - position error", { align: "left", color: G.col.warn, font: `600 11px ${G.col.mono}`, halo: true });
    p.vline(v2.bt, { color: G.col.ok, dash: [5, 4] }); p.text(v2.bt, 4e2, "true", { dx: 4, color: G.col.ok, font: `11px ${G.col.mono}` });
    p.dot(v2.ba, Math.max(FL, fixFor(v2.ba, v2.n, rho).rms / 1e3), { color: G.col.warn });
  };
  const roB = root.querySelector("#s01-ro2");
  const drawB = () => {
    const { est, rho } = drawGlobe(); drawPulse(rho); drawRes(rho);
    const err = norm(sub(est.x, truth()));
    G.readouts(roB, [
      ["Unknowns / equations", `4 / ${v2.n}`, v2.n >= 4 ? "good" : "bad"],
      ["Position error", G.si(err), err < 50 ? "good" : "bad"],
      ["Fit residual", G.si(est.rms), est.rms < 1 ? "good" : ""],
      ["1 ms of clock error", "299.8 km"],
    ]);
  };
  G.mount(globeC, drawB); G.mount(pulseC, drawB); G.mount(resC, drawB);
  // Rotate the globe.
  let gd = null;
  globeC.addEventListener("pointerdown", (e) => { gd = [e.clientX, e.clientY, view.az, view.el]; globeC.setPointerCapture(e.pointerId); });
  globeC.addEventListener("pointermove", (e) => { if (!gd) return; view.az = gd[2] - (e.clientX - gd[0]) * 0.008; view.el = Math.max(-1.4, Math.min(1.4, gd[3] + (e.clientY - gd[1]) * 0.008)); drawB(); });
  globeC.addEventListener("pointerup", () => (gd = null));
  globeC.style.cursor = "grab";
});
