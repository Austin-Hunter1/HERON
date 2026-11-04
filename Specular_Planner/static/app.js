const map = L.map("map", {
  zoomControl: false,
  attributionControl: true,
  minZoom: 3,
  worldCopyJump: false,
  preferCanvas: true,
}).setView([40.0825, -105.228], 14);
L.control.zoom({ position: "bottomright" }).addTo(map);
L.DomEvent.disableClickPropagation(document.getElementById("sliderWrap"));
L.DomEvent.disableScrollPropagation(document.getElementById("sliderWrap"));
const baseTiles = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    maxZoom: 19,
    minZoom: 3,
    keepBuffer: 2,
    updateWhenZooming: false,
    attribution: "Esri",
  }
).addTo(map);

function oneWorldMinZoom() {
  const w = Math.max(map.getSize().x || 0, window.innerWidth || 0, 1024);
  return Math.max(3, Math.ceil(Math.log2(w / 256)));
}

function applyMinZoom() {
  const z = oneWorldMinZoom();
  map.setMinZoom(z);
  baseTiles.options.minZoom = z;
  if (map.getZoom() < z) map.setZoom(z);
}

applyMinZoom();
map.whenReady(applyMinZoom);
window.addEventListener("resize", applyMinZoom);

function waterStyle(feat) {
  const k = ((feat && feat.properties) || {}).kind || "";
  const gtype = (feat && feat.geometry && feat.geometry.type) || "";
  const line = gtype.indexOf("Line") >= 0;
  if (line) {
    const w = k === "river" ? 3.6 : k === "canal" ? 2.1 : 1.5;
    return {
      color: k === "river" ? "#3db4ff" : "#7ed0ff",
      weight: w,
      opacity: k === "stream" ? 0.7 : 0.92,
      fill: false,
    };
  }
  return {
    color: "#9ecbff",
    weight: 1.2,
    fillColor: "#3d9ee8",
    fillOpacity: 0.22,
  };
}

const lakeLayer = L.geoJSON(null, {
  style: waterStyle,
  onEachFeature: function (feat, layer) {
    const n = (feat.properties && (feat.properties.name || feat.properties.kind)) || "water";
    layer.bindTooltip(n, { sticky: true, opacity: 0.9 });
  },
}).addTo(map);
const targetLayer = L.layerGroup().addTo(map);
const pathStyle = { color: "#11a39a", weight: 3, opacity: 0.95 };
const pathCopies = [-360, 0, 360].map((off) => {
  const line = L.polyline([], pathStyle).addTo(map);
  line._lngOff = off;
  return line;
});
const pathLayer = pathCopies[1];
let lastPathPts = [];
function setMissionPath(pts) {
  lastPathPts = pts || lastPathPts || [];
  const base = shortPts(lastPathPts);
  const offs = wrapOffsets();
  pathCopies.forEach((line) => {
    if (base.length && offs.indexOf(line._lngOff) >= 0) {
      line.setLatLngs(base.map((p) => [p[0], p[1] + line._lngOff]));
    } else {
      line.setLatLngs([]);
    }
  });
}
const droneLayer = L.layerGroup().addTo(map);
let droneParts = [];
const wpLayer = L.layerGroup().addTo(map);
const splashLayer = L.layerGroup().addTo(map);
const rayLayer = L.layerGroup();
const padRayLayer = L.layerGroup();
const surveyRayLayer = L.layerGroup();
const specLayer = L.layerGroup().addTo(map);
const specParts = new Map();
const SAT_RAY_MAX_ZOOM = 10;
function satRaysWanted() {
  return map.getZoom() <= SAT_RAY_MAX_ZOOM;
}
function syncSatRays() {
  const on = satRaysWanted();
  [rayLayer, padRayLayer, surveyRayLayer].forEach((g) => {
    if (on && !map.hasLayer(g)) map.addLayer(g);
    if (!on && map.hasLayer(g)) map.removeLayer(g);
  });
}
const trailLayer = L.layerGroup().addTo(map);
const bouncePrn = {};
const surveySelLayer = L.geoJSON(null, {
  style: function (feat) {
    const gtype = (feat && feat.geometry && feat.geometry.type) || "";
    if (String(gtype).indexOf("Line") >= 0) {
      return { color: "#ffe66a", weight: 4, opacity: 0.95, fill: false };
    }
    return { color: "#7ee787", weight: 3, fillColor: "#7ee787", fillOpacity: 0.1 };
  },
}).addTo(map);
const surveyPickLayer = L.layerGroup().addTo(map);
const draftLayer = L.layerGroup().addTo(map);
const clickWpLayer = L.layerGroup().addTo(map);
const satLayer = L.layerGroup().addTo(map);
map.createPane("archive");
map.getPane("archive").style.zIndex = 350;
map.getPane("archive").style.pointerEvents = "none";

let padMarker = null;
let droneMarker = null;
let lastPlan = null;
let planGen = 0;
let targets = [];
let drawing = false;
let draft = [];
let playing = false;
let playTimer = null;
let playT = 0;
let playRate = 20;
let missionMode = "click";
let surveyStart = null;
let surveyEnd = null;
let clickWps = [];
let skySats = [];
let skyFetchTimer = null;
let lastSatRows = [];
let lastSatLive = new Set();
const satMarks = new Map();
let lastDrone = null;
let dispHdg = null;
let lastYawAt = 0;
let lastWrapKey = "";
let wrapWide = false;
let lastTableAt = 0;
let lastTrailAt = 0;
const trails = {};

function sidColor(sid) {
  const pal = ["#7ee0ff", "#ffe66a", "#ff7b72", "#7ee787", "#d2a8ff", "#ffa657", "#79c0ff", "#f778ba"];
  const s = String(sid);
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return pal[h % pal.length];
}

function sidSort(a, b) {
  const order = { G: 0, E: 1, C: 2, J: 3 };
  const oa = order[String(a)[0]] ?? 9;
  const ob = order[String(b)[0]] ?? 9;
  if (oa !== ob) return oa - ob;
  return (parseInt(String(a).replace(/\D/g, ""), 10) || 0) - (parseInt(String(b).replace(/\D/g, ""), 10) || 0);
}

function selectedConsts() {
  const map = [
    ["cG", "G"],
    ["cE", "E"],
    ["cC", "C"],
  ];
  return map.filter(([id]) => document.getElementById(id).checked).map(([, c]) => c);
}

function satConstName(c) {
  return { G: "GPS L5", E: "Galileo E5a", C: "BeiDou B2a", J: "QZSS L5" }[c] || c;
}

function satDetailHtml(row, live) {
  const maskTxt = row.above_mask || row.el >= 20 ? "above mask" : row.el > 0 ? "up, below mask" : "below horizon";
  const useTxt = live ? "bounce on now" : row.used ? "used this mission" : "not used";
  return `<b>${row.sid}</b> · ${satConstName(row.constellation)}<br>el ${Number(row.el).toFixed(0)}° · az ${Number(row.az).toFixed(0)}°<br>${Number(row.lat).toFixed(2)}, ${Number(row.lon).toFixed(2)} · ${Number(row.alt_km).toFixed(0)} km<br>${maskTxt}<br>${useTxt}`;
}

