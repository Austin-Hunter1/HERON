/* core.js - shared helpers for the HERON GNSS course page.
 *
 * Every section script uses the global object G. G gives:
 *   - theme colours read from the CSS tokens (so plots follow light/dark),
 *   - a small canvas plotting class (Plot), a heat map, and a 3D surface,
 *   - slider/checkbox builders that keep the HTML short,
 *   - GNSS helpers: C/A code generator, seeded random numbers, constants.
 * Section scripts call G.section(id, initFn). The core runs each initFn once
 * after the page loads, and redraws every mounted figure on resize or theme
 * change. Keep this file free of section-specific logic.
 */
"use strict";
const G = (() => {
  const G = {};

  /* ---------------- constants ---------------- */
  G.C = 299792458;                 // speed of light, m/s
  G.F_L1 = 1575.42e6;              // L1 / E1 / B1C carrier, Hz
  G.F_L5 = 1176.45e6;              // L5 / E5a / B2a carrier, Hz
  G.LAM_L1 = G.C / G.F_L1;
  G.LAM_L5 = G.C / G.F_L5;
  G.RE = 6371e3;                   // mean Earth radius, m (display geometry only)
  G.DEG = Math.PI / 180;

  /* ---------------- theme ---------------- */
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  G.col = {};
  G.readTheme = () => {
    for (const k of ["bg","surface","ink","ink2","ink3","line","grid","eqbg","accent","gps","gal","bds","refl","ok","warn"]) {
      G.col[k] = css("--" + k);
    }
    G.col.mono = css("--ff-m") || "monospace";
  };
  /** Return a colour with an alpha value. Works for #rrggbb tokens. */
  G.alpha = (hex, a) => {
    const h = hex.replace("#", "");
    if (h.length !== 6) return hex;
    const n = parseInt(h, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  };
  /** Sequential colour map (dark blue to yellow). Readable in both themes. */
  const CMAP = [[13,8,135],[84,2,163],[139,10,165],[185,50,137],[219,92,104],[244,136,73],[254,188,43],[240,249,33]];
  G.cmap = (v) => {
    v = Math.min(1, Math.max(0, v)) * (CMAP.length - 1);
    const i = Math.min(CMAP.length - 2, Math.floor(v)), f = v - i;
    const a = CMAP[i], b = CMAP[i + 1];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
  };
  G.cmapStr = (v, a = 1) => { const c = G.cmap(v); return `rgba(${c[0]|0},${c[1]|0},${c[2]|0},${a})`; };

  /* ---------------- figure registry ---------------- */
  const mounted = [];
  /** Attach a draw function to a canvas. The core calls it on resize and theme change. */
  G.mount = (canvas, draw) => {
    const rec = { canvas, draw, w: 0 };
    mounted.push(rec);
    const ro = new ResizeObserver(() => {
      const w = canvas.clientWidth;
      if (w && w !== rec.w) { rec.w = w; safe(draw); }
    });
    ro.observe(canvas);
    return () => safe(draw);
  };
  const safe = (fn) => { try { fn(); } catch (e) { console.error(e); } };
  G.redrawAll = () => { G.readTheme(); mounted.forEach((r) => safe(r.draw)); };

  /** Size a canvas for the device pixel ratio. Height = width * aspect (data-aspect). */
  G.size = (canvas) => {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(200, canvas.clientWidth);
    const asp = parseFloat(canvas.dataset.aspect || "0.5");
    const minH = parseFloat(canvas.dataset.minh || "160");
    const h = Math.max(minH, Math.round(w * asp));
    canvas.style.height = h + "px";
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  };

  /* ---------------- 2D plot ---------------- */
  /** Nice tick values for an axis range. */
  G.ticks = (a, b, n = 5) => {
    const span = Math.abs(b - a) || 1;
    const raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
    const out = [], lo = Math.min(a, b), hi = Math.max(a, b);
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
    return out;
  };
  G.fmtTick = (v) => {
    const a = Math.abs(v);
    if (a === 0) return "0";
    if (a >= 1e5 || a < 1e-3) return v.toExponential(0);
    return +v.toPrecision(4) + "";
  };

  class Plot {
    constructor(canvas, o = {}) {
      const { ctx, w, h } = o._sized || G.size(canvas);
      this.ctx = ctx; this.w = w; this.h = h; this.o = o;
      const m = Object.assign({ l: 56, r: 14, t: 12, b: 38 }, o.margin || {});
      if (o.noAxes) Object.assign(m, o.margin || { l: 4, r: 4, t: 4, b: 4 });
      this.m = m;
      this.x0 = m.l; this.x1 = w - m.r; this.y0 = h - m.b; this.y1 = m.t;
      this.xr = o.x || [0, 1]; this.yr = o.y || [0, 1];
      if (!o.noAxes) this.axes();
    }
    X(v) { const [a, b] = this.xr; return this.x0 + (v - a) / (b - a) * (this.x1 - this.x0); }
    Y(v) {
      const [a, b] = this.yr;
      if (this.o.logy) { v = Math.log10(Math.max(v, 1e-30)); return this.y0 + (v - Math.log10(a)) / (Math.log10(b) - Math.log10(a)) * (this.y1 - this.y0); }
      return this.y0 + (v - a) / (b - a) * (this.y1 - this.y0);
    }
    invX(px) { const [a, b] = this.xr; return a + (px - this.x0) / (this.x1 - this.x0) * (b - a); }
    invY(py) { const [a, b] = this.yr; return a + (py - this.y0) / (this.y1 - this.y0) * (b - a); }
    axes() {
      const c = this.ctx, o = this.o, C = G.col;
      c.save();
      c.font = `11px ${C.mono}`; c.fillStyle = C.ink3; c.strokeStyle = C.grid; c.lineWidth = 1;
      const xt = o.xticks || G.ticks(this.xr[0], this.xr[1], o.nx || Math.max(3, Math.floor((this.x1 - this.x0) / 80)));
      let yt;
      if (o.logy) { yt = []; for (let e = Math.ceil(Math.log10(this.yr[0])); e <= Math.log10(this.yr[1]); e++) yt.push(Math.pow(10, e)); }
      else yt = o.yticks || G.ticks(this.yr[0], this.yr[1], o.ny || Math.max(3, Math.floor((this.y0 - this.y1) / 45)));
      c.textAlign = "center"; c.textBaseline = "top";
      for (const v of xt) { const x = this.X(v); if (x < this.x0 - 1 || x > this.x1 + 1) continue;
        c.beginPath(); c.moveTo(x, this.y0); c.lineTo(x, this.y1); c.stroke();
        c.fillText((o.xfmt || G.fmtTick)(v), x, this.y0 + 5); }
      c.textAlign = "right"; c.textBaseline = "middle";
      for (const v of yt) { const y = this.Y(v); if (y > this.y0 + 1 || y < this.y1 - 1) continue;
        c.beginPath(); c.moveTo(this.x0, y); c.lineTo(this.x1, y); c.stroke();
        c.fillText((o.yfmt || G.fmtTick)(v), this.x0 - 6, y); }
      c.strokeStyle = C.ink3; c.beginPath(); c.moveTo(this.x0, this.y1); c.lineTo(this.x0, this.y0); c.lineTo(this.x1, this.y0); c.stroke();
      c.fillStyle = C.ink2; c.font = `12px ${C.mono}`;
      if (o.xlabel) { c.textAlign = "center"; c.textBaseline = "bottom"; c.fillText(o.xlabel, (this.x0 + this.x1) / 2, this.h - 2); }
      if (o.ylabel) { c.save(); c.translate(12, (this.y0 + this.y1) / 2); c.rotate(-Math.PI / 2); c.textAlign = "center"; c.textBaseline = "middle"; c.fillText(o.ylabel, 0, 0); c.restore(); }
      c.restore();
    }
    clip(on = true) { const c = this.ctx; if (on) { c.save(); c.beginPath(); c.rect(this.x0, this.y1, this.x1 - this.x0, this.y0 - this.y1); c.clip(); } else c.restore(); }
    style(s = {}) {
      const c = this.ctx; c.strokeStyle = s.color || G.col.accent; c.fillStyle = s.fill || s.color || G.col.accent;
      c.lineWidth = s.width || 1.8; c.setLineDash(s.dash || []); c.globalAlpha = s.alpha == null ? 1 : s.alpha;
      c.lineJoin = "round"; c.lineCap = "round";
    }
    line(xs, ys, s = {}) {
      const c = this.ctx; this.clip(); this.style(s); c.beginPath();
      let pen = false;
      for (let i = 0; i < xs.length; i++) {
        const y = ys[i]; if (y == null || !isFinite(y)) { pen = false; continue; }
        const X = this.X(xs[i]), Y = this.Y(y);
        if (!pen) { c.moveTo(X, Y); pen = true; } else c.lineTo(X, Y);
      }
      c.stroke(); c.globalAlpha = 1; c.setLineDash([]); this.clip(false);
    }
    area(xs, ys, base, s = {}) {
      const c = this.ctx; this.clip(); this.style(s); c.beginPath();
      c.moveTo(this.X(xs[0]), this.Y(base));
      for (let i = 0; i < xs.length; i++) c.lineTo(this.X(xs[i]), this.Y(ys[i]));
      c.lineTo(this.X(xs[xs.length - 1]), this.Y(base)); c.closePath(); c.fill();
      c.globalAlpha = 1; this.clip(false);
    }
    fn(f, s = {}, n = 400) {
      const xs = [], ys = []; const [a, b] = this.xr;
      for (let i = 0; i <= n; i++) { const x = a + (b - a) * i / n; xs.push(x); ys.push(f(x)); }
      this.line(xs, ys, s); return [xs, ys];
    }
    pts(xs, ys, s = {}) {
      const c = this.ctx; this.clip(); this.style(s); const r = s.r || 2;
      for (let i = 0; i < xs.length; i++) { c.beginPath(); c.arc(this.X(xs[i]), this.Y(ys[i]), r, 0, 2 * Math.PI); c.fill(); }
      c.globalAlpha = 1; this.clip(false);
    }
    vline(x, s = {}) { this.line([x, x], [this.yr[0], this.yr[1]], Object.assign({ width: 1.2 }, s)); }
    hline(y, s = {}) { this.line([this.xr[0], this.xr[1]], [y, y], Object.assign({ width: 1.2 }, s)); }
    band(xa, xb, s = {}) {
      const c = this.ctx; this.clip(); c.fillStyle = s.fill || G.alpha(G.col.accent, 0.12);
      c.fillRect(this.X(xa), this.y1, this.X(xb) - this.X(xa), this.y0 - this.y1); this.clip(false);
    }
    hband(ya, yb, s = {}) {
      const c = this.ctx; this.clip(); c.fillStyle = s.fill || G.alpha(G.col.accent, 0.12);
      c.fillRect(this.x0, this.Y(yb), this.x1 - this.x0, this.Y(ya) - this.Y(yb)); this.clip(false);
    }
    text(x, y, str, s = {}) {
      const c = this.ctx; c.save(); c.fillStyle = s.color || G.col.ink; c.font = s.font || `600 12px ${G.col.mono}`;
      c.textAlign = s.align || "left"; c.textBaseline = s.base || "middle";
      const px = s.px ? x : this.X(x), py = s.px ? y : this.Y(y);
      if (s.halo) { c.lineWidth = 4; c.strokeStyle = G.col.surface; c.strokeText(str, px + (s.dx || 0), py + (s.dy || 0)); }
      c.fillText(str, px + (s.dx || 0), py + (s.dy || 0)); c.restore();
    }
    dot(x, y, s = {}) {
      const c = this.ctx; c.save(); c.fillStyle = s.color || G.col.accent; c.strokeStyle = s.ring || G.col.surface; c.lineWidth = 2;
      c.beginPath(); c.arc(this.X(x), this.Y(y), s.r || 5, 0, 2 * Math.PI); c.fill(); c.stroke(); c.restore();
    }
  }
  G.Plot = Plot;
  G.plot = (canvas, o) => new Plot(canvas, o);
  /** Several plots stacked vertically on one canvas, sharing the x axis width.
   *  Only the bottom plot gets the x label. */
  G.stack = (canvas, specs) => {
    const s = G.size(canvas), n = specs.length, H = s.h;
    return specs.map((o, i) => new Plot(canvas, Object.assign({}, o, {
      _sized: s,
      margin: Object.assign({ l: 56, r: 14 }, o.margin || {}, { t: H * i / n + 8, b: H - H * (i + 1) / n + (i === n - 1 ? 34 : 14) }),
    })));
  };

  /* ---------------- heat map ---------------- */
  /** Draw Z[row][col] into a plot area. Rows map to y, cols map to x. */
  G.heat = (p, Z, xr, yr, zr) => {
    const rows = Z.length, cols = Z[0].length;
    const img = p.ctx.createImageData(cols, rows);
    const [z0, z1] = zr;
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      const v = (Z[r][c] - z0) / (z1 - z0); const col = G.cmap(v); const k = 4 * ((rows - 1 - r) * cols + c);
      img.data[k] = col[0]; img.data[k + 1] = col[1]; img.data[k + 2] = col[2]; img.data[k + 3] = 255;
    }
    const off = document.createElement("canvas"); off.width = cols; off.height = rows; off.getContext("2d").putImageData(img, 0, 0);
    const c = p.ctx; c.save(); c.imageSmoothingEnabled = false;
    const xa = p.X(xr[0]), xb = p.X(xr[1]), ya = p.Y(yr[1]), yb = p.Y(yr[0]);
    c.drawImage(off, xa, ya, xb - xa, yb - ya); c.restore();
  };

  /* ---------------- 3D surface ---------------- */
  /** Draw a rotatable surface. Z[row][col]; rows = y axis, cols = x axis.
   *  opts: {xr, yr, zr, xlabel, ylabel, zlabel, thr (z value), state} */
  G.surface = (canvas, Z, o) => {
    const st = o.state; if (st.az == null) { st.az = -0.9; st.el = 0.55; }
    const { ctx: c, w, h } = G.size(canvas); const C = G.col;
    const rows = Z.length, cols = Z[0].length;
    const [z0, z1] = o.zr;
    const nz = (v) => (Math.min(Math.max(v, z0), z1) - z0) / (z1 - z0);
    const sc = Math.min(w, h * 1.5) * 0.31, cx = w / 2, cy = h * 0.58;
    const ca = Math.cos(st.az), sa = Math.sin(st.az), ce = Math.cos(st.el), se = Math.sin(st.el);
    const P = (u, v, z) => { // u,v in [-1,1], z in [0,1]
      const x = u * ca - v * sa, y = u * sa + v * ca, zz = z * 1.1 - 0.35;
      return [cx + sc * x, cy - sc * (zz * ce - y * se) * 1.0, y * ce + zz * se];
    };
    // Painter's order for a height field: draw grid cells from far to near
    // by their floor position only. Why: sorting by the mean depth of each
    // quad fails for tall, steep peaks, because the peak top pulls the depth
    // toward the viewer. The floor order is correct for any azimuth while
    // the view is from above (el > 0).
    const U = (cI) => -1 + 2 * cI / (cols - 1), V = (rI) => -1 + 2 * rI / (rows - 1);
    const zt = o.thr != null ? nz(o.thr) : null;
    const cells = [];
    for (let r = 0; r < rows - 1; r++) for (let k = 0; k < cols - 1; k++) {
      const uc = (U(k) + U(k + 1)) / 2, vc = (V(r) + V(r + 1)) / 2;
      cells.push([r, k, uc * sa + vc * ca]); // rotated floor y: larger is nearer
    }
    cells.sort((a, b) => a[2] - b[2]);
    // floor box
    c.strokeStyle = C.line; c.lineWidth = 1;
    const box = [P(-1, -1, 0), P(1, -1, 0), P(1, 1, 0), P(-1, 1, 0)];
    c.beginPath(); box.forEach((q, i) => (i ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1]))); c.closePath(); c.stroke();
    const edge = G.alpha(C.bg.startsWith("#") ? C.bg : "#000000", 0.25), planeFill = G.alpha(C.warn, 0.16);
    const poly = (pts, fill, stroke) => {
      c.beginPath(); pts.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1]))); c.closePath();
      c.fillStyle = fill; c.fill(); if (stroke) { c.strokeStyle = stroke; c.lineWidth = 0.4; c.stroke(); }
    };
    for (const [r, k] of cells) {
      const zs = [Z[r][k], Z[r][k + 1], Z[r + 1][k + 1], Z[r + 1][k]].map(nz);
      const zm = (zs[0] + zs[1] + zs[2] + zs[3]) / 4;
      const surf = () => poly([P(U(k), V(r), zs[0]), P(U(k + 1), V(r), zs[1]), P(U(k + 1), V(r + 1), zs[2]), P(U(k), V(r + 1), zs[3])], G.cmapStr(zm), edge);
      const plane = () => poly([P(U(k), V(r), zt), P(U(k + 1), V(r), zt), P(U(k + 1), V(r + 1), zt), P(U(k), V(r + 1), zt)], planeFill, null);
      // In one cell: the lower of (surface, threshold plane) goes first.
      if (zt == null) surf(); else if (zm > zt) { plane(); surf(); } else { surf(); plane(); }
    }
    // Range labels at the ends of the floor axes, so the 3D view reads in
    // real units (o.xr / o.yr are [first, last] values of the columns / rows).
    // Write a label centred on a projected point, but keep it inside the
    // canvas. Why: at some azimuths the axis ends project past the edge.
    const lab = (txt, q, dy = 4) => {
      const hw = c.measureText(txt).width / 2 + 3, x = Math.min(w - hw, Math.max(hw, q[0])), y = Math.min(h - 6, Math.max(12, q[1] + dy));
      c.fillText(txt, x, y);
    };
    c.fillStyle = C.ink3; c.font = `11px ${C.mono}`; c.textAlign = "center";
    const fmtR = o.rfmt || ((v) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)));
    // Labels sit at 10 % and 90 % of each axis, away from the shared corner.
    const at = (r, f) => r[0] + f * (r[1] - r[0]);
    if (o.xr) [[-0.8, at(o.xr, 0.1)], [0.8, at(o.xr, 0.9)]].forEach(([u, val]) => { const q = P(u, 1.12, 0); lab(fmtR(val), q); });
    if (o.yr) [[-0.8, at(o.yr, 0.1)], [0.8, at(o.yr, 0.9)]].forEach(([v, val]) => { const q = P(1.14, v, 0); lab(fmtR(val), q); });
    // axis labels
    c.fillStyle = C.ink2; c.font = `12px ${C.mono}`; c.textAlign = "center";
    const lx = P(0, 1.3, 0), ly = P(1.38, 0, 0), lz = P(-1, -1, 1.05);
    if (o.xlabel) lab(o.xlabel, lx, 0);
    if (o.ylabel) lab(o.ylabel, ly, 0);
    if (o.zlabel) lab(o.zlabel, lz, -6);
    // drag to rotate
    if (!canvas._drag) {
      canvas._drag = true; let down = null;
      canvas.addEventListener("pointerdown", (e) => { down = [e.clientX, e.clientY, st.az, st.el]; canvas.setPointerCapture(e.pointerId); });
      canvas.addEventListener("pointermove", (e) => {
        if (!down) return; st.az = down[2] + (e.clientX - down[0]) * 0.01;
        st.el = Math.min(1.45, Math.max(0.05, down[3] + (e.clientY - down[1]) * 0.01)); o.redraw && o.redraw();
      });
      canvas.addEventListener("pointerup", () => (down = null));
      canvas.style.cursor = "grab";
    }
  };

  /* ---------------- controls ---------------- */
  /** Build slider rows in a .controls element.
   *  spec: {id, label, min, max, step, value, fmt(v) -> string} or {id, label, type:'select', options:[[v,text]]}
   *  or {id, label, type:'check', value:bool}. Returns object of current values; calls onChange(vals). */
  G.controls = (el, specs, onChange) => {
    const vals = {};
    for (const s of specs) {
      const row = document.createElement("div");
      if (s.type === "check") {
        row.className = "ctl check";
        row.innerHTML = `<label><input type="checkbox" id="${s.id}"${s.value ? " checked" : ""}> ${s.label}</label>`;
        el.appendChild(row);
        const inp = row.querySelector("input"); vals[s.key || s.id] = inp.checked;
        inp.addEventListener("change", () => { vals[s.key || s.id] = inp.checked; onChange(vals, s.key || s.id); });
        continue;
      }
      row.className = "ctl";
      if (s.type === "select") {
        row.innerHTML = `<span>${s.label}</span><select id="${s.id}">${s.options.map(([v, t]) => `<option value="${v}"${v == s.value ? " selected" : ""}>${t}</option>`).join("")}</select>`;
        el.appendChild(row);
        const inp = row.querySelector("select"); vals[s.key || s.id] = inp.value;
        inp.addEventListener("change", () => { vals[s.key || s.id] = inp.value; onChange(vals, s.key || s.id); });
        continue;
      }
      row.innerHTML = `<span>${s.label}</span><input type="range" id="${s.id}" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.value}"><output></output>`;
      el.appendChild(row);
      const inp = row.querySelector("input"), out = row.querySelector("output");
      const set = () => { const v = parseFloat(inp.value); vals[s.key || s.id] = v; out.textContent = s.fmt ? s.fmt(v) : v; };
      set();
      inp.addEventListener("input", () => { set(); onChange(vals, s.key || s.id); });
    }
    return vals;
  };
  /** Write label/value readouts into a <dl class="readouts">. items: [[label, value, cls]] */
  G.readouts = (dl, items) => {
    dl.innerHTML = items.map(([k, v, cls]) => `<div class="ro"><dt>${k}</dt><dd class="${cls || ""}">${v}</dd></div>`).join("");
  };
  /** Pill step selector: buttons inside el; show the matching [data-step] body. */
  G.stepper = (root, onStep) => {
    const btns = [...root.querySelectorAll(".steps button")];
    const bodies = [...root.querySelectorAll("[data-step]")];
    const go = (i) => {
      btns.forEach((b, k) => b.classList.toggle("on", k === i));
      bodies.forEach((b) => (b.hidden = +b.dataset.step !== i));
      onStep && onStep(i);
    };
    btns.forEach((b, i) => b.addEventListener("click", () => go(i)));
    go(0); return go;
  };

  /* ---------------- maths helpers ---------------- */
  G.rng = (seed = 1) => { // mulberry32
    let a = seed >>> 0;
    const u = () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
    let spare = null;
    u.gauss = () => { if (spare != null) { const s = spare; spare = null; return s; }
      let x, y, r; do { x = 2 * u() - 1; y = 2 * u() - 1; r = x * x + y * y; } while (!r || r >= 1);
      const m = Math.sqrt(-2 * Math.log(r) / r); spare = y * m; return x * m; };
    return u;
  };
  G.sinc = (x) => (Math.abs(x) < 1e-9 ? 1 : Math.sin(Math.PI * x) / (Math.PI * x)); // normalised sinc
  G.tri = (x) => Math.max(0, 1 - Math.abs(x));
  G.db = (x) => 10 * Math.log10(Math.max(x, 1e-30));
  G.lin = (d) => Math.pow(10, d / 10);
  G.wrapPi = (a) => { a = (a + Math.PI) % (2 * Math.PI); if (a < 0) a += 2 * Math.PI; return a - Math.PI; };
  G.fmt = (v, d = 2) => (isFinite(v) ? v.toFixed(d) : "-");
  G.si = (m) => { const a = Math.abs(m);
    if (a >= 1e6) return (m / 1e3).toFixed(0) + " km"; if (a >= 1e4) return (m / 1e3).toFixed(1) + " km";
    if (a >= 1e3) return (m / 1e3).toFixed(2) + " km"; if (a >= 1) return m.toFixed(a >= 100 ? 0 : 2) + " m";
    if (a >= 1e-2) return (m * 100).toFixed(1) + " cm"; return (m * 1000).toFixed(1) + " mm"; };

  /** Regularised upper incomplete gamma Q(k, x) for integer k (sum of k exponentials). */
  G.gammaQ = (k, x) => { let term = 1, sum = 1; for (let i = 1; i < k; i++) { term *= x / i; sum += term; } return Math.exp(-x) * sum; };
  /** Threshold x with Q(k, x) = p (noise power normalised to 1 per coherent sum). */
  G.gammaQinv = (k, p) => { let lo = 0, hi = 50 + 10 * k; for (let i = 0; i < 80; i++) { const m = (lo + hi) / 2; if (G.gammaQ(k, m) > p) lo = m; else hi = m; } return (lo + hi) / 2; };

  /* ---------------- GPS L1 C/A code ---------------- */
  // G2 phase-select taps (IS-GPS-200, Table 3-Ia) for PRN 1..32.
  const G2T = [[2,6],[3,7],[4,8],[5,9],[1,9],[2,10],[1,8],[2,9],[3,10],[2,3],[3,4],[5,6],[6,7],[7,8],[8,9],[9,10],
               [1,4],[2,5],[3,6],[4,7],[5,8],[6,9],[1,3],[4,6],[5,7],[6,8],[7,9],[8,10],[1,6],[2,7],[3,8],[4,9]];
  const caCache = {};
  /** Return the 1023-chip C/A code of a PRN as +1/-1 values (chip 0 = logic 1 -> -1). */
  G.caCode = (prn) => {
    if (caCache[prn]) return caCache[prn];
    const g1 = new Array(10).fill(1), g2 = new Array(10).fill(1), [s1, s2] = G2T[prn - 1], out = new Int8Array(1023);
    for (let i = 0; i < 1023; i++) {
      const bit = g1[9] ^ (g2[s1 - 1] ^ g2[s2 - 1]);
      out[i] = bit ? -1 : 1;
      const f1 = g1[2] ^ g1[9], f2 = g2[1] ^ g2[2] ^ g2[5] ^ g2[7] ^ g2[8] ^ g2[9];
      g1.pop(); g1.unshift(f1); g2.pop(); g2.unshift(f2);
    }
    return (caCache[prn] = out);
  };
  /** First 10 chips of a C/A code as an octal string (check value from the ICD). */
  G.caOctal = (prn) => { const c = G.caCode(prn); let n = 0; for (let i = 0; i < 10; i++) n = n * 2 + (c[i] < 0 ? 1 : 0); return n.toString(8); };
  /** Circular cross-correlation of two +/-1 arrays, normalised by length. */
  G.xcorr = (a, b) => { const N = a.length, out = new Float64Array(N);
    for (let k = 0; k < N; k++) { let s = 0; for (let i = 0; i < N; i++) s += a[i] * b[(i + k) % N]; out[k] = s / N; } return out; };

  /* ---------------- sections and boot ---------------- */
  const inits = [];
  G.section = (id, fn) => inits.push([id, fn]);
  G.data = null;

  G.boot = () => {
    G.readTheme();
    try { G.data = JSON.parse(document.getElementById("heron-data").textContent); } catch (e) { G.data = null; console.error("no HERON data", e); }
    for (const [id, fn] of inits) { try { fn(document.getElementById(id)); } catch (e) { console.error("section " + id, e); } }
    if (window.renderMathInElement) {
      renderMathInElement(document.body, { delimiters: [{ left: "$$", right: "$$", display: true }, { left: "\\(", right: "\\)", display: false }], throwOnError: false });
    }
    const mq = matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener && mq.addEventListener("change", G.redrawAll);
    new MutationObserver(G.redrawAll).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    G.nav();
  };

  /* ---------------- navigation and present mode ---------------- */
  G.nav = () => {
    const parts = [...document.querySelectorAll(".part")];
    const links = [...document.querySelectorAll(".toc a")];
    const io = new IntersectionObserver((ents) => {
      for (const e of ents) if (e.isIntersecting) {
        links.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + e.target.id));
        G.curPart = e.target;
      }
    }, { rootMargin: "-30% 0px -60% 0px" });
    parts.forEach((p) => io.observe(p));

    // Theme toggle cycles system -> light -> dark.
    const tbtn = document.getElementById("theme-btn");
    const modes = [null, "light", "dark"]; let mi = 0;
    tbtn && tbtn.addEventListener("click", () => {
      mi = (mi + 1) % 3; const m = modes[mi];
      if (m) document.documentElement.setAttribute("data-theme", m); else document.documentElement.removeAttribute("data-theme");
      tbtn.textContent = "Theme: " + (m || "system");
    });

    // Present mode: larger type, step through stops with arrow keys, timer.
    const pbtn = document.getElementById("present-btn");
    const stops = () => [...document.querySelectorAll(".hero, .part-h, .panel")];
    const timer = document.querySelector(".timer");
    let t0 = null, tick = null;
    const setPresent = (on) => {
      document.body.classList.toggle("present", on);
      pbtn && pbtn.classList.toggle("on", on);
      if (on && !t0) { t0 = Date.now(); tick = setInterval(updTimer, 1000); }
      setTimeout(G.redrawAll, 50);
    };
    const updTimer = () => {
      if (!timer || !t0) return;
      const el = (Date.now() - t0) / 1000;
      // Planned end time of the current part = sum of the minutes so far.
      let plan = 0; for (const p of parts) { plan += +(p.dataset.min || 0); if (p === G.curPart) break; }
      timer.querySelector(".clk").textContent = `${Math.floor(el / 60)}:${String(Math.floor(el % 60)).padStart(2, "0")} / ${plan} min`;
      const f = Math.min(1, el / 60 / Math.max(plan, 1));
      timer.querySelector(".bar i").style.width = (100 * f) + "%";
      timer.querySelector(".bar").classList.toggle("over", el / 60 > plan);
    };
    pbtn && pbtn.addEventListener("click", () => setPresent(!document.body.classList.contains("present")));
    document.getElementById("timer-reset")?.addEventListener("click", () => { t0 = Date.now(); updTimer(); });
    document.addEventListener("keydown", (e) => {
      if (e.target.closest("input,select,textarea")) return;
      if (e.key === "p" || e.key === "P") { setPresent(!document.body.classList.contains("present")); return; }
      if (!document.body.classList.contains("present")) return;
      if (["ArrowRight", "ArrowDown", "PageDown", " "].includes(e.key) || ["ArrowLeft", "ArrowUp", "PageUp"].includes(e.key)) {
        e.preventDefault();
        const dir = ["ArrowLeft", "ArrowUp", "PageUp"].includes(e.key) ? -1 : 1;
        const list = stops(); const y = window.scrollY + 20;
        let i = list.findIndex((s) => s.getBoundingClientRect().top + window.scrollY > y);
        if (i < 0) i = list.length;
        const target = dir > 0 ? list[i] : list[Math.max(0, i - 2)];
        target && target.scrollIntoView({ block: "start" });
      }
      if (e.key === "Escape") setPresent(false);
    });
  };

  return G;
})();
window.addEventListener("DOMContentLoaded", () => G.boot());
