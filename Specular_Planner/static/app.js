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
let targets = [];
let drawing = false;
let draft = [];
let playing = false;
let playTimer = null;
let playT = 0;
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

function lngVisible(lng) {
  try {
    if (map.getZoom() <= 5) return true;
    const b = map.getBounds();
    const span = Math.max(1, b.getEast() - b.getWest());
    const pad = Math.max(12, span * 0.4);
    return lng >= b.getWest() - pad && lng <= b.getEast() + pad;
  } catch (_) {
    return true;
  }
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
    seen.add(row.sid);
    const isLive = live.has(row.sid);
    const lons = offs.map((off) => row.lon + off).filter((lon) => lngVisible(lon));
    const useLons = lons.length ? lons : [row.lon];
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
        [row.lat, row.lon],
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
  const box = document.getElementById("prnToggles");
  box.innerHTML = prns
    .map((p) => {
      const n = bouncePrn[p].nLake + bouncePrn[p].nLand;
      return `<label class="prn-chip" style="border-color:${sidColor(p)};color:${sidColor(p)}">
        <input type="checkbox" data-prn="${p}" checked /> ${p} <span>(${n})</span>
      </label>`;
    })
    .join("");
  document.getElementById("layers").classList.remove("hidden");
  syncBounceLayers();
}

function aimLockText(s) {
  if (!s) return "";
  if (s.live_offset) {
    if (!s.aim_sid) return "no sat above mask — grid not offset";
    const last = s.last_sid && s.last_sid !== s.aim_sid
      ? `  →  ${s.last_sid} el ${Math.round(s.last_el)}°`
      : `  →  el ${Math.round(s.last_el != null ? s.last_el : s.aim_el)}°`;
    const n = s.n_cells != null ? `${s.n_cells} tiles` : "";
    const km = s.stretch_m != null ? `${Math.round(s.stretch_m)} m stretch` : "";
    const extra = [n, km].filter(Boolean).join(" · ");
    return `LIVE OFFSET  ${s.aim_sid} el ${Math.round(s.aim_el)}°${last}\naz/el recomputed at each WP${extra ? "\n" + extra : ""}`;
  }
  if (!s.aim_sid) return "no sat above mask — grid not offset";
  const when = (s.aim_utc || "").replace("T", " ").slice(0, 16);
  return `GRID LOCK  ${s.aim_sid}  el ${Math.round(s.aim_el)}°  az ${Math.round(s.aim_az)}°\nwhole lake uses this sat at ${when} UTC`;
}

function paintSurveyLock(plan) {
  splashLayer.clearLayers();
  surveyRayLayer.clearLayers();
  const lock = document.getElementById("tileLock");
  const m = plan.meta || {};
  const s = m.survey || {};
  if (m.mode !== "survey" && m.mode !== "areas") {
    lock.classList.add("hidden");
    lock.textContent = "";
    syncSatRays();
    return;
  }
  lock.classList.remove("hidden");
  lock.textContent = aimLockText(s);
  const hovers = plan.hovers || [];
  const step = hovers.length > 80 ? Math.ceil(hovers.length / 80) : 1;
  hovers.forEach((h, i) => {
    if (h.splash_lat == null || h.splash_lon == null) return;
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
  const c = s.centroid;
  if (c && s.aim_sid) {
    const lab = s.live_offset
      ? `LIVE ${s.aim_sid} @ ${Math.round(s.aim_el)}°`
      : `GRID = ${s.aim_sid} @ ${Math.round(s.aim_el)}°`;
    L.marker([c.lat, c.lon], {
      icon: L.divIcon({
        className: "",
        html: `<div class="tile-lock-lab">${lab}</div>`,
        iconSize: [180, 20],
        iconAnchor: [90, 10],
      }),
      interactive: false,
    }).addTo(splashLayer);
  }
  syncSatRays();
}

function setStatus(msg, err) {
  const el = document.getElementById("status");
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
      fillOpacity: 0.18,
    })
      .bindTooltip(t.name || `area ${i + 1}`, { sticky: true })
      .addTo(targetLayer);
  });
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

function finishDraft() {
  if (draft.length < 2) return;
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
  setStatus(`${targets.length} measure area(s). Tiling the box…`);
  computePlan();
}