function satIcon(row, live) {
  const cls = [
    "sat-mark",
    "c" + (row.constellation || String(row.sid)[0]),
    row.above_mask || row.el >= 20 ? "up" : row.el > 0 ? "low" : "down",
    row.used ? "used" : "",
    live ? "live" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return L.divIcon({
    className: "sat-wrap",
    html: `<div class="${cls}"><svg class="sat-svg" viewBox="0 0 24 24" aria-hidden="true"><rect x="9.2" y="9.2" width="5.6" height="5.6" rx="1.1"/><rect x="1.2" y="10.2" width="6.6" height="3.6" rx="0.4"/><rect x="16.2" y="10.2" width="6.6" height="3.6" rx="0.4"/><circle cx="12" cy="12" r="1.3"/></svg><span>${row.sid}</span></div>`,
    iconSize: [54, 34],
    iconAnchor: [27, 17],
  });
}

function nearestLng(fromLon, toLon) {
  let d = toLon - fromLon;
  while (d > 180) d -= 360;
  while (d < -180) d += 360;
  return fromLon + d;
}

function shortPts(points) {
  if (!points || !points.length) return [];
  const out = [[points[0][0], points[0][1]]];
  for (let i = 1; i < points.length; i++) {
    out.push([points[i][0], nearestLng(out[i - 1][1], points[i][1])]);
  }
  return out;
}

function addShortPolyline(layer, points, style) {
  const pts = shortPts(points);
  if (pts.length < 2) return;
  L.polyline(pts, style).addTo(layer);
}

function wrapOffsets() {
  try {
    const b = map.getBounds();
    const west = b.getWest();
    const east = b.getEast();
    const span = east - west;
    if (span > 190 || west < -170 || east > 170) wrapWide = true;
    else if (span < 140 && west > -145 && east < 145) wrapWide = false;
    if (wrapWide) return [-360, 0, 360];
  } catch (_) {}
  return [0];
}

function lonInView(lon) {
  const center = map.getCenter().lng;
  let x = lon;
  while (x - center > 180) x -= 360;
  while (center - x > 180) x += 360;
  return x;
}

function lonsOnMap(lon) {
  let west = -180;
  let east = 180;
  try {
    const b = map.getBounds();
    west = b.getWest();
    east = b.getEast();
  } catch (_) {}
  const span = Math.max(1, east - west);
  const pad = Math.min(30, Math.max(2, span * 0.08));
  const base = lonInView(lon);
  const copies = [base];
  if (span > 320) copies.push(base - 360, base + 360);
  const visible = copies.filter((x) => x >= west - pad && x <= east + pad);
  return visible.length ? visible : [base];
}

function addWrappedCopies(layer, points, style) {
  const base = shortPts(points);
  if (base.length < 2) return;
  wrapOffsets().forEach((off) => {
    L.polyline(
      base.map((p) => [p[0], p[1] + off]),
      style
    ).addTo(layer);
  });
}

function wrapLngs(lon) {
  return wrapOffsets().map((off) => lon + off);
}

function satBand(row) {
  return row.above_mask || row.el >= 20 ? "up" : row.el > 0 ? "low" : "down";
}

function dropSatRec(rec) {
  (rec.mks || []).forEach((m) => satLayer.removeLayer(m));
  if (rec.ray) padRayLayer.removeLayer(rec.ray);
}

function paintSats(rows, liveSet) {
  if (!rows || !rows.length) {
    if (lastSatRows.length) rows = lastSatRows;
    else return;
  }
  const live = liveSet || new Set();
  const offs = wrapOffsets();
  const wrapKey = offs.join(",");
  const padLL = padMarker ? padMarker.getLatLng() : null;
  const wantRays = satRaysWanted();
  const seen = new Set();
  rows.forEach((row) => {
    if (!row || !row.sid) return;
    if (!prnEnabled(row.sid)) {
      const rec = satMarks.get(row.sid);
      if (rec) {
        dropSatRec(rec);
        satMarks.delete(row.sid);
      }
      return;
    }
    seen.add(row.sid);
    const isLive = live.has(row.sid);
    const useLons = lonsOnMap(row.lon);
    const iconKey = `${isLive ? 1 : 0}|${row.used ? 1 : 0}|${satBand(row)}`;
    const zOff = isLive ? 900 : row.used ? 700 : row.above_mask ? 400 : 150;
    let rec = satMarks.get(row.sid);
    if (!rec || rec.wrapKey !== wrapKey || rec.mks.length !== useLons.length) {
      if (rec) dropSatRec(rec);
      const icon = satIcon(row, isLive);
      rec = {
        mks: useLons.map((lon) => {
          const mk = L.marker([row.lat, lon], {
            icon,
            zIndexOffset: zOff,
            keyboard: false,
          });
          mk.bindTooltip(satDetailHtml(row, isLive), { direction: "top", opacity: 0.95, className: "sat-tip" });
          mk.on("click", (e) => L.DomEvent.stop(e));
          mk.addTo(satLayer);
          return mk;
        }),
        wrapKey,
        iconKey,
        ray: null,
      };
      satMarks.set(row.sid, rec);
    } else {
      useLons.forEach((lon, i) => rec.mks[i].setLatLng([row.lat, lon]));
      rec.mks.forEach((m) => m.setZIndexOffset(zOff));
      if (rec.iconKey !== iconKey) {
        const icon = satIcon(row, isLive);
        rec.mks.forEach((m) => {
          m.setIcon(icon);
          m.setTooltipContent(satDetailHtml(row, isLive));
        });
        rec.iconKey = iconKey;
      }
    }
    if (wantRays && padLL && (isLive || row.used) && row.el > 5) {
      const pts = shortPts([
        [padLL.lat, padLL.lng],
        [row.lat, lonInView(row.lon)],
      ]);
      const style = {
        color: isLive ? "#ffe66a" : "#7ee0ff",
        weight: isLive ? 1.5 : 1,
        opacity: isLive ? 0.5 : 0.22,
        dashArray: "3 6",
        interactive: false,
      };
      if (!rec.ray) {
        rec.ray = L.polyline(pts, style).addTo(padRayLayer);
      } else {
        rec.ray.setLatLngs(pts);
        rec.ray.setStyle(style);
      }
    } else if (rec.ray) {
      padRayLayer.removeLayer(rec.ray);
      rec.ray = null;
    }
  });
  satMarks.forEach((rec, sid) => {
    if (seen.has(sid)) return;
    dropSatRec(rec);
    satMarks.delete(sid);
  });
  lastSatRows = rows;
  lastSatLive = live;
  syncSatRays();
}

async function fetchSky() {
  if (lastPlan) return;
  try {
    const p = padMarker ? pad() : { lat: 40.086045, lon: -105.233634 };
    const qs = new URLSearchParams({
      start: document.getElementById("start").value,
      lat: String(p.lat),
      lon: String(p.lon),
      mask: document.getElementById("mask").value || "20",
      h_agl: document.getElementById("h_agl").value || "120",
      constellations: selectedConsts().join(",") || "G",
    });
    const r = await fetch("/api/sky?" + qs.toString());
    const d = await r.json();
    skySats = d.sats || [];
    paintSats(skySats, new Set());
  } catch (err) {
    console.warn(err);
  }
}

function scheduleSky() {
  clearTimeout(skyFetchTimer);
  skyFetchTimer = setTimeout(fetchSky, 280);
}

function prnEnabled(prn) {
  const box = document.querySelector(`#prnToggles input[data-prn="${prn}"]`);
  return !box || box.checked;
}

function setGroupOnMap(group, on) {
  if (!group) return;
  if (on && !map.hasLayer(group)) map.addLayer(group);
  if (!on && map.hasLayer(group)) map.removeLayer(group);
}

function syncBounceLayers() {
  const lakeOn = document.getElementById("lyrLake").checked;
  const landOn = document.getElementById("lyrLand").checked;
  for (const prn of Object.keys(bouncePrn)) {
    const on = prnEnabled(prn);
    setGroupOnMap(bouncePrn[prn].lake, lakeOn && on);
    setGroupOnMap(bouncePrn[prn].land, landOn && on);
  }
  if (lastPlan) renderMixed(lastPlan, playT);
  else if (lastSatRows.length) paintSats(lastSatRows, lastSatLive);
}

function addCoveragePoly(group, geom, color, onWater) {
  if (!geom) return;
  L.geoJSON(geom, {
    pane: "archive",
    style: {
      color,
      weight: onWater ? 2 : 1.5,
      opacity: onWater ? 0.9 : 0.7,
      fillColor: color,
      fillOpacity: onWater ? 0.28 : 0.12,
      dashArray: onWater ? null : "4 3",
    },
    interactive: false,
  }).addTo(group);
}

function paintFlightBounces(plan) {
  for (const prn of Object.keys(bouncePrn)) {
    setGroupOnMap(bouncePrn[prn].lake, false);
    setGroupOnMap(bouncePrn[prn].land, false);
    bouncePrn[prn].lake.clearLayers();
    bouncePrn[prn].land.clearLayers();
    delete bouncePrn[prn];
  }
  const cov = plan.coverage || { lake: {}, land: {}, counts: { lake: 0, land: 0, by_prn: {} } };
  const byPrn = cov.counts && cov.counts.by_prn ? cov.counts.by_prn : {};
  const prnSet = new Set([
    ...Object.keys(cov.lake || {}),
    ...Object.keys(cov.land || {}),
    ...Object.keys(byPrn),
  ]);
  const prns = [...prnSet].sort(sidSort);
  for (const p of prns) {
    bouncePrn[p] = { lake: L.layerGroup(), land: L.layerGroup(), nLake: 0, nLand: 0 };
    const c = byPrn[String(p)] || {};
    bouncePrn[p].nLake = c.lake || 0;
    bouncePrn[p].nLand = c.land || 0;
    addCoveragePoly(bouncePrn[p].lake, (cov.lake || {})[String(p)], sidColor(p), true);
    addCoveragePoly(bouncePrn[p].land, (cov.land || {})[String(p)], "#ff9a6b", false);
  }
  document.getElementById("nLake").textContent = `(${(cov.counts && cov.counts.lake) || 0})`;
  document.getElementById("nLand").textContent = `(${(cov.counts && cov.counts.land) || 0})`;
  const picked = pickedSids(plan);
  const box = document.getElementById("prnToggles");
  box.innerHTML = prns
    .map((p) => {
      const n = bouncePrn[p].nLake + bouncePrn[p].nLand;
      const on = !picked || picked.has(p);
      return `<label class="prn-chip" style="border-color:${sidColor(p)};color:${sidColor(p)}">
        <input type="checkbox" data-prn="${p}" ${on ? "checked" : ""} /> ${p} <span>(${n})</span>
      </label>`;
    })
    .join("");
  document.getElementById("layers").classList.remove("hidden");
  syncBounceLayers();
}

function pickedSids(plan) {
  const m = plan.meta || {};
  if (m.mode !== "survey" && m.mode !== "areas") return null;
  const s = m.survey || {};
  const ids = [...(s.sids || [])];
  if (s.aim_sid) ids.push(s.aim_sid);
  if (s.last_sid) ids.push(s.last_sid);
  const set = new Set(ids.filter(Boolean));
  return set.size ? set : null;
}

function degTxt(v) {
  return v == null || !Number.isFinite(Number(v)) ? "—" : `${Math.round(Number(v))}°`;
}

function aimLockHtml(s) {
  if (!s || !s.aim_sid) {
    return `<div class="hud-el-empty">No satellite above mask</div>`;
  }
  const sid =
    s.last_sid && s.last_sid !== s.aim_sid
      ? `${escHtml(s.aim_sid)} → ${escHtml(s.last_sid)}`
      : escHtml(s.aim_sid);
  const endEl = s.last_el != null ? s.last_el : s.aim_el;
  return `<div class="hud-el">
    <div class="hud-el-sat">${sid}</div>
    <div class="hud-el-pair">
      <div class="hud-el-cell"><span class="k">Start elevation</span><span class="v">${degTxt(s.aim_el)}</span></div>
      <div class="hud-el-cell"><span class="k">End elevation</span><span class="v">${degTxt(endEl)}</span></div>
    </div>
  </div>`;
}

function paintSurveyLock(plan) {
  splashLayer.clearLayers();
  surveyRayLayer.clearLayers();
  const lock = document.getElementById("tileLock");
  const m = plan.meta || {};
  const s = m.survey || {};
  if (m.mode !== "survey" && m.mode !== "areas") {
    lock.classList.add("hidden");
    lock.innerHTML = "";
    syncSatRays();
    return;
  }
  lock.classList.remove("hidden");
  lock.innerHTML = aimLockHtml(s);
  const hovers = plan.hovers || [];
  // Moving sweeps are already drawn by their coverage strips; only mark hover stops.
  const step = hovers.length > 80 ? Math.ceil(hovers.length / 80) : 1;
  hovers.forEach((h, i) => {
    if (h.splash_lat == null || h.splash_lon == null || h.pass_id != null) return;
    if (i % step !== 0 && i !== 0 && i !== hovers.length - 1) return;
    L.circleMarker([h.splash_lat, h.splash_lon], {
      radius: 3,
      color: "#ffe66a",
      weight: 1,
      fillColor: "#ffe66a",
      fillOpacity: 0.85,
    })
      .bindTooltip(
        `intended splash  ${h.prn || s.aim_sid || "?"}  el ${h.el != null ? Math.round(h.el) : "—"}°  (WP${i + 1})`,
        { direction: "top" }
      )
      .addTo(splashLayer);
    addWrappedCopies(
      surveyRayLayer,
      [
        [h.lat, h.lon],
        [h.splash_lat, h.splash_lon],
      ],
      { color: "#ffe66a", weight: 1, opacity: 0.35, dashArray: "3 4", interactive: false }
    );
  });
  if (s.stretch && s.stretch.coordinates && s.stretch.type === "LineString") {
    const latlngs = s.stretch.coordinates.map((p) => [p[1], p[0]]);
    addWrappedCopies(surveyRayLayer, latlngs, { color: "#ffe66a", weight: 2, opacity: 0.55, interactive: false });
  }
  syncSatRays();
}

function setStatus(msg, err) {
  const el = document.getElementById("status");
  if (!msg) {
    el.textContent = "";
    el.classList.add("hidden");
    el.classList.remove("err");
    return;
  }
  el.classList.remove("hidden");
  el.textContent = msg;
  el.classList.toggle("err", !!err);
}

function destPoint(lat, lon, north, east) {
  const mLat = 111132.92;
  const mLon = 111132.92 * Math.cos((lat * Math.PI) / 180);
  return [lat + north / mLat, lon + east / mLon];
}

function fresnelRing(lat, lon, along, across, azDeg, nPts) {
  const az = (azDeg * Math.PI) / 180;
  const n = nPts || 36;
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const th = (i / n) * 2 * Math.PI;
    const x = along * Math.cos(th);
    const y = across * Math.sin(th);
    const north = x * Math.cos(az) - y * Math.sin(az);
    const east = x * Math.sin(az) + y * Math.cos(az);
    pts.push(destPoint(lat, lon, north, east));
  }
  return pts;
}

function ringToLatLngs(coords) {
  return coords.map((p) => [p[1], p[0]]);
}

function lerp(a, b, u) {
  return a + (b - a) * u;
}

function lerpLon(a, b, u) {
  let d = b - a;
  while (d > 180) d -= 360;
  while (d < -180) d += 360;
  let lon = a + d * u;
  while (lon > 180) lon -= 360;
  while (lon < -180) lon += 360;
  return lon;
}

function lerpAngle(a, b, u) {
  let d = ((b - a + 540) % 360) - 180;
  return (a + d * u + 360) % 360;
}

function satsWithData(frames, idx, dir) {
  if (!frames || !frames.length) return [];
  if (dir >= 0) {
    for (let j = idx; j < frames.length; j++) {
      if (frames[j].sats && frames[j].sats.length) return frames[j].sats;
    }
    for (let j = idx - 1; j >= 0; j--) {
      if (frames[j].sats && frames[j].sats.length) return frames[j].sats;
    }
  } else {
    for (let j = idx; j >= 0; j--) {
      if (frames[j].sats && frames[j].sats.length) return frames[j].sats;
    }
    for (let j = idx + 1; j < frames.length; j++) {
      if (frames[j].sats && frames[j].sats.length) return frames[j].sats;
    }
  }
  return [];
}

