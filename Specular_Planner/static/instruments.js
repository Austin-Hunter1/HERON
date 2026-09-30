/* Glass-cockpit instruments on canvas: primary flight display (PFD) and compass (HSI). */
(() => {
  "use strict";
  const RAD = Math.PI / 180;
  const MONO = '"Cascadia Mono", "SFMono-Regular", Consolas, monospace';
  const SANS = '"Segoe UI", system-ui, sans-serif';
  const C = {
    skyTop: "#0a3766", sky: "#2c7cc2", ground: "#6b4826", groundLow: "#35220f",
    white: "#f3f6f9", dim: "rgba(243, 246, 249, 0.72)", tape: "rgba(6, 11, 18, 0.58)", box: "#04070b",
    cyan: "#4fd6ff", magenta: "#ff5ad1", green: "#3ddc97", amber: "#ffb13b", red: "#ff5b5b", yellow: "#ffd84a",
  };
  const CARDINAL = { 0: "N", 90: "E", 180: "S", 270: "W" };

  const wrap180 = (a) => ((a % 360) + 540) % 360 - 180;
  const finite = (v) => typeof v === "number" && Number.isFinite(v);

  function rounded(c, x, y, w, h, r) {
    c.beginPath();
    if (c.roundRect) c.roundRect(x, y, w, h, r);
    else c.rect(x, y, w, h);
  }

  class Gauge {
    constructor(canvas, target) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.target = target;
      this.value = {};
      this.w = 0;
      this.h = 0;
      this.dpr = 1;
      new ResizeObserver(() => this.resize()).observe(canvas);
      this.resize();
      const loop = (now) => {
        this.frame(now);
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    }

    resize() {
      const r = this.canvas.getBoundingClientRect();
      this.dpr = window.devicePixelRatio || 1;
      this.w = r.width;
      this.h = r.height;
      this.canvas.width = Math.max(1, Math.round(r.width * this.dpr));
      this.canvas.height = Math.max(1, Math.round(r.height * this.dpr));
    }

    set(values) {
      Object.assign(this.target, values);
    }

    ease(key, k, angular = false) {
      const t = this.target[key];
      const v = this.value[key];
      if (!finite(t) || !finite(v)) {
        this.value[key] = t;
        return;
      }
      this.value[key] = angular ? (v + wrap180(t - v) * k + 360) % 360 : v + (t - v) * k;
    }

    frame(now) {
      const dt = Math.min(0.1, (now - (this.last || now)) / 1000);
      this.last = now;
      if (!this.w || !this.h || this.canvas.offsetParent === null) return;
      this.smooth(1 - Math.exp(-dt * 9));
      const c = this.ctx;
      c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      c.clearRect(0, 0, this.w, this.h);
      this.draw(c, this.w, this.h);
    }
  }

  class PFD extends Gauge {
    constructor(canvas) {
      super(canvas, {
        roll: 0, pitch: 0, heading: 0, speed: 0, alt: 0, vs: 0, targetAlt: null,
        mode: "", armed: false, att: false, pos: false, link: false, sim: false,
      });
    }

    smooth(k) {
      ["roll", "pitch", "speed", "alt", "vs"].forEach((key) => this.ease(key, k));
      this.ease("heading", k, true);
    }

    draw(c, W, H) {
      const t = this.target;
      const v = this.value;
      const hdgH = 36;
      const tapeW = Math.max(52, Math.min(66, W * 0.15));
      const vsiW = 16;
      const leftX = 10;
      const rightX = W - vsiW - 10 - tapeW;
      const cx = (leftX + tapeW + rightX) / 2;
      const cy = (H - hdgH) / 2 + 8;
      const ppd = (H - hdgH) / 48;
      const roll = t.att && finite(v.roll) ? v.roll : 0;
      const pitch = t.att && finite(v.pitch) ? v.pitch : 0;
      const innerW = rightX - (leftX + tapeW);
      const ladderW = innerW * 0.74;
      const ladderH = (H - hdgH) * 0.6;

      c.save();
      c.beginPath();
      c.rect(0, 0, W, H - hdgH);
      c.clip();
      c.translate(cx, cy);
      c.rotate(-roll * RAD);
      c.translate(0, pitch * ppd);
      const R = Math.hypot(W, H) * 1.3;
      let g = c.createLinearGradient(0, -H * 0.8, 0, 0);
      g.addColorStop(0, C.skyTop);
      g.addColorStop(1, C.sky);
      c.fillStyle = g;
      c.fillRect(-R, -R, 2 * R, R);
      g = c.createLinearGradient(0, 0, 0, H * 0.8);
      g.addColorStop(0, C.ground);
      g.addColorStop(1, C.groundLow);
      c.fillStyle = g;
      c.fillRect(-R, 0, 2 * R, R);
      c.strokeStyle = C.white;
      c.lineWidth = 1.6;
      c.beginPath();
      c.moveTo(-R, 0);
      c.lineTo(R, 0);
      c.stroke();
      c.restore();

      c.save();
      c.beginPath();
      c.rect(cx - ladderW / 2, cy - ladderH / 2, ladderW, ladderH);
      c.clip();
      c.translate(cx, cy);
      c.rotate(-roll * RAD);
      c.strokeStyle = C.white;
      c.fillStyle = C.white;
      c.lineWidth = 1.4;
      c.font = `600 10px ${MONO}`;
      c.textBaseline = "middle";
      for (let p = -90; p <= 90; p += 2.5) {
        if (p === 0) continue;
        const y = (pitch - p) * ppd;
        if (Math.abs(y) > ladderH) continue;
        const major = p % 10 === 0;
        const half = major ? 32 : p % 5 === 0 ? 17 : 7;
        c.beginPath();
        c.moveTo(-half, y);
        c.lineTo(half, y);
        c.stroke();
        if (major) {
          c.textAlign = "right";
          c.fillText(String(Math.abs(p)), -half - 6, y);
          c.textAlign = "left";
          c.fillText(String(Math.abs(p)), half + 6, y);
        }
      }
      c.restore();

      const rr = Math.min(ladderH / 2 + 16, cy - 14);
      c.save();
      c.translate(cx, cy);
      c.strokeStyle = C.white;
      c.fillStyle = C.white;
      c.lineWidth = 1.6;
      c.beginPath();
      c.arc(0, 0, rr, -150 * RAD, -30 * RAD);
      c.stroke();
      for (const a of [-60, -45, -30, -20, -10, 10, 20, 30, 45, 60]) {
        const len = Math.abs(a) === 30 || Math.abs(a) === 60 ? 11 : 6;
        c.save();
        c.rotate(a * RAD);
        c.beginPath();
        c.moveTo(0, -rr);
        c.lineTo(0, -rr - len);
        c.stroke();
        c.restore();
      }
      c.beginPath();
      c.moveTo(0, -rr - 1);
      c.lineTo(-6, -rr - 11);
      c.lineTo(6, -rr - 11);
      c.closePath();
      c.fill();
      c.rotate(-roll * RAD);
      c.fillStyle = C.yellow;
      c.beginPath();
      c.moveTo(0, -rr + 2);
      c.lineTo(-7, -rr + 13);
      c.lineTo(7, -rr + 13);
      c.closePath();
      c.fill();
      c.restore();

      const wingOut = Math.min(74, innerW * 0.34);
      const wingIn = wingOut * 0.42;
      c.save();
      c.translate(cx, cy);
      c.lineJoin = "round";
      c.lineCap = "round";
      for (const [color, width] of [["#000", 7], [C.yellow, 3.5]]) {
        c.strokeStyle = color;
        c.lineWidth = width;
        for (const side of [-1, 1]) {
          c.beginPath();
          c.moveTo(side * wingOut, 0);
          c.lineTo(side * wingIn, 0);
          c.lineTo(side * wingIn, 9);
          c.stroke();
        }
        c.strokeRect(-3, -3, 6, 6);
      }
      c.restore();

      const tapeTop = 44;
      const tapeH = H - hdgH - tapeTop - 12;
      this.tape(c, { x: leftX, y: tapeTop, w: tapeW, h: tapeH, value: v.speed, range: 20, minor: 1, major: 5,
        side: "left", valid: t.pos, title: "GS m/s", decimals: 1 });
      this.tape(c, { x: rightX, y: tapeTop, w: tapeW, h: tapeH, value: v.alt, range: 60, minor: 2, major: 10,
        side: "right", valid: t.pos, title: "ALT m", decimals: 1, bug: t.targetAlt });
      this.vsi(c, W - vsiW - 5, tapeTop, vsiW, tapeH, t.pos ? v.vs : null);
      this.headingTape(c, W, H, hdgH, t.pos || t.att ? v.heading : null);

      c.font = `700 10.5px ${MONO}`;
      c.textBaseline = "middle";
      const modeText = `${t.sim ? "SIM · " : ""}${t.mode || "—"}`;
      const mw = c.measureText(modeText).width + 18;
      c.fillStyle = "rgba(4, 7, 11, 0.78)";
      rounded(c, leftX, 9, mw, 20, 5);
      c.fill();
      c.fillStyle = t.sim ? C.amber : C.cyan;
      c.textAlign = "left";
      c.fillText(modeText, leftX + 9, 19.5);

      const armText = t.armed ? "ARMED" : "DISARMED";
      const aw = c.measureText(armText).width + 18;
      c.fillStyle = t.armed ? "rgba(255, 91, 91, 0.92)" : "rgba(4, 7, 11, 0.78)";
      rounded(c, W - aw - 10, 9, aw, 20, 5);
      c.fill();
      c.fillStyle = t.armed ? "#fff" : C.dim;
      c.fillText(armText, W - aw - 1, 19.5);

      if (!t.link) {
        this.flag(c, 0, 0, W, H, "NO LINK", C.red);
      } else if (!t.att) {
        this.flag(c, cx - ladderW / 2, cy - ladderH / 2, ladderW, ladderH, "NO ATTITUDE DATA", C.amber);
      }
    }

    tape(c, o) {
      const { x, y, w, h, range, minor, major, side, valid, title, decimals, bug } = o;
      const cur = valid && finite(o.value) ? o.value : 0;
      const mid = y + h / 2;
      const ppu = h / range;
      c.save();
      c.fillStyle = C.tape;
      rounded(c, x, y, w, h, 6);
      c.fill();
      c.beginPath();
      c.rect(x, y, w, h);
      c.clip();
      c.strokeStyle = C.dim;
      c.fillStyle = C.white;
      c.lineWidth = 1;
      c.font = `500 11px ${MONO}`;
      c.textBaseline = "middle";
      const lo = Math.floor((cur - range / 2) / minor) * minor;
      for (let s = lo; s <= cur + range / 2 + minor; s += minor) {
        if (side === "left" && s < 0) continue;
        const yy = mid - (s - cur) * ppu;
        const isMajor = Math.abs(Math.round(s) % major) === 0 && Math.abs(s - Math.round(s)) < 1e-6;
        const len = isMajor ? 10 : 5;
        const tx = side === "left" ? x + w - len : x;
        c.beginPath();
        c.moveTo(tx, yy);
        c.lineTo(tx + len, yy);
        c.stroke();
        if (isMajor) {
          c.textAlign = side === "left" ? "right" : "left";
          c.fillText(String(Math.round(s)), side === "left" ? x + w - 14 : x + 14, yy);
        }
      }
      if (finite(bug)) {
        const by = Math.max(y + 6, Math.min(y + h - 6, mid - (bug - cur) * ppu));
        c.fillStyle = C.magenta;
        c.fillRect(side === "left" ? x + w - 5 : x, by - 7, 5, 14);
      }
      c.restore();

      const bh = 30;
      const bw = w + 4;
      const bx = x - 2;
      c.save();
      c.fillStyle = C.box;
      c.strokeStyle = C.white;
      c.lineWidth = 1.2;
      c.beginPath();
      if (side === "left") {
        c.moveTo(bx, mid - bh / 2);
        c.lineTo(bx + bw, mid - bh / 2);
        c.lineTo(bx + bw, mid - 6);
        c.lineTo(bx + bw + 7, mid);
        c.lineTo(bx + bw, mid + 6);
        c.lineTo(bx + bw, mid + bh / 2);
        c.lineTo(bx, mid + bh / 2);
      } else {
        c.moveTo(bx + bw, mid - bh / 2);
        c.lineTo(bx, mid - bh / 2);
        c.lineTo(bx, mid - 6);
        c.lineTo(bx - 7, mid);
        c.lineTo(bx, mid + 6);
        c.lineTo(bx, mid + bh / 2);
        c.lineTo(bx + bw, mid + bh / 2);
      }
      c.closePath();
      c.fill();
      c.stroke();
      c.fillStyle = valid ? C.white : C.amber;
      c.font = `700 16px ${MONO}`;
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.fillText(valid && finite(o.value) ? o.value.toFixed(decimals) : "---", bx + bw / 2, mid + 1);
      c.font = `650 9.5px ${SANS}`;
      c.fillStyle = C.dim;
      c.fillText(title, x + w / 2, y - 9);
      c.restore();
    }

    vsi(c, x, y, w, h, vs) {
      const mid = y + h / 2;
      const scale = h / 2 / 6;
      c.save();
      c.fillStyle = C.tape;
      rounded(c, x, y, w, h, 5);
      c.fill();
      c.strokeStyle = C.dim;
      c.lineWidth = 1;
      for (let s = -6; s <= 6; s += 1) {
        const yy = mid - s * scale;
        const len = s % 2 === 0 ? 6 : 3;
        c.beginPath();
        c.moveTo(x, yy);
        c.lineTo(x + len, yy);
        c.stroke();
      }
      if (finite(vs)) {
        const clamped = Math.max(-6, Math.min(6, vs));
        c.fillStyle = Math.abs(vs) > 5 ? C.amber : C.green;
        const top = Math.min(mid, mid - clamped * scale);
        c.fillRect(x + w / 2 - 2.5, top, 5, Math.max(1.5, Math.abs(clamped * scale)));
      }
      c.fillStyle = C.dim;
      c.font = `650 9.5px ${SANS}`;
      c.textAlign = "center";
      c.fillText("VS", x + w / 2, y - 9);
      c.font = `600 9.5px ${MONO}`;
      c.fillStyle = C.white;
      c.fillText(finite(vs) ? `${vs > 0.05 ? "+" : ""}${vs.toFixed(1)}` : "--", x + w / 2 - 12, y + h + 1);
      c.restore();
    }

    headingTape(c, W, H, hdgH, hdg) {
      const y = H - hdgH;
      c.save();
      c.fillStyle = "rgba(4, 8, 13, 0.94)";
      c.fillRect(0, y, W, hdgH);
      c.strokeStyle = "rgba(255, 255, 255, 0.14)";
      c.beginPath();
      c.moveTo(0, y + 0.5);
      c.lineTo(W, y + 0.5);
      c.stroke();
      const cur = finite(hdg) ? hdg : 0;
      const ppd = W / 110;
      c.strokeStyle = C.dim;
      c.lineWidth = 1;
      c.textAlign = "center";
      c.textBaseline = "middle";
      for (let d = Math.floor((cur - 60) / 5) * 5; d <= cur + 60; d += 5) {
        const x = W / 2 + (d - cur) * ppd;
        const n = ((d % 360) + 360) % 360;
        const major = n % 10 === 0;
        c.beginPath();
        c.moveTo(x, y);
        c.lineTo(x, y + (major ? 8 : 4));
        c.stroke();
        if (n % 30 === 0) {
          const label = CARDINAL[n] || String(n / 10);
          c.font = CARDINAL[n] ? `800 12px ${SANS}` : `600 11px ${MONO}`;
          c.fillStyle = CARDINAL[n] ? C.white : C.dim;
          c.fillText(label, x, y + 21);
        }
      }
      const bw = 54;
      c.fillStyle = C.box;
      c.strokeStyle = C.white;
      c.lineWidth = 1.2;
      c.beginPath();
      c.moveTo(W / 2 - bw / 2, y + 8);
      c.lineTo(W / 2 - 5, y + 8);
      c.lineTo(W / 2, y + 2);
      c.lineTo(W / 2 + 5, y + 8);
      c.lineTo(W / 2 + bw / 2, y + 8);
      c.lineTo(W / 2 + bw / 2, y + hdgH - 4);
      c.lineTo(W / 2 - bw / 2, y + hdgH - 4);
      c.closePath();
      c.fill();
      c.stroke();
      c.fillStyle = finite(hdg) ? C.white : C.amber;
      c.font = `700 14px ${MONO}`;
      c.fillText(finite(hdg) ? `${String(Math.round(cur) % 360).padStart(3, "0")}°` : "---", W / 2, y + hdgH / 2 + 3);
      c.restore();
    }

    flag(c, x, y, w, h, text, color) {
      c.save();
      c.fillStyle = "rgba(6, 10, 16, 0.62)";
      c.fillRect(x, y, w, h);
      c.font = `800 13px ${MONO}`;
      const tw = c.measureText(text).width + 24;
      c.strokeStyle = color;
      c.lineWidth = 1.5;
      c.fillStyle = "rgba(4, 7, 11, 0.9)";
      rounded(c, x + w / 2 - tw / 2, y + h / 2 - 15, tw, 30, 5);
      c.fill();
      c.stroke();
      c.fillStyle = color;
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.fillText(text, x + w / 2, y + h / 2 + 1);
      c.restore();
    }
  }

  class HSI extends Gauge {
    constructor(canvas) {
      super(canvas, { heading: 0, home: null, wp: null, course: null, valid: false });
    }

    smooth(k) {
      ["heading", "home", "wp", "course"].forEach((key) => this.ease(key, k, true));
    }

    draw(c, W, H) {
      const t = this.target;
      const v = this.value;
      const cx = W / 2;
      const cy = H / 2;
      const R = Math.min(W, H) / 2 - 10;
      const hdg = t.valid && finite(v.heading) ? v.heading : 0;

      c.save();
      c.fillStyle = "#060a0f";
      c.strokeStyle = "rgba(255, 255, 255, 0.12)";
      c.lineWidth = 1;
      c.beginPath();
      c.arc(cx, cy, R + 7, 0, Math.PI * 2);
      c.fill();
      c.stroke();

      c.translate(cx, cy);
      c.rotate(-hdg * RAD);
      c.textAlign = "center";
      c.textBaseline = "middle";
      for (let d = 0; d < 360; d += 5) {
        const major = d % 10 === 0;
        c.save();
        c.rotate(d * RAD);
        c.strokeStyle = major ? C.white : C.dim;
        c.lineWidth = major ? 1.5 : 1;
        c.beginPath();
        c.moveTo(0, -R);
        c.lineTo(0, -R + (major ? 9 : 5));
        c.stroke();
        if (d % 30 === 0) {
          c.font = CARDINAL[d] ? `800 13px ${SANS}` : `600 10px ${MONO}`;
          c.fillStyle = d === 0 ? C.amber : CARDINAL[d] ? C.white : C.dim;
          c.fillText(CARDINAL[d] || String(d / 10), 0, -R + 20);
        }
        c.restore();
      }
      c.restore();

      if (t.valid && finite(v.wp)) {
        c.save();
        c.translate(cx, cy);
        c.rotate((v.wp - hdg) * RAD);
        c.strokeStyle = C.magenta;
        c.fillStyle = C.magenta;
        c.lineWidth = 3;
        c.lineCap = "round";
        c.beginPath();
        c.moveTo(0, R - 30);
        c.lineTo(0, -R + 36);
        c.stroke();
        c.beginPath();
        c.moveTo(0, -R + 28);
        c.lineTo(-7, -R + 40);
        c.lineTo(7, -R + 40);
        c.closePath();
        c.fill();
        c.restore();
      }

      if (t.valid && finite(v.home) && finite(t.home)) {
        c.save();
        c.translate(cx, cy);
        c.rotate((v.home - hdg) * RAD);
        c.translate(0, -R + 2);
        c.fillStyle = C.green;
        c.beginPath();
        c.moveTo(0, -1);
        c.lineTo(-7, 10);
        c.lineTo(7, 10);
        c.closePath();
        c.fill();
        c.restore();
      }

      // GPS course over ground: where the aircraft is actually travelling (drift shows against heading).
      if (t.valid && finite(v.course) && finite(t.course)) {
        c.save();
        c.translate(cx, cy);
        c.rotate((v.course - hdg) * RAD);
        c.strokeStyle = C.cyan;
        c.fillStyle = C.cyan;
        c.lineWidth = 2;
        c.setLineDash([4, 4]);
        c.beginPath();
        c.moveTo(0, -14);
        c.lineTo(0, -R + 14);
        c.stroke();
        c.setLineDash([]);
        c.beginPath();
        c.moveTo(0, -R + 3);
        c.lineTo(-5, -R + 9);
        c.lineTo(0, -R + 15);
        c.lineTo(5, -R + 9);
        c.closePath();
        c.fill();
        c.restore();
      }

      c.save();
      c.translate(cx, cy);
      c.fillStyle = C.white;
      c.strokeStyle = "#000";
      c.lineWidth = 1.5;
      c.beginPath();
      c.moveTo(0, -12);
      c.lineTo(8, 10);
      c.lineTo(0, 5);
      c.lineTo(-8, 10);
      c.closePath();
      c.fill();
      c.stroke();
      c.restore();

      c.save();
      c.fillStyle = C.white;
      c.beginPath();
      c.moveTo(cx, cy - R + 1);
      c.lineTo(cx - 6, cy - R - 8);
      c.lineTo(cx + 6, cy - R - 8);
      c.closePath();
      c.fill();
      if (t.valid) {
        const text = `${String(Math.round(hdg) % 360).padStart(3, "0")}°`;
        c.font = `700 12px ${MONO}`;
        c.textAlign = "center";
        c.textBaseline = "middle";
        const tw = c.measureText(text).width + 10;
        c.fillStyle = "rgba(4, 7, 11, 0.9)";
        c.strokeStyle = "rgba(255, 255, 255, 0.35)";
        c.lineWidth = 1;
        rounded(c, cx - tw / 2, cy + 18, tw, 18, 4);
        c.fill();
        c.stroke();
        c.fillStyle = C.white;
        c.fillText(text, cx, cy + 27.5);
      }
      c.restore();

      if (!t.valid) {
        c.save();
        c.fillStyle = "rgba(6, 10, 16, 0.6)";
        c.beginPath();
        c.arc(cx, cy, R + 7, 0, Math.PI * 2);
        c.fill();
        c.fillStyle = C.amber;
        c.font = `800 11px ${MONO}`;
        c.textAlign = "center";
        c.textBaseline = "middle";
        c.fillText("NO HEADING", cx, cy);
        c.restore();
      }
    }
  }

  window.HeronInstruments = { PFD, HSI };
})();
