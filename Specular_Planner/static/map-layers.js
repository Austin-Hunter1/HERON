/* Map layers: imagery and segmented water from earlier flights, toggled from a panel
   next to the zoom buttons. Data comes from data/overlays/ (scripts/import_overlay.py). */
(function () {
  "use strict";
  if (typeof map === "undefined") return;

  const KEY = "heron.mapLayers";
  const state = load();
  const layers = {};          // id -> { ortho, water }
  let items = [];

  map.createPane("flightOrtho").style.zIndex = 250;      // above base tiles, under plan + aircraft
  map.createPane("flightTracks").style.zIndex = 380;
  map.getPane("flightOrtho").style.pointerEvents = "none";
  map.getPane("flightTracks").style.pointerEvents = "none";
  const trackRenderer = L.svg({ pane: "flightTracks" });

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; }
  }
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* private mode */ }
  }
  function prefs(id) {
    if (!state[id] || state[id].__proto__ !== Object.prototype || !("ortho" in state[id])) {
      state[id] = Object.assign({ ortho: false, opacity: 0.85, water: false }, state[id] || {});
    }
    return state[id];
  }

  const icon = {
    layers: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/></svg>',
    close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    fit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
  };

  // ---------------------------------------------------------------- control + panel
  const Control = L.Control.extend({
    options: { position: "bottomright" },
    onAdd: function () {
      const box = L.DomUtil.create("div", "ml-control");
      box.innerHTML =
        '<div class="leaflet-bar"><a href="#" role="button" id="mlOpen" title="Map layers" aria-label="Map layers" aria-expanded="false" aria-controls="mlPanel">' + icon.layers + "</a></div>" +
        '<section id="mlPanel" class="ml-panel" hidden aria-label="Map layers">' +
        '<header><b>Map layers</b><button type="button" id="mlClose" class="ml-x" aria-label="Close">' + icon.close + "</button></header>" +
        '<div id="mlBody" class="ml-body"><p class="ml-empty">Loading…</p></div>' +
        "</section>";
      L.DomEvent.disableClickPropagation(box);
      L.DomEvent.disableScrollPropagation(box);
      return box;
    },
  });
  new Control().addTo(map);

  const openBtn = document.getElementById("mlOpen");
  const panel = document.getElementById("mlPanel");
  const body = document.getElementById("mlBody");

  function setOpen(open) {
    panel.hidden = !open;
    openBtn.setAttribute("aria-expanded", open ? "true" : "false");
    openBtn.classList.toggle("on", open);
  }
  openBtn.addEventListener("click", (e) => { e.preventDefault(); setOpen(panel.hidden); });
  document.getElementById("mlClose").addEventListener("click", () => setOpen(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) setOpen(false); });
  map.on("click", () => setOpen(false));

  // ---------------------------------------------------------------- layers
  function fmtDate(iso) {
    const d = new Date(iso + "T12:00:00");
    return isNaN(d) ? iso : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }

  function ensure(item) {
    const L_ = (layers[item.id] = layers[item.id] || {});
    if (item.ortho && !L_.ortho) {
      L_.ortho = L.imageOverlay(item.ortho.url, item.ortho.bounds, {
        pane: "flightOrtho", interactive: false, className: "ml-ortho",
      });
    }
    if (item.water && !L_.water) {
      L_.water = L.geoJSON(null, {
        pane: "flightTracks", renderer: trackRenderer, interactive: false,
        style: { color: "#ff8a1f", weight: 2.5, opacity: 1, fillColor: "#ff8a1f", fillOpacity: 0.18, lineJoin: "round" },
      });
      fetch(item.water.url).then((r) => r.json()).then((g) => L_.water.addData(g)).catch(() => {});
    }
    return L_;
  }

  function apply(item) {
    const p = prefs(item.id);
    const L_ = ensure(item);
    if (L_.ortho) {
      L_.ortho.setOpacity(p.opacity);
      if (p.ortho) L_.ortho.addTo(map); else L_.ortho.remove();
    }
    if (L_.water) {
      if (p.water) L_.water.addTo(map); else L_.water.remove();
    }
    openBtn.classList.toggle("active", items.some((it) => prefs(it.id).ortho || prefs(it.id).water));
  }

  function row(id, label, sub, checked) {
    return (
      '<label class="ml-row"><span><b>' + label + "</b>" + (sub ? "<small>" + sub + "</small>" : "") + "</span>" +
      '<input type="checkbox" role="switch" class="ml-switch" data-k="' + id + '"' + (checked ? " checked" : "") + " /></label>"
    );
  }

  function render() {
    if (!items.length) {
      body.innerHTML = '<p class="ml-empty">No earlier flights yet. Add one with <code>scripts/import_overlay.py</code>.</p>';
      return;
    }
    body.innerHTML = items.map((it) => {
      const p = prefs(it.id);
      const gsd = it.ortho && it.ortho.gsd_m ? it.ortho.gsd_m.toFixed(2) + " m/px" : "";
      const pct = Math.round(p.opacity * 100);
      return (
        '<div class="ml-flight" data-id="' + it.id + '">' +
        '<div class="ml-head"><span><b>' + esc(it.name) + "</b><small>" + fmtDate(it.date) + "</small></span>" +
        '<button type="button" class="ml-fit" title="Zoom to this flight" aria-label="Zoom to this flight">' + icon.fit + "</button></div>" +
        (it.ortho ? row("ortho", "Orthomosaic", gsd, p.ortho) +
          '<div class="ml-opacity' + (p.ortho ? "" : " off") + '"><span>Opacity</span>' +
          '<input type="range" min="10" max="100" step="5" value="' + pct + '" aria-label="Orthomosaic opacity" />' +
          "<output>" + pct + "%</output></div>" : "") +
        (it.water ? row("water", '<i class="ml-swatch"></i>Water',
          it.water.m2 ? (it.water.m2 / 1e4).toFixed(1) + " ha segmented from the flight" : "Segmented from the flight", p.water) : "") +
        "</div>"
      );
    }).join("");

    body.querySelectorAll(".ml-flight").forEach((el) => {
      const it = items.find((x) => x.id === el.dataset.id);
      const p = prefs(it.id);
      el.querySelectorAll(".ml-switch").forEach((sw) => sw.addEventListener("change", () => {
        p[sw.dataset.k] = sw.checked;
        if (sw.dataset.k === "ortho") el.querySelector(".ml-opacity").classList.toggle("off", !sw.checked);
        save(); apply(it);
      }));
      const range = el.querySelector(".ml-opacity input");
      if (range) range.addEventListener("input", () => {
        p.opacity = range.value / 100;
        range.nextElementSibling.textContent = range.value + "%";
        if (layers[it.id] && layers[it.id].ortho) layers[it.id].ortho.setOpacity(p.opacity);
        save();
      });
      el.querySelector(".ml-fit").addEventListener("click", () => {
        const b = it.ortho ? L.latLngBounds(it.ortho.bounds) : layers[it.id].water && layers[it.id].water.getBounds();
        if (b && b.isValid()) map.flyToBounds(b, { padding: [40, 40], maxZoom: 18, duration: 0.6 });
      });
    });
  }

  window.heronShowFlightWater = function(id) {
    const item = items.find(it => it.id === id);
    if (!item) return;
    const p = prefs(id); p.water = true; p.ortho = true;
    apply(item); render();
    if (item.ortho) map.fitBounds(L.latLngBounds(item.ortho.bounds), {padding: [40,40], maxZoom: 17});
  };
  // Plan > Water can pick a flight's segmented water while that layer is shown.
  window.heronFlightWaterAt = function (latlng) {
    let found = null;
    items.forEach((it) => {
      const w = layers[it.id] && layers[it.id].water;
      if (found || !w || !map.hasLayer(w)) return;
      w.eachLayer((layer) => {
        if (!found && typeof clickOnLayer === "function" && clickOnLayer(layer, latlng)) found = { layer, id: it.id };
      });
    });
    return found;
  };

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  fetch("/api/overlays").then((r) => r.json()).then((d) => {
    items = d.overlays || [];
    render();
    items.forEach(apply);
  }).catch(() => { body.innerHTML = '<p class="ml-empty">Couldn\'t load map layers.</p>'; });
})();