function lerpSats(aRows, bRows, u) {
  if (!aRows || !aRows.length) return bRows && bRows.length ? bRows : [];
  if (!bRows || !bRows.length || u <= 0) return aRows;
  if (u >= 1) return bRows;
  const by = Object.create(null);
  for (const r of bRows) by[r.sid] = r;
  const seen = new Set();
  const out = [];
  for (const r of aRows) {
    seen.add(r.sid);
    const o = by[r.sid];
    if (!o) {
      out.push(r);
      continue;
    }
    const pick = u < 0.5 ? r : o;
    out.push({
      ...pick,
      lat: lerp(r.lat, o.lat, u),
      lon: lerpLon(r.lon, o.lon, u),
      el: lerp(r.el, o.el, u),
      az: lerpAngle(r.az, o.az, u),
      alt_km: lerp(r.alt_km || 0, o.alt_km || 0, u),
    });
  }
  for (const r of bRows) {
    if (!seen.has(r.sid)) out.push(r);
  }
  return out;
}

function angleDelta(from, to) {
  return ((to - from + 540) % 360) - 180;
}

function distM(lat0, lon0, lat1, lon1) {
  const dlat = lat1 - lat0;
  const dlon = (lon1 - lon0) * Math.cos((((lat0 + lat1) / 2) * Math.PI) / 180);
  return Math.hypot(dlat * 111132.92, dlon * 111132.92);
}

function courseHdg(lat0, lon0, lat1, lon1) {
  if (distM(lat0, lon0, lat1, lon1) < 0.4) return null;
  const dlat = lat1 - lat0;
  const dlon = (lon1 - lon0) * Math.cos((((lat0 + lat1) / 2) * Math.PI) / 180);
  return (Math.atan2(dlon, dlat) * (180 / Math.PI) + 360) % 360;
}

function aimWp(plan, phase) {
  const hovers = plan.hovers || [];
  const pad = plan.pad;
  if (!pad) return null;
  if (!hovers.length) return pad;
  if (phase === "climb" || phase === "hold") return hovers[0];
  if (phase === "return" || phase === "land") return pad;
  const m = /^(to|hover)_(\d+)$/.exec(String(phase || ""));
  if (!m) return hovers[0];
  const idx = Number(m[2]) - 1;
  if (m[1] === "to") return hovers[idx] || pad;
  return hovers[idx + 1] || pad;
}

function prevWp(plan, phase) {
  const hovers = plan.hovers || [];
  const pad = plan.pad;
  const m = /^to_(\d+)$/.exec(String(phase || ""));
  if (!m || !pad) return null;
  const idx = Number(m[1]) - 1;
  if (idx <= 0) return pad;
  return hovers[idx - 1] || pad;
}

function headingToWp(plan, lat, lon, phase, fallback) {
  const hovers = plan.hovers || [];
  const m = /^(to|hover)_(\d+)$/.exec(String(phase || ""));
  if (m) {
    const hov = hovers[Number(m[2]) - 1];
    if (hov && plan.meta.roi_mode === "body_yaw" && hov.yaw_deg != null) return hov.yaw_deg;
    if (plan.meta.roi_mode === "per_point" && hov && hov.splash_lat != null && hov.splash_lon != null) {
      const lookSplash = courseHdg(lat, lon, hov.splash_lat, hov.splash_lon);
      if (lookSplash != null) return lookSplash;
    }
  }
  if (plan.meta.roi_mode === "per_point" && (phase === "climb" || phase === "hold") && hovers[0] && hovers[0].splash_lat != null) {
    const look = courseHdg(lat, lon, hovers[0].splash_lat, hovers[0].splash_lon);
    if (look != null) return look;
  }
  const dest = aimWp(plan, phase);
  if (!dest) return fallback ?? 0;
  const look = courseHdg(lat, lon, dest.lat, dest.lon);
  let hdg = look ?? fallback ?? 0;
  const to = /^to_(\d+)$/.exec(String(phase || ""));
  if (to && dest) {
    const d = distM(lat, lon, dest.lat, dest.lon);
    const prev = prevWp(plan, phase);
    const leg = prev ? distM(prev.lat, prev.lon, dest.lat, dest.lon) : 40;
    const turnIn = Math.min(8, Math.max(2.5, leg * 0.18));
    const next = aimWp(plan, "hover_" + to[1]);
    const lookNext = next ? courseHdg(lat, lon, next.lat, next.lon) : null;
    if (lookNext != null && d < turnIn) {
      const u = Math.min(1, (turnIn - d) / turnIn);
      hdg = lerpAngle(hdg, lookNext, u * u);
    }
  }
  return hdg;
}

function smoothHdg(target) {
  const now = performance.now();
  const dt = lastYawAt ? (now - lastYawAt) / 1000 : 0;
  lastYawAt = now;
  if (dispHdg == null || !playing || dt <= 0 || dt > 0.2) {
    dispHdg = target;
    return dispHdg;
  }
  const d = angleDelta(dispHdg, target);
  const step = 110 * dt;
  if (Math.abs(d) <= step) dispHdg = (target + 360) % 360;
  else dispHdg = (dispHdg + Math.sign(d) * step + 360) % 360;
  return dispHdg;
}

function droneIcon(hdg) {
  return L.divIcon({
    className: "drone-wrap",
    html: `<div class="drone-tri" style="transform:rotate(${hdg}deg)"></div><div class="drone-tag">UAV</div>`,
    iconSize: [36, 36],
    iconAnchor: [18, 18],
  });
}

function setDrone(lat, lon, hdg) {
  lastDrone = { lat, lon, hdg: hdg ?? 0 };
  const lons = wrapLngs(lon);
  if (droneParts.length !== lons.length) {
    const icon = droneIcon(lastDrone.hdg);
    droneLayer.clearLayers();
    droneParts = lons.map((lng) =>
      L.marker([lat, lng], { icon, interactive: false, zIndexOffset: 1200, keyboard: false }).addTo(droneLayer)
    );
  } else {
    lons.forEach((lng, i) => {
      droneParts[i].setLatLng([lat, lng]);
      const el = droneParts[i].getElement();
      const tri = el && el.querySelector(".drone-tri");
      if (tri) tri.style.transform = `rotate(${lastDrone.hdg}deg)`;
    });
  }
  droneMarker = droneParts[lons.length === 3 ? 1 : 0] || droneParts[0];
}

function padIcon() {
  return L.divIcon({
    className: "",
    html: '<div class="pad-lab">PAD</div>',
    iconSize: [36, 16],
    iconAnchor: [18, 8],
  });
}

let padGhosts = [];
function syncPadWrap() {
  if (!padMarker) return;
  const ll = padMarker.getLatLng();
  const extras = wrapOffsets().filter((off) => off !== 0).map((off) => ll.lng + off);
  padGhosts.forEach((g) => map.removeLayer(g));
  padGhosts = extras.map((lng) =>
    L.marker([ll.lat, lng], { icon: padIcon(), interactive: false, keyboard: false }).addTo(map)
  );
}

function boxRing(a, b) {
  const minx = Math.min(a[0], b[0]);
  const maxx = Math.max(a[0], b[0]);
  const miny = Math.min(a[1], b[1]);
  const maxy = Math.max(a[1], b[1]);
  return [
    [minx, miny],
    [maxx, miny],
    [maxx, maxy],
    [minx, maxy],
    [minx, miny],
  ];
}

function paintTargets() {
  targetLayer.clearLayers();
  targets.forEach((t, i) => {
    L.polygon(ringToLatLngs(t.coordinates), {
      color: "#ffe66a",
      weight: 2,
      fillColor: "#ffe66a",
      fillOpacity: lastPlan ? 0 : 0.18,
    })
      .bindTooltip(t.name || `area ${i + 1}`, { sticky: true })
      .addTo(targetLayer);
  });
  updateSweepHint();
}

function ringAreaM2(ring) {
  const lat0 = ring.reduce((a, p) => a + p[1], 0) / ring.length;
  const mx = 111132.92 * Math.cos((lat0 * Math.PI) / 180);
  const my = 111132.92;
  let a = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    a += ring[i][0] * mx * ring[i + 1][1] * my - ring[i + 1][0] * mx * ring[i][1] * my;
  }
  return Math.abs(a) / 2;
}

function durationTxt(s) {
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

function sweepLaneM() {
  const h = Number(document.getElementById("h_agl").value) || 60;
  const el = Math.min(90, Math.max(8, Number(document.getElementById("target_el").value) || 45));
  const lambda = 299792458 / 1176.45e6;
  const strip = 2 * Math.sqrt((lambda * h) / Math.sin((el * Math.PI) / 180));
  const cov = document.getElementById("coverage").value;
  const hover = document.getElementById("survey_style").value === "hover";
  const custom = Number(document.getElementById("tile_m").value);
  let lane = cov === "custom" ? custom : (strip * 100) / Number(cov);
  if (cov !== "custom" && hover) lane /= Math.SQRT2;
  return { h, el, strip, lane, hover, custom: cov === "custom" };
}

function updateSweepHint() {
  const hint = document.getElementById("sweepHint");
  if (!hint) return;
  const { h, el, strip, lane, hover, custom } = sweepLaneM();
  document.getElementById("tileField").classList.toggle("hidden", !custom);
  if (!(lane > 0)) {
    hint.textContent = "Enter a lane spacing.";
    return;
  }
  const what = hover ? "Points every" : "Lanes every";
  const share = custom ? ` · covers about ${Math.min(100, Math.round((100 * strip) / lane))}% of the area` : "";
  let txt = `Reflection strip ≈ ${strip.toFixed(0)} m wide at ${h} m and ${el}°. ${what} ${lane.toFixed(0)} m${share}.`;
  if (missionMode === "areas" && targets.length) {
    const area = targets.reduce((a, t) => a + ringAreaM2(t.coordinates), 0);
    const speed = Number(document.getElementById("speed").value) || 8;
    if (hover) {
      const n = Math.ceil(area / (lane * lane));
      const dwell = Number(document.getElementById("loiter").value) || 0;
      txt += ` About ${n} stops, ${durationTxt(n * dwell + (n * lane) / speed)} on the area.`;
    } else {
      const km = area / lane / 1000;
      txt += ` About ${km < 10 ? km.toFixed(1) : km.toFixed(0)} km of passes, ${durationTxt((km * 1000) / speed)} at ${speed} m/s.`;
    }
  }
  hint.textContent = txt;
}

function setDrawUi() {
  document.getElementById("btnFinish").disabled = !drawing || draft.length < 2;
  document.getElementById("btnUndo").disabled = !drawing || draft.length === 0;
  document.getElementById("btnDraw").textContent = drawing ? "Drawing…" : "New area";
  document.body.classList.toggle("drawing", drawing);
}

function startAreaDraw() {
  drawing = true;
  draft = [];
  map.doubleClickZoom.disable();
  paintDraft();
  setDrawUi();
}

function stopAreaDraw() {
  drawing = false;
  draft = [];
  map.doubleClickZoom.enable();
  paintDraft();
  setDrawUi();
}

function paintDraft() {
  draftLayer.clearLayers();
  if (!draft.length) return;
  const ll = draft.map((p) => [p[1], p[0]]);
  if (draft.length === 2) {
    const ring = boxRing(draft[0], draft[1]).map((p) => [p[1], p[0]]);
    L.polygon(ring, { color: "#ffe66a", weight: 2, fillColor: "#ffe66a", fillOpacity: 0.12, interactive: false }).addTo(
      draftLayer
    );
  } else if (draft.length >= 3) {
    L.polygon(ll, { color: "#ffe66a", weight: 2, fillColor: "#ffe66a", fillOpacity: 0.12, interactive: false }).addTo(
      draftLayer
    );
  } else {
    addShortPolyline(draftLayer, ll, { color: "#ffe66a", weight: 2, interactive: false });
  }
  ll.forEach((p) =>
    L.circleMarker(p, { radius: 4, color: "#ffe66a", fillColor: "#ffe66a", fillOpacity: 1 }).addTo(draftLayer)
  );
}

function commitAreaDraft() {
  if (draft.length < 2) return false;
  let ring;
  if (draft.length === 2) ring = boxRing(draft[0], draft[1]);
  else {
    ring = draft.slice();
    if (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1]) {
      ring.push(ring[0]);
    }
  }
  targets.push({ name: `area ${targets.length + 1}`, coordinates: ring });
  stopAreaDraw();
  paintTargets();
  return true;
}

