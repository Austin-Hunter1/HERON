/* Configurable science phases; uses the planner's map, playback and export. */
window.HeronScience = (() => {
  const $ = (id) => document.getElementById(id);
  const layer = L.layerGroup().addTo(map);
  const hover = (name = "Hover observation", satellites = "G09, E05, C12") =>
    ({type: "hover", name, satellites, duration_s: 120, points: []});
  const passes = () => ({type: "passes", name: "Shoreline passes", satellites: "G09, E05, C12",
    count: 3, speed_mps: 3, spacing_m: 25, water_m: 200, land_m: 100, points: []});
  let phases = [hover("Group observation"), hover("Second observation", "E23"), passes()];
  let picking = 0;
  let visible = false;
  let entered = false;
  const ids = (s) => Array.isArray(s) ? s.join(", ") : String(s);

  function waterSource() {
    const chosen = $("scienceWater").value;
    if (chosen === "__mapped__") return "";
    if (chosen) return chosen;
    // The visible segmented water is what map clicks refer to. Prefer pass picks.
    const ordered = [...phases.filter(p => p.type === "passes"), ...phases.filter(p => p.type !== "passes")];
    for (const phase of ordered) for (const point of phase.points) {
      const hit = window.heronFlightWaterAt?.({lat: point.lat, lng: point.lon});
      if (hit) {
        if ([...$("scienceWater").options].some(o => o.value === hit.id)) $("scienceWater").value = hit.id;
        return hit.id;
      }
    }
    return "";
  }
  function invalidate() {
    resetMission({keepSelection: true});
    setStatus("Science setup changed. Build mission to update the preview and export.");
  }
  function field(i, key, label, extra = "") {
    const numeric = !["name", "satellites"].includes(key);
    const value = key === "satellites" ? ids(phases[i][key]) : (phases[i][key] ?? "");
    return `<label class="field"><span>${label}</span><input data-phase="${i}" data-key="${key}" type="${numeric ? "number" : "text"}" value="${escHtml(String(value))}" ${extra} /></label>`;
  }
  function render() {
    $("sciencePhases").innerHTML = phases.map((p, i) => `<section class="science-phase ${picking === i ? "picking" : ""}">
      <div class="science-phase-head"><b>${i+1}. ${p.type === "hover" ? "Hover" : "Passes"}</b>
        <div><button type="button" data-action="up" data-phase="${i}" ${i === 0 ? "disabled" : ""} aria-label="Move phase ${i+1} up">↑</button>
        <button type="button" data-action="down" data-phase="${i}" ${i === phases.length-1 ? "disabled" : ""} aria-label="Move phase ${i+1} down">↓</button>
        <button type="button" data-action="remove" data-phase="${i}" aria-label="Remove phase ${i+1}">Remove</button></div></div>
      <label class="science-record"><input type="checkbox" data-phase="${i}" data-key="record" ${p.record === false ? "" : "checked"} /> Record this phase</label>
      ${field(i, "name", "Name", 'maxlength="80"')}
      ${field(i, "satellites", "Satellites · primary first", 'placeholder="G09, E05, C12"')}
      ${p.type === "hover" ? field(i, "duration_s", "Observe for (seconds)", 'min="1" max="1200" step="1"') :
        `<div class="field-grid">${field(i, "count", "Passes", 'min="1" max="20" step="1"')}
        ${field(i, "speed_mps", "Pass speed (m/s)", 'min="1" max="25" step="0.5"')}
        ${field(i, "water_m", "Water-side length (m)", 'min="5" max="1500" step="5"')}${field(i, "land_m", "Land-side length (m)", 'min="5" max="500" step="5"')}${field(i, "spacing_m", "Spacing (m)", 'min="0" max="300" step="1"')}</div>`}
      <p class="science-point-note">${p.points.length ? p.points.map((pt,j) => `${p.type === "hover" ? "Reflection" : j === 0 ? "Start" : "End"}: ${pt.lat.toFixed(5)}, ${pt.lon.toFixed(5)}`).join("<br>") : "No map points selected"}</p>
      <button type="button" data-action="pick" data-phase="${i}" class="secondary-button">${picking === i ? "Selecting on map…" : p.points.length ? "Replace map points" : "Select on map"}</button>
      <small>${p.type === "hover" ? "Click a reflection point on water. The drone holds at the calculated position." : "Click inside the selected water outline, then on land to set the crossing and direction. Water-side and land-side lengths extend the passes from that crossing. Spacing 0 repeats the same line."}</small>
    </section>`).join("");
    draw();
  }
  function draw() {
    layer.clearLayers();
    if (!visible) return;
    phases.forEach((p, i) => {
      p.points.forEach((pt, j) => L.circleMarker([pt.lat, pt.lon], {radius: 7, color: p.type === "hover" ? "#1e847a" : "#a35d21", fillOpacity: .8})
        .bindTooltip(`${i+1}. ${escHtml(p.name)}${p.type === "passes" ? ` · ${j+1}` : ""}`).addTo(layer));
      if (p.points.length === 2) L.polyline(p.points.map(pt => [pt.lat, pt.lon]), {color: "#a35d21", weight: 2, dashArray: "6 5"}).addTo(layer);
    });
  }
  $("sciencePhases").addEventListener("input", (e) => {
    const el = e.target;
    if (!el.dataset.key) return;
    phases[Number(el.dataset.phase)][el.dataset.key] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
    invalidate();
  });
  $("sciencePhases").addEventListener("click", (e) => {
    const button = e.target.closest("button[data-action]");
    if (!button) return;
    const i = Number(button.dataset.phase), action = button.dataset.action;
    if (action === "pick") {
      picking = i;
      phases[i].points = [];
      invalidate();
      setStatus(phases[i].type === "hover" ? "Click the hover's reflection point on water." : "Click the water end, then the land end of the reflection pass.");
    } else {
      if (action === "remove") phases.splice(i, 1);
      else { const j = i + (action === "up" ? -1 : 1); [phases[i], phases[j]] = [phases[j], phases[i]]; }
      picking = -1;
      invalidate();
    }
    render();
  });
  function add(phase) {
    if (phases.length >= 20) return setStatus("Use at most 20 science phases.", true);
    phases.push(phase); picking = phases.length-1; invalidate(); render();
  }
  $("scienceAddHover").onclick = () => add(hover());
  $("scienceAddPasses").onclick = () => add(passes());
  $("sciencePreset").onclick = () => {
    phases = [hover("Group observation"), hover("Second observation", "E23"), passes()];
    picking = 0;
    $("h_agl").value = "121.92";
    invalidate(); render();
    setStatus("Preset ready: 400 ft, two 120-second hovers, three passes at 3 m/s. Select each phase's map points; E23 can be changed to E36.");
  };
  $("scienceOffset").oninput = invalidate;
  $("scienceRecording").onchange = invalidate;
  $("scienceSave").onclick = () => {
    const setup = {version: 1, phases, altitude_m: Number($("h_agl").value), transit_mps: Number($("speed").value),
      antenna_offset_deg: Number($("scienceOffset").value), start: $("start").value, pad: pad(),
      water_overlay: waterSource(), elev_mask: Number($("mask").value), constellations: selectedConsts(),
      recording_mode: $("scienceRecording").value};
    const url = URL.createObjectURL(new Blob([JSON.stringify(setup, null, 2)], {type: "application/json"}));
    const a = document.createElement("a"); a.href = url; a.download = "HERON_science_setup.json"; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  // Validate a science setup and put it in the editor. keepPlan leaves the mission on screen as built.
  function apply(config, keepPlan = false) {
    if (config.version !== 1 || !Array.isArray(config.phases) || !config.phases.length || config.phases.length > 20) throw Error("Not a science setup file.");
    const numeric = (x, min, max) => typeof x === "number" && Number.isFinite(x) && x >= min && x <= max;
    for (const p of config.phases) {
      if (!["hover", "passes"].includes(p.type) || typeof p.name !== "string" ||
          !(typeof p.satellites === "string" || Array.isArray(p.satellites)) || !Array.isArray(p.points) ||
          p.points.length > (p.type === "hover" ? 1 : 2) ||
          p.points.some(pt => !numeric(pt.lat, -85, 85) || !numeric(pt.lon, -180, 180)) ||
          (p.type === "hover" ? !numeric(p.duration_s, 1, 1200) :
            !numeric(p.count, 1, 20) || !Number.isInteger(p.count) || !numeric(p.speed_mps, 1, 25) || !numeric(p.spacing_m, 0, 300))) throw Error("Invalid science phase in setup file.");
    }
    if (!numeric(config.altitude_m, 2, 122) || !numeric(config.transit_mps, 1, 25) ||
        !numeric(config.antenna_offset_deg, -180, 180) || !numeric(config.elev_mask, 10, 40) ||
        !config.pad || !numeric(config.pad.lat, -85, 85) || !numeric(config.pad.lon, -180, 180) ||
        !Array.isArray(config.constellations) || !config.constellations.length || config.constellations.some(c => !["G","E","C"].includes(c)) ||
        typeof config.start !== "string" || !Number.isFinite(Date.parse(config.start))) throw Error("Invalid flight settings in setup file.");
    if (config.water_overlay && ![...$("scienceWater").options].some(o => o.value === config.water_overlay)) throw Error("This setup’s water outline is unavailable.");
    $("scienceWater").value = config.water_overlay || "";
    $("scienceRecording").value = config.recording_mode === "continuous" ? "continuous" : "phases";
    phases = config.phases;
    $("h_agl").value = config.altitude_m;
    $("speed").value = config.transit_mps;
    $("scienceOffset").value = config.antenna_offset_deg;
    $("start").value = config.start;
    showTakeoffLocal();
    $("mask").value = config.elev_mask;
    for (const c of ["G", "E", "C"]) $("c"+c).checked = config.constellations.includes(c);
    padMarker.setLatLng([config.pad.lat, config.pad.lon]);
    picking = -1;
    if (keepPlan) render(); else { invalidate(); render(); }
  }
  $("scienceLoad").onclick = () => $("scienceFile").click();
  $("scienceFile").onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      apply(JSON.parse(await file.text()));
      setStatus("Science setup loaded. Check the takeoff time, then Build mission.");
    } catch (err) { setStatus(String(err.message || err), true); }
    e.target.value = "";
  };
  const waterSelect = $("scienceWater");
  fetch("/api/overlays").then(r => r.json()).then(data => {
    for (const item of data.overlays || []) if (item.water) {
      const option = document.createElement("option"); option.value = item.id;
      option.textContent = `${item.name} · ${item.date}`; waterSelect.appendChild(option);
    }
  }).catch(() => setStatus("Could not load flight water outlines.", true));
  waterSelect.onchange = () => {
    invalidate();
    if (waterSelect.value) window.heronShowFlightWater?.(waterSelect.value);
  };
  render();
  return {
    load: (config) => apply(config, true),
    waterSource,
    recording: () => $("scienceRecording").value,
    stages: () => structuredClone(phases),
    setVisible(on) {
      visible = on;
      if (on && !entered) { $("h_agl").value = "121.92"; if (padMarker) map.setView(padMarker.getLatLng(), 15); entered = true; }
      draw();
    },
    pick(ll) {
      if (picking < 0 || !phases[picking]) return setStatus("Choose Select on map for a science phase.");
      const phase = phases[picking];
      phase.points.push({lat: ll.lat, lon: ll.lng});
      waterSource();
      if (phase.points.length === (phase.type === "hover" ? 1 : 2)) picking = -1;
      invalidate(); render();
      setStatus(picking < 0 ? "Location selected. Select the next phase's points, or Build mission." : "Now click the land end of the pass.");
    },
    summary(survey) {
      const fmt = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
      return `<div class="science-summary">${survey.phases.map((p,i) => `<p><b>${i+1}. ${escHtml(p.name)}</b> · +${fmt(p.start_s)}–${fmt(p.end_s)}<br>
        ${p.type === "hover" ? `${p.duration_s} s stationary` : `${p.count} passes · ${p.speed_mps} m/s`} · ${escHtml(ids(p.satellites))}<br>
        Primary ${escHtml(p.primary)} · largest sampled target heading offset ${p.max_pointing_offset_deg}°</p>`).join("")}
        ${survey.warnings.map(w => `<p class="science-warning">${escHtml(w)}</p>`).join("")}
        <p>All listed satellites are modeled together. Shared antenna reception and coherent reflection must be checked in the data.</p></div>`;
    },
  };
})();
