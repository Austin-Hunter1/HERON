/* s10.js - Part 10: GNSS-R geometry, Fresnel zones and an open-loop error budget.
 * Flat surface, satellite at infinity (plane wave). Coordinates: x along the
 * satellite azimuth, y across, z up; antenna at (0, 0, h). The Fresnel picture
 * uses the exact extra path, not the quadratic approximation, so the drawn
 * ellipse (approximation) and the colour bands (exact) can be compared.
 * Open-loop terms: tropo refractivity N ~ 300 ppm near the ground (assumed
 * standard atmosphere); roughness uses the Rayleigh coherent loss factor. */
G.section("s10", (root) => {
  const $ = (s) => root.querySelector(s);
  const geoC = $("#s10-geo");
  const v = G.controls($("#s10-ctl"), [
    { id: "s10-h", key: "h", label: "Antenna height h", min: 2, max: 400, step: 1, value: 100, fmt: (x) => x + " m" },
    { id: "s10-e", key: "e", label: "Elevation e", min: 5, max: 90, step: 1, value: 45, fmt: (x) => x + "°" },
  ], () => { drawGeo(); drawFz(); drawOL(); });
  const drawGeo = () => {
    const { ctx: c, w, h } = G.size(geoC), C = G.col, e = v.e * G.DEG, H = v.h;
    // Room above the antenna for the incoming rays, and H below the surface for the image antenna.
    const d = H / Math.tan(e), span = Math.max(d * 1.25 + H * 0.4, H * 1.6), sc = Math.min((w - 40) / span, (h - 24) / (2.7 * H));
    const gy = 12 + 1.65 * H * sc, ax = 30 + H * 0.15 * sc, X = (x) => ax + x * sc, Y = (z) => gy - z * sc;
    // water and image region
    c.fillStyle = G.alpha(C.gps, 0.1); c.fillRect(0, gy, w, h - gy);
    c.strokeStyle = C.ink; c.lineWidth = 2; c.beginPath(); c.moveTo(0, gy); c.lineTo(w, gy); c.stroke();
    // incoming parallel rays from upper right
    const ux = Math.cos(e), uz = Math.sin(e), L = 2000;
    const A = [0, H], S = [d, 0], Im = [0, -H];
    c.lineWidth = 2.5;
    c.strokeStyle = C.gps; c.beginPath(); c.moveTo(X(A[0] + ux * L), Y(A[1] + uz * L)); c.lineTo(X(A[0]), Y(A[1])); c.stroke();
    c.strokeStyle = C.refl; c.beginPath(); c.moveTo(X(S[0] + ux * L), Y(S[1] + uz * L)); c.lineTo(X(S[0]), Y(S[1])); c.lineTo(X(A[0]), Y(A[1])); c.stroke();
    c.setLineDash([5, 4]); c.lineWidth = 1.5; c.beginPath(); c.moveTo(X(S[0]), Y(S[1])); c.lineTo(X(Im[0]), Y(Im[1])); c.stroke(); c.setLineDash([]);
    // projection: foot of the perpendicular from the antenna onto the reflected ray line through the image
    const t = (A[0] - Im[0]) * ux + (A[1] - Im[1]) * uz, F = [Im[0] + ux * t, Im[1] + uz * t];
    c.strokeStyle = C.warn; c.lineWidth = 3; c.beginPath(); c.moveTo(X(Im[0]), Y(Im[1])); c.lineTo(X(F[0]), Y(F[1])); c.stroke();
    c.strokeStyle = C.ink3; c.lineWidth = 1; c.setLineDash([3, 3]); c.beginPath(); c.moveTo(X(A[0]), Y(A[1])); c.lineTo(X(F[0]), Y(F[1])); c.moveTo(X(A[0]), Y(A[1])); c.lineTo(X(Im[0]), Y(Im[1])); c.stroke(); c.setLineDash([]);
    // markers and labels
    const mark = (P, col, lab, dx = 10, dy = -8) => { c.fillStyle = col; c.beginPath(); c.arc(X(P[0]), Y(P[1]), 5, 0, 7); c.fill(); c.fillStyle = C.ink; c.font = `600 12px ${C.mono}`; c.textAlign = "left"; c.fillText(lab, X(P[0]) + dx, Y(P[1]) + dy); };
    // drone icon
    c.fillStyle = C.ink; c.fillRect(X(0) - 12, Y(H) - 3, 24, 6); c.fillRect(X(0) - 16, Y(H) - 6, 8, 2); c.fillRect(X(0) + 8, Y(H) - 6, 8, 2);
    mark(A, C.ink, "antenna, h = " + H + " m", 16, -10); mark(S, C.refl, "specular point", 8, 18); mark(Im, C.ink3, "image antenna", 10, 4);
    c.fillStyle = C.warn; c.fillText("Δρ = 2h sin e = " + (2 * H * Math.sin(e)).toFixed(1) + " m", X((Im[0] + F[0]) / 2) + 10, Y((Im[1] + F[1]) / 2));
    c.fillStyle = C.ink2; c.fillText("d = " + d.toFixed(1) + " m", X(d / 2) - 20, gy + 16);
    c.fillStyle = C.gps; c.fillText("direct", X(ux * H * 0.5) + 8, Y(H + uz * H * 0.5));
    c.fillStyle = C.refl; c.fillText("to satellite (parallel rays)", Math.min(w - 210, X(S[0] + ux * H * 0.9)), Math.max(14, Y(uz * H * 0.9)));
    const dr = 2 * H * Math.sin(e);
    G.readouts($("#s10-ro"), [
      ["Specular offset d", d.toFixed(1) + " m"],
      ["Path difference Δρ", dr.toFixed(2) + " m"],
      ["Delay", (dr / G.C * 1e9).toFixed(1) + " ns"],
      ["In L1 C/A chips", (dr / 293.05).toFixed(3)],
      ["In L5 chips", (dr / 29.305).toFixed(2)],
      ["In L1 cycles", (dr / G.LAM_L1).toFixed(0)],
      ["∂Δρ/∂h", (2 * Math.sin(e)).toFixed(3)],
    ]);
  };
  G.stepper($("#s10-geo").closest(".panel"));
  G.mount(geoC, drawGeo);

  /* ---------- Fresnel zones ---------- */
  const fzC = $("#s10-fz");
  const v2 = G.controls($("#s10-ctl2"), [
    { id: "s10-lam", key: "lam", label: "Carrier", type: "select", value: "l1", options: [["l1", "L1 / E1 / B1C (19.0 cm)"], ["l5", "L5 / E5a / B2a (25.5 cm)"]] },
    { id: "s10-canal", key: "canal", label: "Water strip width", min: 0, max: 60, step: 1, value: 20, fmt: (x) => (x ? x + " m" : "off") },
  ], () => drawFz());
  const drawFz = () => {
    const C = G.col, e = v.e * G.DEG, H = v.h, lam = v2.lam === "l5" ? G.LAM_L5 : G.LAM_L1;
    const b = Math.sqrt(lam * H / Math.sin(e) + (lam / (2 * Math.sin(e))) ** 2), a = b / Math.sin(e);
    const ext = Math.max(a, b) * 2.4;
    const p = G.plot(fzC, { x: [-ext, ext], y: [-ext * 0.75, ext * 0.75], xlabel: "along azimuth from specular point (m)", ylabel: "across (m)" });
    // exact extra path on a pixel grid
    const W = 180, Hh = 135, img = p.ctx.createImageData(W, Hh), ux = Math.cos(e), uz = Math.sin(e), d = H / Math.tan(e);
    const base = Math.hypot(d, H) - (ux * d);
    const col1 = hex(C.accent), col0 = hex(C.surface), colE = hex(C.gps);
    for (let j = 0; j < Hh; j++) for (let i = 0; i < W; i++) {
      const x = -ext + 2 * ext * (i + 0.5) / W, y = ext * 0.75 - 1.5 * ext * (j + 0.5) / Hh;
      const Px = d + x, Py = y, path = Math.hypot(Px, Py, H) - ux * Px, ex = path - base;
      const zone = Math.floor(ex / (lam / 2));
      const k = 4 * (j * W + i), cc = zone === 0 ? col1 : (zone % 2 ? col0 : colE);
      const al = zone === 0 ? 0.85 : 0.25;
      img.data[k] = cc[0]; img.data[k + 1] = cc[1]; img.data[k + 2] = cc[2]; img.data[k + 3] = 255 * al;
    }
    const off = document.createElement("canvas"); off.width = W; off.height = Hh; off.getContext("2d").putImageData(img, 0, 0);
    p.ctx.save(); p.ctx.imageSmoothingEnabled = false; p.ctx.drawImage(off, p.X(-ext), p.Y(ext * 0.75), p.X(ext) - p.X(-ext), p.Y(-ext * 0.75) - p.Y(ext * 0.75)); p.ctx.restore();
    p.axes();
    // approximate ellipse
    const c = p.ctx; c.strokeStyle = C.ink; c.lineWidth = 2; c.setLineDash([6, 4]); c.beginPath();
    c.ellipse(p.X(0), p.Y(0), Math.abs(p.X(a) - p.X(0)), Math.abs(p.Y(b) - p.Y(0)), 0, 0, 7); c.stroke(); c.setLineDash([]);
    if (v2.canal > 0) { c.strokeStyle = C.refl; c.lineWidth = 2; const x0 = p.X(-v2.canal / 2), x1 = p.X(v2.canal / 2); c.strokeRect(x0, p.y1, x1 - x0, p.y0 - p.y1); p.text(v2.canal / 2, ext * 0.68, " water strip", { color: C.refl, halo: true }); }
    p.dot(0, 0, { color: C.refl, r: 4 });
    G.readouts($("#s10-ro2"), [
      ["Semi-axis a (along)", a.toFixed(1) + " m"],
      ["Semi-axis b (across)", b.toFixed(1) + " m"],
      ["Zone area", (Math.PI * a * b).toFixed(0) + " m²"],
      ["Strip covers zone?", v2.canal >= 2 * a ? "yes, full width" : v2.canal > 0 ? (100 * v2.canal / (2 * a)).toFixed(0) + " % of 2a" : "-", v2.canal >= 2 * a ? "good" : ""],
    ]);
  };
  function hex(h) { const n = parseInt((h || "#888888").replace("#", ""), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  G.mount(fzC, drawFz);

  /* ---------- open loop: waveforms, roughness, error budget ---------- */
  const wfC = $("#s10-wf"), cohC = $("#s10-coh");
  const v3 = G.controls($("#s10-ctl3"), [
    { id: "s10-sig", key: "sig", label: "Signal", type: "select", value: "l1", options: [["l1", "L1 C/A (1.023 Mchip/s)"], ["l5", "L5 / E5a / B2a (10.23 Mchip/s)"]] },
    { id: "s10-dh", key: "dh", label: "Height model error", min: -10, max: 10, step: 0.1, value: 2, fmt: (x) => x.toFixed(1) + " m" },
    { id: "s10-sr", key: "sr", label: "Surface roughness σ_s", min: 0, max: 0.2, step: 0.002, value: 0.02, fmt: (x) => (x * 100).toFixed(1) + " cm" },
    { id: "s10-hdot", key: "hd", label: "Climb rate ḣ", min: -5, max: 5, step: 0.1, value: 1, fmt: (x) => x.toFixed(1) + " m/s" },
    { id: "s10-cab", key: "cab", label: "Uncalibrated channel delay", min: 0, max: 5, step: 0.1, value: 0.5, fmt: (x) => x.toFixed(1) + " ns" },
  ], () => drawOL());
  const drawOL = () => {
    const C = G.col, e = v.e * G.DEG, H = v.h, chip = v3.sig === "l5" ? 29.305 : 293.05, lam = v3.sig === "l5" ? G.LAM_L5 : G.LAM_L1;
    const dr = 2 * H * Math.sin(e), drModel = 2 * (H + v3.dh) * Math.sin(e);
    const coh = Math.exp(-Math.pow(4 * Math.PI * v3.sr * Math.sin(e) / lam, 2));
    const refl = 0.35;  // display amplitude of the reflected peak (relative to direct)
    const xr = Math.max(2.5, dr / chip + 1.8);
    const p = G.plot(wfC, { x: [-1.5, xr], y: [0, 1.12], xlabel: "delay relative to direct (chips of " + (v3.sig === "l5" ? "L5" : "L1 C/A") + ")", ylabel: "|R|" });
    // open-loop correlator window centred on the model delay: ±1 chip
    p.band(drModel / chip - 1, drModel / chip + 1, { fill: G.alpha(C.refl, 0.1) });
    p.vline(drModel / chip, { color: C.refl, dash: [5, 4] });
    p.fn((x) => G.tri(x), { color: C.gps, width: 2.2 }, 600);
    p.fn((x) => refl * Math.sqrt(coh) * G.tri(x - dr / chip), { color: C.refl, width: 2.2 }, 600);
    p.fn((x) => refl * Math.sqrt(1 - coh) * 0.6 * G.tri((x - dr / chip) / 1.15), { color: C.ink3, width: 1.4, dash: [3, 3] }, 600);
    p.text(0, 1.05, "direct (up antenna)", { align: "center", color: C.gps, font: `11px ${C.mono}` });
    p.text(dr / chip, refl * Math.sqrt(coh) + 0.08, "reflected, coherent", { align: "center", color: C.refl, font: `11px ${C.mono}` });
    p.text(drModel / chip, 0.95, "model", { dx: 4, color: C.refl, font: `11px ${C.mono}` });
    // coherence vs roughness
    const q = G.plot(cohC, { x: [0, 20], y: [0, 1.05], xlabel: "surface roughness σ_s (cm)", ylabel: "coherent power" });
    q.fn((s) => Math.exp(-Math.pow(4 * Math.PI * s / 100 * Math.sin(e) / G.LAM_L1, 2)), { color: C.gps, width: 2 });
    q.fn((s) => Math.exp(-Math.pow(4 * Math.PI * s / 100 * Math.sin(e) / G.LAM_L5, 2)), { color: C.bds, width: 2, dash: [6, 3] });
    q.dot(v3.sr * 100, coh, { color: C.refl });
    q.text(19.5, 0.95, "L1 solid · L5 dashed", { align: "right", color: C.ink2, font: `11px ${C.mono}` });
    const rayleigh = lam / (8 * Math.sin(e));
    // error budget, metres of path and of height
    const dT = 300e-6 * dr;                     // extra tropo along the extra path (N ~ 300)
    const dCab = v3.cab * 1e-9 * G.C;
    const fdr = -(2 / lam) * (v3.hd * Math.sin(e) + H * Math.cos(e) * (0.5 * G.DEG / 60)); // e-dot ~ 0.5 deg/min
    const toH = (m) => (m / (2 * Math.sin(e)) * 100).toFixed(1) + " cm";
    G.readouts($("#s10-ro3"), [
      ["Reflected Doppler offset", fdr.toFixed(2) + " Hz"],
      ["Model delay error", ((drModel - dr) / chip).toFixed(3) + " chip", Math.abs(drModel - dr) < chip / 2 ? "good" : "bad"],
      ["Extra tropo ΔT", (dT * 100).toFixed(1) + " cm → h " + toH(dT)],
      ["Channel delay", (dCab * 100).toFixed(0) + " cm → h " + toH(dCab)],
      ["Coherent power", (100 * coh).toFixed(0) + " %", coh > 0.5 ? "good" : "bad"],
      ["Rayleigh limit λ/(8 sin e)", (rayleigh * 100).toFixed(1) + " cm"],
    ]);
  };
  G.mount(wfC, drawOL); G.mount(cohC, drawOL);
});