function finishDraft() {
  if (!commitAreaDraft()) return;
  computePlan();
}

document.getElementById("btnDraw").onclick = () => {
  startAreaDraw();
};
document.getElementById("btnFinish").onclick = finishDraft;
document.getElementById("btnUndo").onclick = () => {
  draft.pop();
  paintDraft();
  setDrawUi();
};
document.getElementById("btnClear").onclick = () => {
  targets = [];
  stopAreaDraw();
  paintTargets();
  startAreaDraw();
};

map.on("click", (e) => {
  if (document.body.dataset.view !== "plan") return;
  if (missionMode === "ground") {
    L.DomEvent.stop(e);
    groundClick(e.latlng);
    return;
  }
  if (missionMode === "survey") {
    pickWater(e.latlng);
    return;
  }
  if (missionMode === "click") {
    L.DomEvent.stop(e);
    clickWps.push({ lat: e.latlng.lat, lon: e.latlng.lng });
    paintClickWps();
    return;
  }
  if (missionMode === "areas") {
    if (!drawing) startAreaDraw();
    L.DomEvent.stop(e);
    draft.push([e.latlng.lng, e.latlng.lat]);
    paintDraft();
    setDrawUi();
    return;
  }
});
map.on("dblclick", (e) => {
  if (document.body.dataset.view !== "plan" || missionMode !== "areas" || !drawing) return;
  L.DomEvent.stop(e);
  if (draft.length >= 3) draft.pop();
  finishDraft();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && drawing) {
    stopAreaDraw();
    if (missionMode === "areas") startAreaDraw();
  }
});

function pad() {
  const ll = padMarker.getLatLng();
  return { lat: ll.lat, lon: ll.lng, name: "Pad" };
}

function pipLatLng(lat, lon, latlngs) {
  let inside = false;
  for (let i = 0, j = latlngs.length - 1; i < latlngs.length; j = i++) {
    const yi = latlngs[i].lat;
    const xi = latlngs[i].lng;
    const yj = latlngs[j].lat;
    const xj = latlngs[j].lng;
    const hit = yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi + 1e-18) + xi;
    if (hit) inside = !inside;
  }
  return inside;
}

function clickOnLayer(layer, latlng) {
  if (!layer.getLatLngs) return false;
  if (layer instanceof L.Polygon) {
    const rings = [];
    const walk = (x) => {
      if (!x) return;
      if (x.lat != null) return;
      if (Array.isArray(x) && x[0] && x[0].lat != null) rings.push(x);
      else if (Array.isArray(x)) x.forEach(walk);
    };
    walk(layer.getLatLngs());
    return rings.some((r) => pipLatLng(latlng.lat, latlng.lng, r));
  }
  const pts = [];
  const walk = (x) => {
    if (!x) return;
    if (x.lat != null) pts.push(x);
    else if (Array.isArray(x)) x.forEach(walk);
  };
  walk(layer.getLatLngs());
  for (let i = 0; i < pts.length - 1; i++) {
    const ab = map.distance(pts[i], pts[i + 1]);
    const ap = map.distance(latlng, pts[i]);
    const bp = map.distance(latlng, pts[i + 1]);
    if (ap + bp <= ab + 45) return true;
  }
  return false;
}

function clickOnLake(latlng) {
  let hit = false;
  lakeLayer.eachLayer((layer) => {
    if (clickOnLayer(layer, latlng)) hit = true;
  });
  return hit;
}

function paintClickWps() {
  clickWpLayer.clearLayers();
  const n = document.getElementById("nClickWps");
  if (n) n.textContent = `${clickWps.length} ${clickWps.length === 1 ? "waypoint" : "waypoints"}`;
  if (!clickWps.length) return;
  const padLL = padMarker ? padMarker.getLatLng() : null;
  const line = clickWps.map((w) => [w.lat, w.lon]);
  if (padLL) line.unshift([padLL.lat, padLL.lng]);
  addWrappedCopies(clickWpLayer, line, { color: "#11a39a", weight: 2.5, dashArray: "6 5", interactive: false });
  clickWps.forEach((w, i) => {
    wrapLngs(w.lon).forEach((lon) => {
      L.circleMarker([w.lat, lon], {
        radius: 6,
        color: "#fff",
        weight: 2,
        fillColor: "#0b7068",
        fillOpacity: 1,
      }).addTo(clickWpLayer);
      L.marker([w.lat, lon], {
        icon: L.divIcon({
          className: "",
          html: `<div class="wp-label">WP${i + 1}</div>`,
          iconSize: [40, 16],
          iconAnchor: [20, 22],
        }),
        interactive: false,
      }).addTo(clickWpLayer);
    });
  });
}

function resetMission({ keepSelection = false } = {}) {
  window.dispatchEvent(new Event("heron:plan-invalid"));
  planGen += 1;
  stopPlay();
  lastPlan = null;
  playT = 0;
  dispHdg = null;
  lastYawAt = 0;
  lastTableAt = 0;
  lastTrailAt = 0;
  lastSatLive = new Set();
  for (const k of Object.keys(trails)) delete trails[k];
  trailLayer.clearLayers();
  clearSpecParts();
  splashLayer.clearLayers();
  surveyRayLayer.clearLayers();
  wpLayer.clearLayers();
  lastPathPts = [];
  setMissionPath([]);
  for (const prn of Object.keys(bouncePrn)) {
    setGroupOnMap(bouncePrn[prn].lake, false);
    setGroupOnMap(bouncePrn[prn].land, false);
    bouncePrn[prn].lake.clearLayers();
    bouncePrn[prn].land.clearLayers();
    delete bouncePrn[prn];
  }
  if (!keepSelection) {
    clickWps = [];
    targets = [];
    surveyStart = null;
    surveyEnd = null;
    surveySelLayer.clearLayers();
    surveyPickLayer.clearLayers();
    clickWpLayer.clearLayers();
    stopAreaDraw();
    paintClickWps();
    paintSurveyPicks();
  }
  targetLayer.clearLayers();
  paintTargets();
  document.getElementById("layers").classList.add("hidden");
  document.getElementById("prnToggles").innerHTML = "";
  document.getElementById("nLake").textContent = "";
  document.getElementById("nLand").textContent = "";
  document.getElementById("summary").classList.add("hidden");
  document.getElementById("summary").innerHTML = "";
  document.getElementById("tableWrap").classList.add("hidden");
  document.getElementById("tbody").innerHTML = "";
  document.getElementById("exports").classList.add("hidden");
  document.getElementById("exports").innerHTML = "";
  document.getElementById("sliderWrap").classList.add("hidden");
  document.getElementById("tileLock").classList.add("hidden");
  document.getElementById("tileLock").innerHTML = "";
  document.getElementById("hudPhase").textContent = "Standby";
  document.getElementById("hudAgl").textContent = "—";
  document.getElementById("hudAglFt").textContent = "";
  document.getElementById("hudMsl").textContent = "—";
  document.getElementById("hudSpd").textContent = "—";
  document.getElementById("hudSpdKt").textContent = "";
  document.getElementById("hudVs").textContent = "—";
  document.getElementById("hudHdg").textContent = "—";
  document.getElementById("hudPos").textContent = "—";
  document.getElementById("hudSat").textContent = "Waiting for mission data";
  document.getElementById("go").disabled = false;
  if (padMarker) {
    const ll = padMarker.getLatLng();
    setDrone(ll.lat, ll.lng, 180);
  }
  fetchSky();
  setStatus("");
}

function setMode(m) {
  if (m !== missionMode) resetMission();
  missionMode = m;
  const modeStates = [
    ["modeClick", m === "click"],
    ["modeAreas", m === "areas"],
    ["modeSurvey", m === "survey"],
    ["modeGround", m === "ground"],
  ];
  modeStates.forEach(([id, active]) => {
    const button = document.getElementById(id);
    button.classList.toggle("on", active);
    button.setAttribute("aria-pressed", String(active));
  });
  document.getElementById("clickTools").classList.toggle("hidden", m !== "click");
  document.getElementById("areaTools").classList.toggle("hidden", m !== "areas");
  document.getElementById("surveyTools").classList.toggle("hidden", m !== "survey");
  document.getElementById("windowField").classList.add("hidden");
  document.getElementById("tileTools").classList.toggle("hidden", m !== "survey" && m !== "areas");
  document.body.classList.toggle("survey", m === "survey");
  document.body.classList.toggle("click-wps", m === "click");
  document.getElementById("groundTools").classList.toggle("hidden", m !== "ground");
  document.body.classList.toggle("ground", m === "ground");
  document.querySelector("#go span").textContent = m === "ground" ? "Show reflections" : "Build mission";
  if (m === "ground") groundEnter();
  else groundLayer.clearLayers();
  if (m !== "survey") {
    surveyStart = null;
    surveyEnd = null;
    surveySelLayer.clearLayers();
    surveyPickLayer.clearLayers();
  }
  if (m !== "click") clickWpLayer.clearLayers();
  else paintClickWps();
  if (m !== "areas") stopAreaDraw();
  if (m === "survey") {
    paintSurveyPicks();
  } else if (m === "areas") {
    startAreaDraw();
  }
  updateSweepHint();
  setStatus("");
}

function layerIsLine(layer) {
  const g = (layer.feature && layer.feature.geometry) || {};
  return String(g.type || "").indexOf("Line") >= 0;
}

function paintSurveyPicks() {
  surveyPickLayer.clearLayers();
  const n = document.getElementById("nSurveyPicks");
  if (!surveyStart) {
    if (n) n.textContent = "No water selected";
    return;
  }
  const mark = (ll, label) => {
    L.circleMarker([ll.lat, ll.lon], {
      radius: 7,
      color: "#14110b",
      weight: 2,
      fillColor: "#ffe66a",
      fillOpacity: 1,
    }).addTo(surveyPickLayer);
    L.marker([ll.lat, ll.lon], {
      icon: L.divIcon({
        className: "",
        html: `<div class="seg-lab">${label}</div>`,
        iconSize: [44, 16],
        iconAnchor: [22, 22],
      }),
      interactive: false,
    }).addTo(surveyPickLayer);
  };
  mark(surveyStart, "START");
  if (surveyEnd) {
    mark(surveyEnd, "END");
    if (n) n.textContent = "start + end";
  } else if (layerIsLine(surveyStart.layer)) {
    if (n) n.textContent = "start set — click end";
  } else if (n) n.textContent = "lake selected";
}

function pickWater(latlng) {
  let found = null;
  lakeLayer.eachLayer((layer) => {
    if (!found && clickOnLayer(layer, latlng)) found = layer;
  });
  if (!found) {
    setStatus("Select a waterbody first.", true);
    return;
  }
  surveySelLayer.clearLayers();
  surveySelLayer.addData(found.toGeoJSON());
  if (layerIsLine(found)) {
    if (!surveyStart || surveyStart.layer !== found || surveyEnd) {
      surveyStart = { lat: latlng.lat, lon: latlng.lng, layer: found };
      surveyEnd = null;
      paintSurveyPicks();
      return;
    }
    surveyEnd = { lat: latlng.lat, lon: latlng.lng };
    paintSurveyPicks();
    computePlan();
    return;
  }
  surveyStart = { lat: latlng.lat, lon: latlng.lng, layer: found };
  surveyEnd = null;
  paintSurveyPicks();
  computePlan();
}

