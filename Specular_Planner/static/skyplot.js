// Polar sky plot for the takeoff spot and time, from the planner's own orbit data (/api/skyplot).
// North up, east right (as seen looking up from below, like Trimble's). Mission satellites are
// drawn bold; the rest faint. A slider moves through the window; Save PNG downloads the picture.
window.HeronSkyplot = (() => {
  const $ = (id) => document.getElementById(id);
  const COLORS = { G: "#3f8f3a", E: "#2f6fb3", C: "#c79a00", R: "#888", J: "#888" };
  const NAMES = { G: "GPS", E: "Galileo", C: "BeiDou" };
  let data = null, minute = 0, picked = null, hoverSid = null;
  const panel = $("skyPanel"), canvas = $("skyCanvas"), slider = $("skyTime");

  function chosenSats() {
    if (typeof missionMode === "undefined" || missionMode !== "science" || !window.HeronScience) return new Set();
    return new Set(window.HeronScience.stages()
      .flatMap((p) => String(Array.isArray(p.satellites) ? p.satellites.join(",") : p.satellites || "").split(/[\s,]+/))
      .map((x) => x.trim().toUpperCase()).filter(Boolean)
      .map((x) => x.replace(/^([A-Z])(\d)$/, "$10$2")));
  }

  function startIso() {
    const raw = $("start").value;
    return raw || new Date().toISOString().slice(0, 16);
  }

  function fmtTime(min) {
    const t = new Date(new Date(`${startIso()}Z`).getTime() + min * 60000);
    const utc = t.toISOString().slice(11, 16);
    const local = new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit", hour12: false, timeZoneName: "short" }).format(t);
    return `${utc} UTC · ${local}`;
  }

  async function load() {
    const where = padMarker ? padMarker.getLatLng() : map.getCenter();
    const qs = new URLSearchParams({
      start: startIso(), lat: where.lat.toFixed(6), lon: where.lng.toFixed(6),
      mask: $("mask").value || "20", h_agl: $("h_agl").value || "120",
      constellations: (typeof selectedConsts === "function" ? selectedConsts() : ["G", "E", "C"]).join(",") || "G,E,C",
      hours: $("skyHours").value, step_min: "1",
    });
    $("skyInfo").textContent = "Loading…";
    const r = await fetch(`/api/skyplot?${qs}`);
    if (!r.ok) throw new Error(`Sky plot failed (${r.status})`);
    data = await r.json();
    data.where = where;
    slider.max = String(data.hours * 60);
    minute = Math.min(minute, data.hours * 60);
    slider.value = String(minute);
    draw();
  }

  function at(sat, min) {
    const pts = sat.points;
    let best = pts[0];
    for (const p of pts) if (Math.abs(p[0] - min) < Math.abs(best[0] - min)) best = p;
    return best;
  }

  function geometry() {
    const size = canvas.width, cx = size / 2, cy = size / 2 + 14, r = size / 2 - 40;
    const xy = (az, el) => {
      const d = r * (90 - Math.max(el, 0)) / 90, a = az * Math.PI / 180;
      return [cx + d * Math.sin(a), cy - d * Math.cos(a)];
    };
    return { size, cx, cy, r, xy };
  }

  function draw() {
    const ctx = canvas.getContext("2d");
    const { size, cx, cy, r, xy } = geometry();
    const chosen = chosenSats();
    const mask = data ? data.mask : 20;
    ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    // Rings every 15 deg, mask ring, cardinal lines.
    ctx.lineWidth = 1;
    for (let el = 0; el < 90; el += 15) {
      ctx.strokeStyle = el === 0 ? "#9aa8a6" : "#e1e7e4";
      ctx.beginPath(); ctx.arc(cx, cy, r * (90 - el) / 90, 0, 2 * Math.PI); ctx.stroke();
      if (el) { ctx.fillStyle = "#8d9a99"; ctx.font = "11px sans-serif"; ctx.fillText(`${el}°`, cx + 3, cy - r * (90 - el) / 90 - 2); }
    }
    ctx.strokeStyle = "#e1e7e4";
    for (let az = 0; az < 360; az += 30) {
      const [x, y] = xy(az, 0); ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y); ctx.stroke();
    }
    ctx.fillStyle = "#14282a"; ctx.font = "bold 13px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    [["N", 0], ["E", 90], ["S", 180], ["W", 270]].forEach(([t, az]) => { const [x, y] = xy(az, -9); ctx.fillText(t, x, y); });
    ctx.strokeStyle = "#d8663a"; ctx.setLineDash([6, 4]); ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(cx, cy, r * (90 - mask) / 90, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
    if (!data) return;
    // Faint satellites first, mission satellites on top.
    const order = [...data.sats].sort((a, b) => (chosen.has(a.sid) ? 1 : 0) - (chosen.has(b.sid) ? 1 : 0));
    const anyChosen = order.some((s) => chosen.has(s.sid));
    const marks = [];
    for (const sat of order) {
      const bold = chosen.has(sat.sid) || sat.sid === picked;
      const faint = anyChosen && !bold;
      const color = COLORS[sat.sid[0]] || "#888";
      ctx.globalAlpha = faint ? 0.28 : 1;
      ctx.strokeStyle = color; ctx.lineWidth = bold ? 2.5 : 1.2;
      ctx.beginPath(); let pen = false;
      for (const [, az, el] of sat.points) {
        if (el < 0) { pen = false; continue; }
        const [x, y] = xy(az, el);
        if (pen) ctx.lineTo(x, y); else { ctx.moveTo(x, y); pen = true; }
      }
      ctx.stroke();
      const [, az, el] = at(sat, minute);
      if (el >= 0) {
        const [x, y] = xy(az, el);
        ctx.fillStyle = el >= mask ? color : "#ffffff"; ctx.strokeStyle = color; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, bold ? 7 : 5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
        marks.push({ sat, x, y, az, el, bold, faint });
      }
      ctx.globalAlpha = 1;
    }
    // Labels: mission satellites first, each in the first nearby spot that does not cover another.
    const placed = [];
    const clash = (a) => placed.some((b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h);
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    for (const m of [...marks].sort((a, b) => (b.bold ? 1 : 0) - (a.bold ? 1 : 0))) {
      ctx.font = `${m.bold ? "bold 12px" : "10px"} sans-serif`;
      const w = ctx.measureText(m.sat.sid).width + 2, h = m.bold ? 14 : 12;
      const spots = [[9, -9], [9, 9], [-9 - w, -9], [-9 - w, 9], [-w / 2, -18], [-w / 2, 18], [14, 0], [-14 - w, 0]];
      let box = null;
      for (const [dx, dy] of spots) {
        const b = { x: m.x + dx, y: m.y + dy - h / 2, w, h };
        if (!clash(b)) { box = b; break; }
      }
      if (!box) { if (!m.bold) continue; box = { x: m.x + 9, y: m.y - 9 - h / 2, w, h }; }
      placed.push(box);
      ctx.globalAlpha = m.faint ? 0.45 : 1;
      if (m.bold) { ctx.fillStyle = "rgba(255,255,255,0.85)"; ctx.fillRect(box.x - 1, box.y, box.w + 1, box.h); }
      ctx.fillStyle = "#14282a"; ctx.fillText(m.sat.sid, box.x, box.y + h / 2);
      ctx.globalAlpha = 1;
    }
    ctx.textBaseline = "alphabetic";
    canvas._marks = marks;
    // Caption (part of the PNG).
    ctx.textAlign = "left"; ctx.fillStyle = "#14282a"; ctx.font = "bold 12px sans-serif";
    ctx.fillText(`${fmtTime(minute)}`, 10, 16);
    ctx.font = "11px sans-serif"; ctx.fillStyle = "#68797a";
    ctx.fillText(`${data.where.lat.toFixed(5)}, ${data.where.lng.toFixed(5)} · mask ${mask}°`, 10, 32);
    ctx.textAlign = "right";
    let ly = 16;
    for (const c of ["G", "E", "C"]) {
      if (!data.sats.some((s) => s.sid[0] === c)) continue;
      ctx.fillStyle = COLORS[c]; ctx.fillRect(size - 74, ly - 6, 10, 10);
      ctx.fillStyle = "#14282a"; ctx.textAlign = "left"; ctx.fillText(NAMES[c], size - 60, ly); ly += 16;
    }
    $("skyClock").textContent = fmtTime(minute);
    info();
  }

  function info() {
    const sid = picked || hoverSid;
    const box = $("skyInfo");
    if (!data) return;
    const sat = sid && data.sats.find((s) => s.sid === sid);
    if (!sat) {
      const up = data.sats.filter((s) => at(s, minute)[2] >= data.mask).length;
      const chosen = [...chosenSats()];
      box.textContent = `${up} satellites above the ${data.mask}° mask.` +
        (chosen.length ? ` Mission satellites in bold: ${chosen.join(", ")}.` : "") + " Click a satellite for details.";
      return;
    }
    const [, az, el] = at(sat, minute);
    const span = sat.above_from_min == null ? "never above the mask in this window"
      : `above the mask ${fmtTime(sat.above_from_min).split(" · ")[0]}–${fmtTime(sat.above_to_min).split(" · ")[0]}` +
        (sat.above_to_min >= data.hours * 60 ? " (and later)" : "");
    box.textContent = `${sat.sid}: azimuth ${az.toFixed(0)}°, elevation ${el.toFixed(0)}°; ${span}.`;
  }

  function nearest(ev) {
    const rect = canvas.getBoundingClientRect();
    const x = (ev.clientX - rect.left) * canvas.width / rect.width;
    const y = (ev.clientY - rect.top) * canvas.height / rect.height;
    let best = null, dist = 14;
    for (const m of canvas._marks || []) {
      const d = Math.hypot(m.x - x, m.y - y);
      if (d < dist) { dist = d; best = m.sat.sid; }
    }
    return best;
  }

  canvas.addEventListener("mousemove", (ev) => {
    const sid = nearest(ev);
    canvas.style.cursor = sid ? "pointer" : "default";
    if (sid !== hoverSid) { hoverSid = sid; info(); }
  });
  canvas.addEventListener("click", (ev) => { picked = nearest(ev); draw(); });
  slider.addEventListener("input", () => { minute = Number(slider.value); draw(); });
  $("skyHours").addEventListener("change", () => load().catch(fail));
  $("skyClose").onclick = () => panel.classList.add("hidden");
  $("skySave").onclick = () => {
    const a = document.createElement("a");
    const stamp = new Date(new Date(`${startIso()}Z`).getTime() + minute * 60000).toISOString().slice(0, 16).replace(/[-:]/g, "");
    a.download = `HERON_skyplot_${stamp}Z.png`;
    a.href = canvas.toDataURL("image/png");
    a.click();
  };
  function fail(err) { $("skyInfo").textContent = String(err.message || err); }

  $("btnSkyplot").onclick = () => {
    panel.classList.remove("hidden");
    minute = 0; picked = null;
    load().catch(fail);
  };
  ["start", "mask", "cG", "cE", "cC"].forEach((id) => $(id)?.addEventListener("change", () => {
    if (!panel.classList.contains("hidden")) load().catch(fail);
  }));
  draw();
  return { refresh: () => load().catch(fail) };
})();