document.getElementById("btnDraw").onclick = () => {
  startAreaDraw();
  setStatus("Click two corners for a box, or more for a polygon.");
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
  setStatus("Areas cleared. Click two corners for a box.");
};

map.on("click", (e) => {
  if (missionMode === "survey") {
    pickWater(e.latlng);
    return;
  }
  if (missionMode === "click") {
    L.DomEvent.stop(e);
    clickWps.push({ lat: e.latlng.lat, lon: e.latlng.lng });
    paintClickWps();
    setStatus(`${clickWps.length} waypoint(s).`);
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
  if (missionMode !== "areas" || !drawing) return;
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

function setMode(m) {
  missionMode = m;
  const modeStates = [
    ["modeClick", m === "click"],
    ["modeAreas", m === "areas"],
    ["modeSurvey", m === "survey"],
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
    setStatus("Choose a river stretch or select a lake on the map.");
  } else if (m === "areas") {
    startAreaDraw();
    setStatus("Draw the survey boundary on the map.");
  } else setStatus("Place your waypoints on the map, then build the mission.");
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
      setStatus("Start set. Click where the river stretch should end.");
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
  if (missionMode === "areas" && !targets.length) {
    setStatus("Click two corners for a box, then Finish.", true);
    return;
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
      if (missionMode === "survey") {
        body.click_lat = surveyStart.lat;
        body.click_lon = surveyStart.lon;
        if (surveyEnd) {
          body.click_lat2 = surveyEnd.lat;
          body.click_lon2 = surveyEnd.lon;
        }
      }
      body.max_wp = Number(document.getElementById("max_wp").value);
      if (tileVal) body.tile_m = Number(tileVal);
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
    showPlan(data);
  } catch (e) {
    setStatus(String(e), true);
  } finally {
    btn.disabled = false;
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
  const phase = u < 0.5 ? a.phase : b.phase;
  const lat = lerp(a.drone.lat, b.drone.lat, u);
  const lon = lerpLon(a.drone.lon, b.drone.lon, u);
  const spd = lerp(a.drone.speed_mps || 0, b.drone.speed_mps || 0, u);
  const satRows = lerpSats(satsWithData(frames, i, -1), satsWithData(frames, i + 1, 1), u);
  return {
    t: tSec,
    iso: a.iso,
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

function pushTrail(prn, lat, lon) {
  if (!trails[prn]) trails[prn] = [];
  const arr = trails[prn];
  const last = arr[arr.length - 1];
  if (last && Math.abs(last[0] - lat) < 1e-6 && Math.abs(last[1] - lon) < 1e-6) return;
  arr.push([lat, lon]);
  if (arr.length > 18) arr.shift();
}

function drawTrails() {
  trailLayer.clearLayers();
  for (const prn of Object.keys(trails)) {
    const pts = trails[prn];
    if (pts.length < 2) continue;
    addShortPolyline(trailLayer, pts, { color: "#7ee0ff", weight: 2, opacity: 0.45, interactive: false });
  }
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
  document.getElementById("sliderLabel").textContent =
    `+${Math.round(f.t)}s  ${phaseLabel(f.phase)}  ${f.iso.replace("T", " ").slice(0, 19)} UTC`;

  const want = headingToWp(plan, f.drone.lat, f.drone.lon, f.phase, f.drone.hdg);
  const hdg = smoothHdg(want);
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
    const picked = !!(s.in_target || s.aimed);
    const wet = !!s.on_water;
    const hit = picked || wet;
    const col = hit ? "#ffe66a" : "#6b7584";
    upsertSpec(s, f.drone, hit, col);
    pushTrail(s.prn, s.lat, s.lon);
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
    drawTrails();
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
    playT += dt * 20;
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
  setStatus("Ready.");
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
  const notes = [];
  if (m.mode === "survey" || (m.mode === "areas" && m.survey)) {
    const s = m.survey || {};
    if (s.name) notes.push(escHtml(s.name));
    if (s.aim_sid) {
      notes.push(
        `Grid lock ${escHtml(s.aim_sid)} · el ${s.aim_el ?? "—"}° · az ${s.aim_az ?? "—"}°`
      );
    }
    if (s.tile_m != null) notes.push(`Tile ${s.tile_m} m`);
    notes.push(
      s.coarsened
        ? `Opened spacing so ≤${s.max_wp} WP`
        : "Tile ≤ first Fresnel so patches overlap"
    );
  } else if (m.mode !== "click" && m.mode !== "manual") {
    if (m.hold_s) notes.push(`Pad hold ${Math.round(m.hold_s)} s`);
    if (m.almanac_age_days != null) notes.push(`Almanac ${m.almanac_age_days} d`);
    const hits = Object.entries(m.target_hits || {})
      .map(([k, v]) => `${k} ${v}`)
      .join(" · ");
    if (hits) notes.push(`Splash in area: ${escHtml(hits)}`);
    if (m.fallback_hovers && m.fallback_hovers.length) {
      notes.push(`No sat lock: ${escHtml(m.fallback_hovers.join(", "))}`);
    } else {
      notes.push("WPs ordered by bounce in the box");
    }
  }
  sum.innerHTML = `
    <div class="sum-top">
      <div class="sum-mode">${mode}</div>
      <div class="sum-when">${escHtml(when)} UTC</div>
    </div>
    <div class="stat-grid">
      ${statCell("Waypoints", m.n_hovers ?? "—")}
      ${statCell("Duration", mins, "min")}
      ${statCell("Distance", distTxt, distUnit)}
      ${statCell("Speed", m.speed_mps ?? "—", "m/s")}
      ${statCell("Hover", m.loiter_s ?? "—", "s / WP")}
      ${statCell("L5 on water", prns.length)}
    </div>
    ${notes.length ? `<div class="sum-note">${notes.join("<br>")}</div>` : ""}
    <div class="sum-sats">
      <div class="sum-sats-label">L5 on water</div>
      <div class="sum-chips">${satChips(prns)}</div>
    </div>`;
  sum.classList.remove("hidden");
}

function showPlan(plan) {
  lastPlan = plan;
  stopPlay();
  playT = 0;
  dispHdg = null;
  lastYawAt = 0;
  for (const k of Object.keys(trails)) delete trails[k];
  trailLayer.clearLayers();
  clearSpecParts();

  const m = plan.meta;
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
            (h.t_in_window != null ? `  geometry +${h.t_in_window}s` : ""),
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
  const miss = (plan.exports && plan.exports.mission) || {};
  const fname = (plan.exports && plan.exports.filename) || "HERON.waypoints";
  const ok = miss.ok !== false;
  const warn = (miss.warnings || []).join(" · ");
  const err = (miss.errors || []).join(" · ");
  const n = miss.n_nav != null ? `${miss.n_nav} WPs` : "";
  ex.innerHTML = `
    <div class="export-k">ArduPilot / Mission Planner</div>
    <a class="export-btn" href="${plan.exports.waypoints}" download="${escHtml(fname)}">Download .waypoints</a>
    <a href="${plan.exports.runcard}" download>Run card</a>
    <div class="export-note ${ok ? "ok" : "bad"}">${ok ? `Validated · ${n}` : "File failed checks"}</div>
    ${err ? `<div class="export-note bad">${escHtml(err)}</div>` : ""}
    ${warn ? `<div class="export-note">${escHtml(warn)}</div>` : ""}
    <div class="export-hint">Copter → Flight Plan → Load WP File. Relative alt. WP_YAW_BEHAVIOR = Face Next Waypoint.</div>`;
  setStatus(ok ? "Mission ready. File is Mission Planner Copter format." : "Mission ready, but the WP file failed checks.", !ok);
}

document.getElementById("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  computePlan();
});

document.getElementById("modeClick").onclick = () => setMode("click");
document.getElementById("modeAreas").onclick = () => setMode("areas");
document.getElementById("modeSurvey").onclick = () => setMode("survey");
document.getElementById("btnUndoWp").onclick = () => {
  clickWps.pop();
  paintClickWps();
};
document.getElementById("btnClearWp").onclick = () => {
  clickWps = [];
  paintClickWps();
  setStatus("Waypoints cleared.");
};
document.getElementById("btnClearSurvey").onclick = () => {
  surveyStart = null;
  surveyEnd = null;
  surveySelLayer.clearLayers();
  surveyPickLayer.clearLayers();
  paintSurveyPicks();
  setStatus("Stretch cleared. River: click start, then end.");
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