async function computePlan() {
  window.dispatchEvent(new Event("heron:plan-invalid"));
  const gen = ++planGen;
  if (missionMode === "survey" && !surveyStart) {
    setStatus("Select a waterbody first.", true);
    return;
  }
  if (missionMode === "survey" && surveyStart && layerIsLine(surveyStart.layer) && !surveyEnd) {
    setStatus("Click the end of the river stretch.", true);
    return;
  }
  if (missionMode === "click" && !clickWps.length) {
    setStatus("Add at least one waypoint.", true);
    return;
  }
  if (missionMode === "areas") {
    commitAreaDraft();
    if (!targets.length) {
      setStatus("Draw an area on the map first.", true);
      return;
    }
  }
  if (!selectedConsts().length) {
    setStatus("Turn on at least one constellation.", true);
    return;
  }
  const btn = document.getElementById("go");
  btn.disabled = true;
  const msg =
    missionMode === "survey"
      ? "Tiling the waterbody…"
      : missionMode === "click"
        ? "Speculars along your route…"
        : "Tiling the box…";
  setStatus(msg);
  try {
    const tileVal = document.getElementById("tile_m").value;
    const body = {
      start: document.getElementById("start").value,
      duration_min: Number(document.getElementById("duration").value),
      h_agl: Number(document.getElementById("h_agl").value),
      speed_mps: Number(document.getElementById("speed").value),
      loiter_s: Number(document.getElementById("loiter").value),
      elev_mask: Number(document.getElementById("mask").value),
      constellations: selectedConsts(),
      pad: pad(),
      targets,
      step_s: 15,
      mode: missionMode,
    };
    if (missionMode === "survey" || missionMode === "areas") {
      const tel = Number(document.getElementById("target_el").value);
      if (Number.isFinite(tel)) body.target_el = tel;
    }
    if (missionMode === "survey" || missionMode === "areas") {
      if (missionMode === "survey") {
        body.click_lat = surveyStart.lat;
        body.click_lon = surveyStart.lon;
        if (surveyEnd) {
          body.click_lat2 = surveyEnd.lat;
          body.click_lon2 = surveyEnd.lon;
        }
      }
      body.max_wp = Number(document.getElementById("max_wp").value);
      body.survey_style = document.getElementById("survey_style").value;
      body.roi_mode = document.getElementById("roi_mode").value;
      body.antenna_offset_deg = Number(document.getElementById("antenna_offset_deg").value);
      body.max_leg_m = Number(document.getElementById("max_leg_m").value);
      const bearing = document.getElementById("sweep_bearing").value;
      if (bearing !== "") body.sweep_bearing = Number(bearing);
      const coverage = document.getElementById("coverage").value;
      if (coverage === "custom") {
        if (!(Number(tileVal) > 0)) throw new Error("Enter a lane spacing, or pick a Coverage preset.");
        body.tile_m = Number(tileVal);
      } else {
        body.coverage_pct = Number(coverage);
      }
    }
    if (missionMode === "click") body.waypoints = clickWps;
    const r = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await r.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error("Planner error (not JSON). " + text.replace(/<[^>]+>/g, " ").slice(0, 160));
    }
    if (!r.ok) throw new Error(data.error || text.slice(0, 160));
    if (gen !== planGen) return;
    showPlan(data);
  } catch (e) {
    if (gen !== planGen) return;
    setStatus(String(e), true);
  } finally {
    if (gen === planGen) btn.disabled = false;
  }
}

function phaseLabel(p) {
  if (!p) return "—";
  if (p === "climb") return "climb";
  if (p === "hold") return "pad hold";
  if (p === "return") return "RTL";
  if (p === "land") return "land";
  if (p.startsWith("hover_")) return `hover WP${p.slice(6)}`;
  if (p.startsWith("to_")) return `to WP${p.slice(3)}`;
  return p;
}

function mixedAt(plan, tSec) {
  const frames = plan.frames;
  if (!frames.length) return null;
  if (tSec <= frames[0].t) return { ...frames[0], u: 0, i: 0, sats: satsWithData(frames, 0, 1) };
  if (tSec >= frames[frames.length - 1].t) {
    const i = frames.length - 1;
    return { ...frames[i], u: 1, i, sats: satsWithData(frames, i, -1) };
  }
  let i = 0;
  while (i < frames.length - 1 && frames[i + 1].t < tSec) i += 1;
  const a = frames[i];
  const b = frames[i + 1];
  const span = Math.max(1e-4, b.t - a.t);
  const u = Math.min(1, Math.max(0, (tSec - a.t) / span));
  const byPrn = {};
  for (const s of a.speculars) byPrn[s.prn] = { a: s };
  for (const s of b.speculars) {
    if (!byPrn[s.prn]) byPrn[s.prn] = {};
    byPrn[s.prn].b = s;
  }
  const speculars = [];
  for (const prn of Object.keys(byPrn)) {
    const pair = byPrn[prn];
    if (!pair.a || !pair.b) continue;
    const src = pair.a;
    const dst = pair.b;
    speculars.push({
      ...dst,
      lat: lerp(src.lat, dst.lat, u),
      lon: lerpLon(src.lon, dst.lon, u),
      el: lerp(src.el, dst.el, u),
      az: lerpAngle(src.az, dst.az, u),
      fresnel_along_m: lerp(src.fresnel_along_m, dst.fresnel_along_m, u),
      fresnel_across_m: lerp(src.fresnel_across_m, dst.fresnel_across_m, u),
    });
  }
  speculars.sort((x, y) => y.el - x.el);
  const phase = u < 1 ? a.phase : b.phase;
  const lat = lerp(a.drone.lat, b.drone.lat, u);
  const lon = lerpLon(a.drone.lon, b.drone.lon, u);
  const spd = lerp(a.drone.speed_mps || 0, b.drone.speed_mps || 0, u);
  const satRows = lerpSats(satsWithData(frames, i, -1), satsWithData(frames, i + 1, 1), u);
  return {
    t: tSec,
    iso: new Date(Date.parse(plan.meta.start) + tSec * 1000).toISOString(),
    phase,
    drone: {
      lat,
      lon,
      hdg: headingToWp(plan, lat, lon, phase, a.drone.hdg ?? 0),
      alt_agl: lerp(a.drone.alt_agl || 0, b.drone.alt_agl || 0, u),
      alt_m: lerp(a.drone.alt_m || 0, b.drone.alt_m || 0, u),
      speed_mps: spd,
      vs_mps: lerp(a.drone.vs_mps || 0, b.drone.vs_mps || 0, u),
    },
    speculars,
    sats: satRows,
    u,
    i,
  };
}

function drawTrails(plan, tSec, current) {
  trailLayer.clearLayers();
  const runs = new Map();
  function flush(prn) {
    const pts = runs.get(prn) || [];
    if (pts.length > 1 && prnEnabled(prn)) addShortPolyline(trailLayer, pts, { color: "#7ee0ff", weight: 2, opacity: 0.45, interactive: false });
    runs.delete(prn);
  }
  const frames = plan.frames.filter(f => f.t >= tSec - 60 && f.t <= tSec);
  if (!frames.length || frames[frames.length-1].t < tSec) frames.push(current);
  for (const frame of frames) {
    const present = new Set(frame.speculars.map(s => s.prn));
    for (const prn of runs.keys()) if (!present.has(prn)) flush(prn);
    for (const s of frame.speculars) {
      if (!runs.has(s.prn)) runs.set(s.prn, []);
      runs.get(s.prn).push([s.lat, s.lon]);
    }
  }
  for (const prn of [...runs.keys()]) flush(prn);
}

function prnLabIcon(prn, hit) {
  return L.divIcon({
    className: "",
    html: `<div class="prn-lab${hit ? " hit" : ""}">${prn}</div>`,
    iconSize: [52, 16],
    iconAnchor: [26, 8],
  });
}

function dropSpecPart(part) {
  if (!part) return;
  specLayer.removeLayer(part.outline);
  specLayer.removeLayer(part.fill);
  (part.labels || []).forEach((m) => specLayer.removeLayer(m));
  if (part.ray) rayLayer.removeLayer(part.ray);
}

function clearSpecParts() {
  specParts.forEach((part) => dropSpecPart(part));
  specParts.clear();
  specLayer.clearLayers();
  rayLayer.clearLayers();
}

function upsertSpec(s, drone, hit, col) {
  const ring = fresnelRing(s.lat, s.lon, s.fresnel_along_m, s.fresnel_across_m, s.az);
  const tip = `${s.prn}  ${s.fresnel_along_m.toFixed(0)}×${s.fresnel_across_m.toFixed(0)} m  el ${s.el.toFixed(0)}°`;
  const lons = wrapLngs(s.lon);
  let part = specParts.get(s.prn);
  if (!part) {
    const outline = L.polygon(ring, {
      color: "#000",
      weight: hit ? 6 : 5,
      fillOpacity: 0,
      opacity: 0.55,
      interactive: false,
    }).addTo(specLayer);
    const fill = L.polygon(ring, {
      color: col,
      weight: hit ? 3.5 : 2.5,
      fillColor: col,
      fillOpacity: hit ? 0.38 : s.on_water ? 0.22 : 0.08,
    })
      .bindTooltip(tip, { sticky: true })
      .addTo(specLayer);
    const labels = lons.map((lon) =>
      L.marker([s.lat, lon], {
        icon: prnLabIcon(s.prn, hit),
        interactive: false,
        zIndexOffset: 800,
      }).addTo(specLayer)
    );
    part = { outline, fill, labels, hit, ray: null };
    specParts.set(s.prn, part);
  } else {
    part.outline.setLatLngs(ring);
    part.fill.setLatLngs(ring);
    part.outline.setStyle({ weight: hit ? 6 : 5 });
    part.fill.setStyle({
      color: col,
      fillColor: col,
      weight: hit ? 3.5 : 2.5,
      fillOpacity: hit ? 0.38 : s.on_water ? 0.22 : 0.08,
    });
    part.fill.setTooltipContent(tip);
    if (part.labels.length !== lons.length) {
      part.labels.forEach((m) => specLayer.removeLayer(m));
      part.labels = lons.map((lon) =>
        L.marker([s.lat, lon], {
          icon: prnLabIcon(s.prn, hit),
          interactive: false,
          zIndexOffset: 800,
        }).addTo(specLayer)
      );
      part.hit = hit;
    } else {
      lons.forEach((lon, i) => part.labels[i].setLatLng([s.lat, lon]));
      if (part.hit !== hit) {
        part.labels.forEach((m) => m.setIcon(prnLabIcon(s.prn, hit)));
        part.hit = hit;
      }
    }
  }
  if (satRaysWanted()) {
    const pts = shortPts([
      [drone.lat, drone.lon],
      [s.lat, s.lon],
    ]);
    const rayStyle = {
      color: col,
      weight: hit ? 2.5 : 1.5,
      opacity: hit ? 0.95 : 0.55,
      dashArray: hit ? null : "4 4",
    };
    if (!part.ray) {
      part.ray = L.polyline(pts, { ...rayStyle, interactive: false }).addTo(rayLayer);
    } else {
      part.ray.setLatLngs(pts);
      part.ray.setStyle(rayStyle);
    }
  } else if (part.ray) {
    rayLayer.removeLayer(part.ray);
    part.ray = null;
  }
  part.keep = true;
}

function sweepSpecParts() {
  specParts.forEach((part, prn) => {
    if (part.keep) {
      part.keep = false;
      return;
    }
    dropSpecPart(part);
    specParts.delete(prn);
  });
}

function renderMixed(plan, tSec) {
  const f = mixedAt(plan, tSec);
  if (!f) return;
  const slider = document.getElementById("slider");
  if (document.activeElement !== slider) slider.value = String(f.t);
  document.getElementById("playElapsed").textContent = `+${Math.round(f.t)}s`;
  document.getElementById("playPhase").textContent = phaseLabel(f.phase);
  const iso = f.iso || "";
  const day = iso.slice(0, 10);
  const clock = iso.slice(11, 19);
  document.getElementById("playClock").innerHTML = day && clock
    ? `<em>${escHtml(day)}</em>${escHtml(clock)} UTC`
    : "<em>—</em>";

  const want = headingToWp(plan, f.drone.lat, f.drone.lon, f.phase, f.drone.hdg);
  const hdg = playing ? smoothHdg(want) : want;
  f.drone.hdg = hdg;
  setDrone(f.drone.lat, f.drone.lon, hdg);
  const tb = document.getElementById("tbody");
  const now = performance.now();
  const paintTable = !playing || now - lastTableAt > 120;
  if (paintTable) {
    lastTableAt = now;
    tb.innerHTML = "";
  }

  const hits = [];
  const live = new Set();
  for (const s of f.speculars) {
    if (!prnEnabled(s.prn)) continue;
    const picked = !!(s.in_target || s.aimed);
    const wet = !!s.on_water;
    const hit = picked || wet;
    const col = hit ? "#ffe66a" : "#6b7584";
    upsertSpec(s, f.drone, hit, col);
    // Trails are reconstructed from mission time, never from display refreshes.
    if (hit) live.add(s.prn);
    if (picked) hits.push(`${s.prn} ${s.el.toFixed(0)}°`);

    if (paintTable) {
      const tr = document.createElement("tr");
      const zone = `${s.fresnel_along_m.toFixed(0)}×${s.fresnel_across_m.toFixed(0)}`;
      const hitTxt = s.aimed ? "lock" : s.in_target ? "area" : s.on_water ? "water" : "land";
      tr.innerHTML = `
      <td>${s.prn}</td>
      <td>${s.el.toFixed(0)}</td>
      <td>${s.az.toFixed(0)}</td>
      <td>${s.extra_m ?? "—"}</td>
      <td>${s.samples ?? "—"}</td>
      <td>${zone}</td>
      <td class="${hit ? "ok" : "no"}">${hitTxt}</td>`;
      tb.appendChild(tr);
    }
  }
  sweepSpecParts();
  if (!playing || now - lastTrailAt > 80) {
    lastTrailAt = now;
    drawTrails(plan, tSec, f);
  }
  if (paintTable && !f.speculars.length) {
    tb.innerHTML = '<tr><td colspan="7" class="no">no sats above mask</td></tr>';
  }

  const d = f.drone;
  const agl = d.alt_agl || 0;
  const spd = d.speed_mps || 0;
  const vs = d.vs_mps || 0;
  const vsSign = vs >= 0.05 ? "+" : vs <= -0.05 ? "" : "";
  document.getElementById("hudPhase").textContent = phaseLabel(f.phase);
  document.getElementById("hudAgl").textContent = `${agl.toFixed(0)}`;
  document.getElementById("hudAglFt").textContent = `${(agl * 3.28084).toFixed(0)} ft`;
  document.getElementById("hudMsl").textContent = `${(d.alt_m || 0).toFixed(0)}`;
  document.getElementById("hudSpd").textContent = spd.toFixed(1);
  document.getElementById("hudSpdKt").textContent = `${(spd * 1.94384).toFixed(0)} kt`;
  document.getElementById("hudVs").textContent = `${vsSign}${vs.toFixed(1)}`;
  document.getElementById("hudHdg").textContent = `${Math.round(d.hdg || 0)}`;
  document.getElementById("hudPos").textContent = `${d.lat.toFixed(5)}  ${d.lon.toFixed(5)}`;
  document.getElementById("hudSat").textContent =
    agl < 5
      ? "Landed"
      : hits.length
        ? hits.join("   ")
        : f.speculars.length
          ? `${f.speculars.length} bounce`
          : "No sats above mask";
  const satRows = f.sats && f.sats.length ? f.sats : lastSatRows.length ? lastSatRows : skySats;
  paintSats(satRows, live);
}

function stopPlay() {
  playing = false;
  if (playTimer) cancelAnimationFrame(playTimer);
  playTimer = null;
  const btn = document.getElementById("btnPlay");
  btn.classList.remove("playing");
  btn.setAttribute("aria-label", "Play mission");
}

function startPlay(plan) {
  if (playing) {
    stopPlay();
    renderMixed(plan, playT);
    return;
  }
  playing = true;
  const btn = document.getElementById("btnPlay");
  btn.classList.add("playing");
  btn.setAttribute("aria-label", "Pause mission");
  const lastT = plan.frames[plan.frames.length - 1].t;
  if (playT >= lastT) playT = 0;
  let last = performance.now();
  const tick = (now) => {
    if (!playing) return;
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    playT += dt * playRate;
    if (playT >= lastT) {
      playT = lastT;
      stopPlay();
      renderMixed(plan, playT);
      return;
    }
    renderMixed(plan, playT);
    playTimer = requestAnimationFrame(tick);
  };
  playTimer = requestAnimationFrame(tick);
}

async function loadDefaults() {
  const r = await fetch("/api/defaults");
  const d = await r.json();
  lakeLayer.clearLayers();
  lakeLayer.addData(d.lake);
  const br = (d.lake.features || []).find((f) => (f.properties && f.properties.name) === "Boulder Reservoir");
  const fit = br ? L.geoJSON(br).getBounds() : lakeLayer.getBounds();
  map.fitBounds(fit, { paddingTopLeft: [340, 20], paddingBottomRight: [240, 20] });

  padMarker = L.marker([d.pad.lat, d.pad.lon], {
    draggable: true,
    title: "takeoff / land — drag",
    icon: padIcon(),
  }).addTo(map);
  padMarker.on("drag", syncPadWrap);
  padMarker.on("dragend", () => {
    syncPadWrap();
    scheduleSky();
  });
  syncPadWrap();
  setDrone(d.pad.lat, d.pad.lon, 180);

  targets = [];
  document.getElementById("start").value = d.start;
  document.getElementById("duration").value = d.duration_min;
  document.getElementById("h_agl").value = d.h_agl;
  document.getElementById("speed").value = d.speed_mps;
  document.getElementById("loiter").value = d.loiter_s;
  document.getElementById("mask").value = d.elev_mask;
  setMode("click");
  setStatus("");
  fetchSky();
}

function escHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function statCell(k, v, u) {
  return `<div class="stat-cell"><span class="k">${k}</span><span class="v">${v}</span>${u ? `<span class="u">${u}</span>` : ""}</div>`;
}

function satChips(prns) {
  if (!prns || !prns.length) return '<span class="stat-empty">none this flight</span>';
  return prns
    .map((p) => {
      const c = sidColor(p);
      return `<span class="sat-chip" style="color:${c};border-color:${c}">${escHtml(p)}</span>`;
    })
    .join("");
}

function paintSummary(plan) {
  const m = plan.meta || {};
  const sum = document.getElementById("summary");
  const mode =
    m.mode === "survey" ? "Tile water" : m.mode === "click" || m.mode === "manual" ? "Route" : "Measure";
  const when = (m.start || "").replace("T", " ").slice(0, 16);
  const mins = (Number(m.total_s) / 60).toFixed(1);
  const dist = Number(m.transit_m) || 0;
  const distTxt = dist >= 1000 ? (dist / 1000).toFixed(2) : String(Math.round(dist));
  const distUnit = dist >= 1000 ? "km" : "m";
  const prns = m.l5_prns_on_water || [];
  sum.innerHTML = `
    <div class="sum-top">
      <div class="sum-mode">${mode}</div>
      <div class="sum-when">${escHtml(when)} UTC</div>
    </div>
    <div class="stat-grid">
      ${statCell("Waypoints", m.n_hovers ?? "—")}
      ${statCell("Mission items", plan.exports?.mission?.n ?? "—")}
      ${statCell("Duration", mins, "min")}
      ${statCell("Distance", distTxt, distUnit)}
      ${statCell("Speed", m.speed_mps ?? "—", "m/s")}
      ${statCell("Hover", Number(m.loiter_s) < 0.05 ? "fly-through" : (m.loiter_s ?? "—"), Number(m.loiter_s) < 0.05 ? "" : "s / WP")}
      ${statCell("L5 on water", prns.length)}
    </div>
    ${m.survey?.sweep_bearing_deg != null ? `<p>${escHtml(String(m.survey.sweep_bearing_deg))}° sweep · ${m.survey.n_passes} passes · lanes every ${m.survey.lane_spacing_m} m · strip ≈ ${m.survey.footprint_m} m wide</p>` : ""}
    ${m.survey?.sampled_yaw_error_deg != null ? `<p>Sampled heading-setpoint error: ${m.survey.sampled_yaw_error_deg}°. This excludes aircraft yaw lag and fixed mounting tilt.</p>` : ""}
    ${m.survey?.sampled_tracking_error_m != null ? `<p>Sampled reflection error: ${m.survey.sampled_tracking_error_m} m between checkpoints. Geometry only; turns and aircraft response are not modeled.</p>` : ""}
    ${plan.coverage?.target ? `<p><b>${plan.coverage.target.percent}% geometric target coverage</b> · pink shows missed area. Selected satellite, survey passes only; not a received-signal guarantee.</p>` : ""}
    ${m.survey?.unchecked_tracking_segments ? `<p>Some legs could not be checked for reflection tracking because satellite visibility changed.</p>` : ""}
    ${m.survey ? `<p>${m.roi_mode === "body_yaw" ? "Body yaw follows predicted reflection azimuth; antenna tilt is fixed." : m.roi_mode === "none" ? "No pointing commands; antenna heading is uncontrolled." : "Fixed-ground ROI; not continuous reflection tracking."} Verify the simulated reflection coverage before flight.</p>` : ""}
    <div class="sum-sats">
      <div class="sum-sats-label">L5 on water</div>
      <div class="sum-chips">${satChips(prns)}</div>
    </div>`;
  sum.classList.remove("hidden");
}

function showPlan(plan) {
  lastPlan = plan;
  window.dispatchEvent(new CustomEvent("heron:plan-ready", { detail: plan }));
  stopPlay();
  playT = 0;
  dispHdg = null;
  lastYawAt = 0;
  for (const k of Object.keys(trails)) delete trails[k];
  trailLayer.clearLayers();
  clearSpecParts();

  const m = plan.meta;
  paintTargets();
  if (plan.coverage?.target?.uncovered) {
    L.geoJSON(plan.coverage.target.uncovered, {style:{color:"#d83b65",weight:1,fillColor:"#d83b65",fillOpacity:0.3},interactive:false}).addTo(targetLayer);
  }
  const hovers = plan.hovers || [];
  const pts = [[plan.pad.lat, plan.pad.lon], ...hovers.map((h) => [h.lat, h.lon]), [plan.pad.lat, plan.pad.lon]];
  setMissionPath(pts);

  wpLayer.clearLayers();
  const dense = hovers.length > 48;
  hovers.forEach((h, i) => {
    const keepLabel = !dense || i === 0 || i === hovers.length - 1 || i % Math.ceil(hovers.length / 24) === 0;
    wrapLngs(h.lon).forEach((lon) => {
      L.circleMarker([h.lat, lon], {
        radius: dense ? 3 : 7,
        color: "#fff",
        weight: dense ? 1 : 2,
        fillColor: "#1f6feb",
        fillOpacity: 1,
      }).addTo(wpLayer);
      if (!keepLabel) return;
      L.marker([h.lat, lon], {
        icon: L.divIcon({
          className: "",
          html: `<div class="wp-label">WP${i + 1}</div>`,
          iconSize: [40, 16],
          iconAnchor: [20, 22],
        }),
      })
        .bindTooltip(
          `${h.name}` +
            (h.prn ? `  ${h.prn} @ ${Math.round(h.el)}°` : "  centroid (no sat lined up)") +
            ((h.geometry_s ?? h.t_in_window) != null ? `  geometry +${Math.round(h.geometry_s ?? h.t_in_window)}s` : ""),
          { direction: "top" }
        )
        .addTo(wpLayer);
    });
  });

  paintFlightBounces(plan);
  paintSurveyLock(plan);
  paintSummary(plan);

  const slider = document.getElementById("slider");
  document.getElementById("sliderWrap").classList.remove("hidden");
  document.getElementById("tableWrap").classList.remove("hidden");
  const lastT = plan.frames[plan.frames.length - 1].t;
  slider.min = 0;
  slider.max = lastT;
  slider.step = 0.05;
  slider.value = 0;
  slider.oninput = () => {
    stopPlay();
    playT = Number(slider.value);
    renderMixed(plan, playT);
  };
  document.getElementById("btnPlay").onclick = () => startPlay(plan);
  renderMixed(plan, 0);

  const ex = document.getElementById("exports");
  ex.classList.remove("hidden");
  const fname = (plan.exports && plan.exports.filename) || "HERON.waypoints";
  ex.innerHTML = `
    <div class="export-k">ArduPilot / Mission Planner</div>
    <a class="export-btn" href="${plan.exports.waypoints}" download="${escHtml(fname)}">Download .waypoints</a>
    <a href="${plan.exports.runcard}" download>Run card</a>`;
  setStatus("");
}

const GROUND_TZ = "America/Denver";
const groundLayer = L.layerGroup().addTo(map);
const groundNowLayer = L.layerGroup();
const ground = { rx: null, water: null, geoid: null, result: null, times: [], byTime: new Map(), idx: 0, gen: 0, sky: [] };

function groundClock(iso, opts) {
  return new Intl.DateTimeFormat("en-US", { timeZone: GROUND_TZ, hour: "2-digit", minute: "2-digit", hour12: false, ...opts }).format(new Date(iso));
}

function groundZone(dateStr) {
  const day = String(dateStr || "").slice(0, 10) || "2026-10-12";
  const probe = new Date(`${day}T18:00:00Z`);
  const part = (style) => new Intl.DateTimeFormat("en-US", { timeZone: GROUND_TZ, timeZoneName: style })
    .formatToParts(probe).find((p) => p.type === "timeZoneName").value;
  return { abbr: part("short"), long: part("long") };
}

function paintGroundZone() {
  const z = groundZone(document.getElementById("g_from").value);
  document.getElementById("g_tz").textContent = `Times are ${z.long} (${z.abbr}).`;
  return z;
}

function nextMondayLocal() {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", { timeZone: GROUND_TZ, year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" })
      .formatToParts(new Date())
      .map((p) => [p.type, p.value])
  );
  const d = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day)));
  const dow = d.getUTCDay();
  d.setUTCDate(d.getUTCDate() + (dow === 1 ? 0 : (8 - dow) % 7));
  return d.toISOString().slice(0, 10);
}

function groundNum(id) {
  return Number(document.getElementById(id).value);
}

function paintGroundPlace() {
  groundLayer.clearLayers();
  if (ground.result) {
    L.geoJSON(ground.result.water.geojson, { style: { color: "#ffe66a", weight: 2, fill: false }, interactive: false }).addTo(groundLayer);
  }
  if (ground.rx) {
    L.marker([ground.rx.lat, ground.rx.lon], {
      icon: L.divIcon({ className: "", html: '<div class="ground-rx"></div>', iconSize: [16, 16], iconAnchor: [8, 8] }),
      title: "Receiver",
    }).addTo(groundLayer);
  }
  if (ground.water && !ground.result) {
    L.circleMarker([ground.water.lat, ground.water.lon], { radius: 6, color: "#14110b", weight: 2, fillColor: "#ffe66a", fillOpacity: 1 }).addTo(groundLayer);
  }
  groundNowLayer.addTo(groundLayer);
}

async function groundLookup(lat, lon) {
  const r = await fetch(`/api/elevation?lat=${lat}&lon=${lon}`);
  const d = await r.json();
  if (!r.ok || d.ground_m == null) throw new Error(d.error || "No elevation at that point.");
  return d;
}

let groundDefaults = null;
async function groundEnter(reset) {
  if (!groundDefaults) groundDefaults = await (await fetch("/api/ground/defaults")).json();
  const d = groundDefaults;
  if (reset || !ground.rx) {
    ground.rx = { lat: d.lat, lon: d.lon, name: d.name };
    ground.water = null;
    ground.geoid = d.geoid_m;
    ground.result = null;
    document.getElementById("g_ground").value = d.ground_m;
    document.getElementById("g_ant").value = d.antenna_m;
    document.getElementById("g_water").value = d.water_m;
    document.getElementById("g_mask").value = 2;
    const day = nextMondayLocal();
    document.getElementById("g_from").value = `${day}T08:00`;
    document.getElementById("g_to").value = `${day}T12:00`;
  }
  paintGroundZone();
  paintGroundPlace();
  map.setView([ground.rx.lat + 0.003, ground.rx.lon - 0.002], 16);
}

async function groundClick(latlng) {
  let onLake = false;
  lakeLayer.eachLayer((layer) => {
    if (!onLake && layer instanceof L.Polygon && clickOnLayer(layer, latlng)) onLake = true;
  });
  if (onLake) {
    ground.water = { lat: latlng.lat, lon: latlng.lng };
  } else {
    ground.rx = { lat: latlng.lat, lon: latlng.lng, name: "Receiver" };
  }
  ground.result = null;
  paintGroundPlace();
  setStatus(onLake ? "Looking up the water surface height…" : "Looking up the ground height…");
  try {
    const d = await groundLookup(latlng.lat, latlng.lng);
    document.getElementById(onLake ? "g_water" : "g_ground").value = d.ground_m.toFixed(1);
    if (!onLake && d.geoid_m != null) ground.geoid = d.geoid_m;
    setStatus(onLake ? `Water surface ${d.ground_m.toFixed(1)} m from USGS lidar.` : `Ground ${d.ground_m.toFixed(1)} m from USGS lidar.`);
  } catch (e) {
    setStatus(`${e.message} Type the height in by hand.`, true);
  }
  computeGround();
}

async function computeGround() {
  if (!ground.rx) return;
  const gen = ++ground.gen;
  stopPlay();
  const btn = document.getElementById("go");
  btn.disabled = true;
  setStatus("Finding reflections on the lake…");
  try {
    const body = {
      lat: ground.rx.lat,
      lon: ground.rx.lon,
      ground_m: groundNum("g_ground"),
      antenna_m: groundNum("g_ant"),
      water_m: groundNum("g_water"),
      geoid_m: ground.geoid,
      mask: groundNum("g_mask"),
      start_local: document.getElementById("g_from").value,
      end_local: document.getElementById("g_to").value,
      tz: GROUND_TZ,
      step_s: 30,
      constellations: selectedConsts(),
    };
    const r = await fetch("/api/ground", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "Ground test failed.");
    if (gen !== ground.gen) return;
    showGround(data);
    setStatus("");
  } catch (e) {
    if (gen === ground.gen) setStatus(String(e.message || e), true);
  } finally {
    if (gen === ground.gen) btn.disabled = false;
  }
}

function showGround(res) {
  ground.result = res;
  ground.sky = res.sky || [];
  const t0 = new Date(res.start_utc).getTime();
  const t1 = new Date(res.end_utc).getTime();
  ground.times = [];
  for (let t = t0; t <= t1; t += res.step_s * 1000) ground.times.push(new Date(t).toISOString());
  ground.byTime = new Map();
  for (const p of res.points) {
    const k = Math.round((new Date(p.utc).getTime() - t0) / (res.step_s * 1000));
    if (!ground.byTime.has(k)) ground.byTime.set(k, []);
    ground.byTime.get(k).push(p);
  }
  paintGroundPlace();

  const sum = document.getElementById("summary");
  const zone = groundZone(res.start_utc);
  const rows = res.sats
    .map(
      (s) => `<tr data-sid="${escHtml(s.sid)}"><td style="color:${sidColor(s.sid)}">${escHtml(s.sid)}</td>
        <td>${groundClock(s.first_utc)}–${groundClock(s.last_utc)}</td><td>${s.minutes}</td>
        <td>${s.el_min}–${s.el_max}</td><td>${s.az_first}–${s.az_last}</td><td>${s.dist_min_m}–${s.dist_max_m}</td></tr>`
    )
    .join("");
  const day = groundClock(res.start_utc, { weekday: "short", month: "short", day: "numeric", hour: undefined, minute: undefined });
  sum.innerHTML = `
    <div class="sum-top"><div class="sum-mode">Ground test</div><div class="sum-when">${escHtml(day)} · ${escHtml(zone.long)} (${escHtml(zone.abbr)})</div></div>
    <div class="stat-grid">
      ${statCell("Antenna above water", res.receiver.height_above_water_m, "m")}
      ${statCell("Minutes with a reflection", res.minutes_with_reflection, "min")}
      ${statCell("Satellites", res.sats.length)}
    </div>
    <p>Reflections on <b>${escHtml(res.water.name)}</b> from ${groundClock(res.start_utc)} to ${groundClock(res.end_utc)} ${escHtml(zone.abbr)}. Every nearby lake is included.</p>
    ${res.sats.length ? `<table class="grid ground-table"><thead><tr><th>Sat</th><th>Time (${escHtml(zone.abbr)})</th><th>Min</th><th>El °</th><th>Az °</th><th>Out m</th></tr></thead><tbody>${rows}</tbody></table>` : `<p>No reflections land on nearby lakes in that window. Try a lower satellite limit, a higher spot, or a longer window.</p>`}`;
  sum.classList.remove("hidden");

  const slider = document.getElementById("slider");
  document.getElementById("sliderWrap").classList.remove("hidden");
  slider.min = 0;
  slider.max = ground.times.length - 1;
  slider.step = 1;
  const first = res.points.length ? Math.round((new Date(res.points[0].utc).getTime() - t0) / (res.step_s * 1000)) : 0;
  slider.value = first;
  slider.oninput = () => {
    stopPlay();
    renderGround(Number(slider.value));
  };
  document.getElementById("btnPlay").onclick = groundPlay;
  const bounds = L.geoJSON(res.water.geojson).getBounds();
  bounds.extend([res.receiver.lat, res.receiver.lon]);
  if (bounds.isValid()) map.fitBounds(bounds, { paddingTopLeft: [420, 40], paddingBottomRight: [40, 120] });
  renderGround(first);
}

function renderGround(i) {
  ground.idx = i;
  const res = ground.result;
  if (!res) return;
  const slider = document.getElementById("slider");
  if (document.activeElement !== slider) slider.value = String(i);
  const iso = ground.times[i];
  const now = ground.byTime.get(i) || [];
  const zone = groundZone(iso);
  document.getElementById("playElapsed").textContent = `${groundClock(iso)} ${zone.abbr}`;
  document.getElementById("playPhase").textContent = now.length ? `${now.length} on the water` : "no reflection";
  document.getElementById("playClock").innerHTML = `<em>${escHtml(groundClock(iso, { weekday: "short", hour: undefined, minute: undefined }))}</em>${escHtml(zone.abbr)}`;
  groundNowLayer.clearLayers();
  const h = res.receiver.height_above_water_m;
  const lam = 299792458 / 1176.45e6;
  for (const p of now) {
    const col = sidColor(p.sid);
    const s = Math.sin((Math.max(p.el, 0.5) * Math.PI) / 180);
    const across = Math.sqrt((lam * h) / s);
    const along = Math.sqrt((lam * h) / (s * s * s));
    L.polygon(fresnelRing(p.lat, p.lon, along, across, p.az, 48), { color: col, weight: 1.5, fillColor: col, fillOpacity: 0.25, interactive: false }).addTo(groundNowLayer);
    L.polyline([[res.receiver.lat, res.receiver.lon], [p.lat, p.lon]], { color: col, weight: 1.2, opacity: 0.55, dashArray: "3 5", interactive: false }).addTo(groundNowLayer);
    L.circleMarker([p.lat, p.lon], { radius: 5, color: "#fff", weight: 2, fillColor: col, fillOpacity: 1, interactive: false }).addTo(groundNowLayer);
    L.marker([p.lat, p.lon], {
      interactive: false,
      icon: L.divIcon({
        className: "",
        iconSize: null,
        iconAnchor: [-12, 11],
        html: `<div class="spec-tag" style="--c:${col}"><b>${escHtml(p.sid)}</b><span>${p.el.toFixed(1)}° · ${Math.round(p.dist_m)} m</span></div>`,
      }),
    }).addTo(groundNowLayer);
  }
  const live = new Set(now.map((p) => p.sid));
  document.querySelectorAll(".ground-table tbody tr").forEach((tr) => tr.classList.toggle("now", live.has(tr.dataset.sid)));
  if (ground.sky.length) {
    const tSec = i * res.step_s;
    let j = 0;
    while (j < ground.sky.length - 2 && ground.sky[j + 1].t <= tSec) j++;
    const a = ground.sky[j];
    const b = ground.sky[Math.min(j + 1, ground.sky.length - 1)];
    const u = b.t > a.t ? Math.min(1, Math.max(0, (tSec - a.t) / (b.t - a.t))) : 0;
    const used = new Set(res.sats.map((s) => s.sid));
    const rows = lerpSats(a.sats, b.sats, u).map((r) => ({ ...r, used: used.has(r.sid) }));
    paintSats(rows, live);
  }
}

function groundPlay() {
  if (playing) {
    stopPlay();
    return;
  }
  if (!ground.result) return;
  playing = true;
  const btn = document.getElementById("btnPlay");
  btn.classList.add("playing");
  btn.setAttribute("aria-label", "Pause");
  if (ground.idx >= ground.times.length - 1) ground.idx = 0;
  let pos = ground.idx;
  let last = performance.now();
  const tick = (now) => {
    if (!playing) return;
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    pos += (dt * playRate * 30) / ground.result.step_s;
    if (pos >= ground.times.length - 1) {
      renderGround(ground.times.length - 1);
      stopPlay();
      return;
    }
    renderGround(Math.floor(pos));
    playTimer = requestAnimationFrame(tick);
  };
  playTimer = requestAnimationFrame(tick);
}

document.getElementById("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (missionMode === "ground") computeGround();
  else computePlan();
});

document.getElementById("modeClick").onclick = () => setMode("click");
document.getElementById("modeGround").onclick = () => setMode("ground");
document.getElementById("g_from").addEventListener("change", paintGroundZone);
document.getElementById("modeAreas").onclick = () => setMode("areas");
document.getElementById("modeSurvey").onclick = () => setMode("survey");
document.getElementById("btnUndoWp").onclick = () => {
  clickWps.pop();
  paintClickWps();
};
document.getElementById("btnClearWp").onclick = () => {
  clickWps = [];
  paintClickWps();
};
document.getElementById("btnClearSurvey").onclick = () => {
  surveyStart = null;
  surveyEnd = null;
  surveySelLayer.clearLayers();
  surveyPickLayer.clearLayers();
  paintSurveyPicks();
};

loadDefaults().catch((e) => setStatus(String(e), true));

function refreshWrapLayers() {
  const k = wrapOffsets().join(",");
  const wrapChanged = k !== lastWrapKey;
  lastWrapKey = k;
  syncSatRays();
  if (wrapChanged) {
    droneParts = [];
    if (padMarker) syncPadWrap();
    if (lastDrone) setDrone(lastDrone.lat, lastDrone.lon, lastDrone.hdg);
    setMissionPath(lastPathPts);
    paintClickWps();
  }
  if (lastSatRows.length) paintSats(lastSatRows, lastSatLive);
}
map.on("moveend", refreshWrapLayers);
map.on("zoomend", refreshWrapLayers);

(function initChrome() {
  const sideBtn = document.getElementById("sidebarToggle");
  try {
    if (localStorage.getItem("heron.sidebar") === "0") document.body.classList.add("sidebar-collapsed");
  } catch (_) {}
  const syncSidebarButton = () => {
    const collapsed = document.body.classList.contains("sidebar-collapsed");
    sideBtn.setAttribute("aria-expanded", String(!collapsed));
    sideBtn.setAttribute("aria-label", collapsed ? "Show mission planner" : "Hide mission planner");
  };
  syncSidebarButton();
  sideBtn.onclick = () => {
    const collapsed = document.body.classList.toggle("sidebar-collapsed");
    try {
      localStorage.setItem("heron.sidebar", collapsed ? "0" : "1");
    } catch (_) {}
    syncSidebarButton();
  };
  L.DomEvent.disableClickPropagation(document.getElementById("sidebar"));
  L.DomEvent.disableClickPropagation(sideBtn);
})();

document.getElementById("start").addEventListener("change", scheduleSky);
document.getElementById("mask").addEventListener("change", scheduleSky);
document.getElementById("h_agl").addEventListener("change", scheduleSky);
["cG", "cE", "cC"].forEach((id) => {
  document.getElementById(id).addEventListener("change", scheduleSky);
});

document.getElementById("lyrLake").onchange = syncBounceLayers;
document.getElementById("lyrLand").onchange = syncBounceLayers;
document.getElementById("prnToggles").addEventListener("change", syncBounceLayers);
document.getElementById("prnAll").onclick = () => {
  document.querySelectorAll("#prnToggles input").forEach((el) => {
    el.checked = true;
  });
  syncBounceLayers();
};
document.getElementById("prnNone").onclick = () => {
  document.querySelectorAll("#prnToggles input").forEach((el) => {
    el.checked = false;
  });
  syncBounceLayers();
};
function setPlaySpeedOpen(open) {
  const box = document.getElementById("playRates");
  box.classList.toggle("open", open);
  document.getElementById("playSpeedMenu").classList.toggle("hidden", !open);
  document.getElementById("playSpeedBtn").setAttribute("aria-expanded", String(open));
}
document.getElementById("playSpeedBtn").onclick = (ev) => {
  ev.stopPropagation();
  setPlaySpeedOpen(!document.getElementById("playRates").classList.contains("open"));
};
document.getElementById("playSpeedMenu").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-rate]");
  if (!btn) return;
  playRate = Number(btn.dataset.rate) || 20;
  document.querySelectorAll("#playSpeedMenu button").forEach((el) => {
    const on = el === btn;
    el.classList.toggle("on", on);
    el.setAttribute("aria-selected", String(on));
  });
  document.getElementById("playSpeedVal").textContent = `${playRate}×`;
  setPlaySpeedOpen(false);
});
document.addEventListener("click", (ev) => {
  const box = document.getElementById("playRates");
  if (!box || box.contains(ev.target)) return;
  setPlaySpeedOpen(false);
});

(function almanacUi() {
  const box = document.getElementById("almBox");
  const list = document.getElementById("almList");
  const note = document.getElementById("almNote");
  const fileScrim = document.getElementById("fileScrim");
  let items = [];
  const checks = {};
  const bandClass = { "gps-yuma": "g", "galileo-xml": "e", "beidou-tle": "c" };

  function fmtBytes(n) {
    if (!n) return "0 B";
    if (n < 1024) return `${n} B`;
    if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
    return `${(n / 1048576).toFixed(1)} MB`;
  }
  function ageTxt(h) {
    if (h == null) return "not on disk";
    if (h < 1) return "<1 h ago";
    if (h < 48) return `${Math.round(h)} h ago`;
    return `${(h / 24).toFixed(1)} d ago`;
  }
  function statusLine(it) {
    const c = checks[it.id];
    if (!c || !c.state) return "";
    if (c.state === "checking") return c.message || "checking…";
    if (c.state === "current") return "already current";
    if (c.state === "updated") return "pulled new copy";
    if (c.state === "stale") return c.message || "newer copy available";
    if (c.state === "missing") return "not on disk";
    if (c.state === "error") return c.message || "check failed";
    return c.message || "";
  }
  function render() {
    list.innerHTML = items
      .map((it) => {
        const st = (checks[it.id] && checks[it.id].state) || "";
        const msg = statusLine(it);
        return `<div class="alm-row ${bandClass[it.id] || ""}" data-id="${it.id}">
          <div class="alm-row-h">
            <b>${it.used || it.name}</b>
            <span class="alm-n">${it.sats || 0}</span>
          </div>
          <div class="alm-src">${it.agency} · ${it.name}</div>
          <div class="alm-file">${it.file || "—"} · ${fmtBytes(it.bytes)} · ${ageTxt(it.age_hours)}</div>
          ${msg ? `<div class="alm-msg ${st}">${msg}</div>` : ""}
          <div class="alm-links">
            <button type="button" data-act="view" ${it.missing ? "disabled" : ""}>view</button>
            <button type="button" data-act="dl" ${it.missing ? "disabled" : ""}>download</button>
            <button type="button" data-act="refresh">${it.missing ? "fetch" : "update"}</button>
            <a href="${it.page}" target="_blank" rel="noopener">source</a>
          </div>
        </div>`;
      })
      .join("");
  }
  async function loadList() {
    const r = await fetch("/api/almanacs");
    const d = await r.json();
    items = d.items || [];
    render();
  }
  async function checkOne(id) {
    checks[id] = { state: "checking" };
    render();
    try {
      const r = await fetch("/api/almanacs/" + encodeURIComponent(id) + "/check", { method: "POST" });
      checks[id] = await r.json();
    } catch (e) {
      checks[id] = { state: "error", message: String(e) };
    }
    render();
  }
  async function refreshOne(id) {
    checks[id] = { state: "checking", message: "checking remote…" };
    render();
    try {
      const r = await fetch("/api/almanacs/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
      const d = await r.json();
      checks[id] = d;
      await loadList();
      if (d.state === "updated") {
        if (lastPlan) setStatus("Orbit file updated. Compute again to use it.");
        else fetchSky();
      } else if (d.state === "current") {
        setStatus("Already current.");
      } else if (!d.ok) {
        setStatus(d.message || "Update failed.", true);
      }
    } catch (e) {
      checks[id] = { state: "error", message: String(e) };
      render();
      setStatus(String(e), true);
    }
  }
  async function viewOne(id) {
    const title = document.getElementById("fileTitle");
    const body = document.getElementById("fileBody");
    const dl = document.getElementById("fileDownload");
    title.textContent = "…";
    body.textContent = "";
    dl.href = "/api/almanacs/" + encodeURIComponent(id) + "/file";
    fileScrim.classList.remove("hidden");
    const r = await fetch("/api/almanacs/" + encodeURIComponent(id) + "/preview");
    const d = await r.json();
    if (!r.ok) {
      title.textContent = "missing";
      body.textContent = d.error || "Could not open file.";
      return;
    }
    title.textContent = d.file + (d.truncated ? "  (cut)" : "");
    body.textContent = d.text || "";
  }

  document.getElementById("btnAlmanac").onclick = () => {
    const hide = box.classList.toggle("hidden");
    document.getElementById("btnAlmanac").setAttribute("aria-expanded", String(!hide));
    if (!hide) loadList();
  };
  document.getElementById("fileClose").onclick = () => fileScrim.classList.add("hidden");
  fileScrim.addEventListener("click", (e) => {
    if (e.target === fileScrim) fileScrim.classList.add("hidden");
  });
  list.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const row = btn.closest("[data-id]");
    const id = row && row.getAttribute("data-id");
    if (!id) return;
    const act = btn.getAttribute("data-act");
    if (act === "view") viewOne(id);
    if (act === "dl") window.location = "/api/almanacs/" + encodeURIComponent(id) + "/file";
    if (act === "refresh") refreshOne(id);
  });
  document.getElementById("almCheck").onclick = async () => {
    note.textContent = "checking…";
    items.forEach((it) => {
      checks[it.id] = { state: "checking", message: "checking remote…" };
    });
    render();
    try {
      const r = await fetch("/api/almanacs/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: true }),
      });
      const d = await r.json();
      for (const row of d.results || []) checks[row.id] = row;
      await loadList();
      if (d.updated) {
        note.textContent = `updated ${d.updated}`;
        if (lastPlan) setStatus("Orbit files updated. Compute again to use them.");
        else fetchSky();
      } else {
        note.textContent = d.ok ? "already current" : d.message || "check failed";
      }
    } catch (e) {
      note.textContent = String(e);
    }
  };
  L.DomEvent.disableClickPropagation(document.getElementById("fileSheet"));
})();

// Any planner edit invalidates the previous export and simulation.
for (const id of ["survey_style", "coverage", "sweep_bearing", "max_leg_m", "roi_mode", "antenna_offset_deg", "tile_m", "max_wp", "target_el", "speed", "loiter", "h_agl"]) {
  const el = document.getElementById(id);
  el.addEventListener("input", updateSweepHint);
  el.addEventListener("change", () => {
    updateSweepHint();
    if (!lastPlan) return;
    resetMission({ keepSelection: true });
    setStatus("Settings changed. Build mission to update the route and export.");
  });
}
updateSweepHint();
