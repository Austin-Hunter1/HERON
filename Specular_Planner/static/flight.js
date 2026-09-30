/* Plan / Simulate / Fly workspaces. Planning lives in app.js; this file owns both flight decks. */
(() => {
  "use strict";
  window.addEventListener("error", (event) => console.error("Flight UI error:", event.message));
  window.addEventListener("unhandledrejection", (event) => console.error("Flight UI request:", String(event.reason)));
  const el = (id) => document.getElementById(id);
  const VIEWS = ["plan", "sim", "fly"];
  const NETWORK = {
    udp: { label: "UDP · ELRS Wi-Fi", host: "0.0.0.0", port: 14550, hostLabel: "Listen address" },
    herelink_hotspot: { label: "UDP · HereLink hotspot", host: "0.0.0.0", port: 14550, hostLabel: "Listen address" },
    herelink_client: { label: "UDP · HereLink USB", host: "192.168.42.129", port: 14552, hostLabel: "HereLink address" },
    tcp: { label: "TCP · ArduPilot SITL", host: "127.0.0.1", port: 5760, hostLabel: "Simulator address" },
  };
  const LANDED = { 1: "On ground", 2: "In air", 3: "Taking off", 4: "Landing" };

  function service(color) {
    return {
      token: "", state: null, at: 0, error: "", pending: false, polling: false, nextPoll: 0, lastJob: "",
      marker: null, rotation: null, home: null, generation: null, trackPts: [],
      track: L.polyline([], { color, weight: 3, opacity: 0.9, dashArray: "5 7" }),
      // "plan" flies the mission built on Plan; "aircraft" flies the one read from the drone.
      source: "plan", awaitingRead: false, onboardLayer: L.layerGroup(), onboardKey: "",
      activeLayer: L.layerGroup(), activeKey: "",
    };
  }
  const services = { flight: service("#ffb13b"), sim: service("#ffb13b") };
  const pfd = new window.HeronInstruments.PFD(el("pfd"));
  const hsi = new window.HeronInstruments.HSI(el("hsi"));
  let view = "plan";
  let plan = null;
  let planId = null;
  let confirmingPlan = null;
  let following = false;
  let showTrail = true;
  let portsKey = "";
  let eventsKey = "";
  let checksKey = "";
  let logFilter = "all";
  let wasLiveAttached = false;
  let toastTimer = null;
  let hold = null;
  let holdDone = false;

  const active = () => (view === "sim" ? "sim" : "flight");
  const attachedOf = (name) => !!services[name].state?.telemetry?.transport;
  const finite = (v) => typeof v === "number" && Number.isFinite(v);
  const onboardOf = (name) => services[name].state?.onboard || null;
  const missionId = (name) => (services[name].source === "aircraft" ? onboardOf(name)?.plan_id || null : planId);
  const fmtTime = (s) => (s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`
    : s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`);

  function bearing(a, b) {
    const p = (a.lat * Math.PI) / 180;
    const q = (b.lat * Math.PI) / 180;
    const dl = ((b.lon - a.lon) * Math.PI) / 180;
    const x = Math.sin(dl) * Math.cos(q);
    const y = Math.cos(p) * Math.sin(q) - Math.sin(p) * Math.cos(q) * Math.cos(dl);
    return ((Math.atan2(x, y) * 180) / Math.PI + 360) % 360;
  }
  const meters = (a, b) => map.distance([a.lat, a.lon], [b.lat, b.lon]);
  const fmtDist = (m) => (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`);

  function message(text, error = false) {
    el("flightMessage").textContent = text || "";
    el("flightMessage").classList.toggle("error", !!error);
  }

  async function api(name, path, body, retried = false) {
    const s = services[name];
    if (body !== undefined && !s.token) s.token = (await api(name, "session")).token;
    const response = await fetch(`/api/${name}/${path}`, {
      cache: "no-store",
      ...(body === undefined ? {} : {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Flight-Token": s.token },
        body: JSON.stringify(body),
      }),
    });
    const data = await response.json();
    if (response.status === 403 && body !== undefined && !retried && /Reload/.test(data.error || "")) {
      // The server restarted and issued a new token.
      s.token = "";
      return api(name, path, body, true);
    }
    if (!response.ok) throw new Error(data.error || `Aircraft service returned ${response.status}`);
    return data;
  }

  async function poll(name) {
    const s = services[name];
    if (s.polling) return;
    s.polling = true;
    const requested = missionId(name);
    try {
      const next = await api(name, `status${requested ? `?plan_id=${encodeURIComponent(requested)}` : ""}`);
      if (requested === missionId(name) || s.source === "aircraft") {
        s.state = next;
        s.at = Date.now();
        s.error = "";
      }
    } catch (error) {
      s.error = error.message;
      s.at = 0;
    } finally {
      s.polling = false;
      render();
    }
  }

  function schedule() {
    const now = Date.now();
    for (const name of ["flight", "sim"]) {
      const s = services[name];
      const focused = view !== "plan" && active() === name;
      const every = focused && attachedOf(name) ? 250 : focused || attachedOf(name) ? 1000 : 2500;
      if (now >= s.nextPoll) {
        s.nextPoll = now + every;
        poll(name);
      }
    }
  }

  async function action(name, path, body = {}) {
    const s = services[name];
    if (s.pending && path !== "rtl" && path !== "disconnect") return;
    s.pending = true;
    render();
    try {
      const result = await api(name, path, body);
      if (result.message) message(result.message);
      if (path === "connect") await api(name, "refresh", {});
      s.nextPoll = 0;
      await poll(name);
    } catch (error) {
      message(error.message, true);
    } finally {
      s.pending = false;
      render();
    }
  }

  function setView(next) {
    if (!VIEWS.includes(next)) next = "plan";
    view = next;
    document.body.dataset.view = next;
    document.querySelectorAll(".ab-tabs [data-view]").forEach((tab) => {
      tab.setAttribute("aria-selected", String(tab.dataset.view === next));
    });
    const flying = next !== "plan";
    if (flying) stopPlay();
    // The predicted playback, satellites and bounce points belong to Plan only.
    [droneLayer, satLayer, specLayer, trailLayer].forEach((layer) => {
      if (flying) map.removeLayer(layer);
      else if (!map.hasLayer(layer)) layer.addTo(map);
    });
    if (padMarker && padMarker.dragging) {
      if (flying) padMarker.dragging.disable();
      else padMarker.dragging.enable();
    }
    closePopover();
    try {
      history.replaceState(null, "", `#${next}`);
    } catch (_) {}
    services.flight.nextPoll = 0;
    services.sim.nextPoll = 0;
    syncMapLayers();
    setTimeout(() => map.invalidateSize(), 0);
    render();
  }

  function aircraftIcon(kind) {
    return L.divIcon({
      className: `ac-marker ${kind}`,
      html: '<div class="ac-body"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M16 3 26 27 16 21 6 27z"/></svg></div>',
      iconSize: [32, 32],
      iconAnchor: [16, 16],
    });
  }
  const homeIcon = L.divIcon({ className: "home-marker", html: "<span>H</span>", iconSize: [24, 24], iconAnchor: [12, 12] });

  function syncMapLayers() {
    for (const name of ["flight", "sim"]) {
      const s = services[name];
      const shown = (view === "fly" && name === "flight") || (view === "sim" && name === "sim");
      for (const layer of [s.marker, s.home]) {
        if (!layer) continue;
        if (shown && !map.hasLayer(layer)) layer.addTo(map);
        if (!shown && map.hasLayer(layer)) map.removeLayer(layer);
      }
      const trail = shown && showTrail;
      if (trail && !map.hasLayer(s.track)) s.track.addTo(map);
      if (!trail && map.hasLayer(s.track)) map.removeLayer(s.track);
      for (const layer of [s.onboardLayer, s.activeLayer]) {
        if (shown && !map.hasLayer(layer)) layer.addTo(map);
        if (!shown && map.hasLayer(layer)) map.removeLayer(layer);
      }
    }
  }

  /** Where a drone-stored mission starts: the drone takes off wherever it is, so prefer its GPS Home, then the pad. */
  function launchPoint(name) {
    const home = services[name].state?.telemetry?.home;
    if (home && (home.lat || home.lon)) return { point: { lat: home.lat, lon: home.lon }, source: "gps" };
    const pad = typeof padMarker !== "undefined" && padMarker ? padMarker.getLatLng() : null;
    if (pad) return { point: { lat: pad.lat, lon: pad.lng }, source: "pad" };
    return { point: null, source: null };
  }

  /** Mission stored on the drone. The HERON plan already has its own route on the map. */
  function paintOnboard(name) {
    const s = services[name];
    const onboard = onboardOf(name);
    const show = onboard && onboard.path.length && !(onboard.plan_id && onboard.plan_id === planId && s.source === "plan");
    const home = show ? launchPoint(name).point : null;
    const rois = show ? onboard.roi || [] : [];
    const key = show ? JSON.stringify([onboard.path, rois, home, onboard.error]) : "";
    if (key === s.onboardKey) return;
    s.onboardKey = key;
    s.onboardLayer.clearLayers();
    if (!show) return;
    const layer = s.onboardLayer;
    const pts = onboard.path.map((p) => [p.lat, p.lon]);
    const route = home ? [[home.lat, home.lon], ...pts, [home.lat, home.lon]] : pts;
    // Drawn exactly like the Plan page; gold is kept for the track the drone actually flies.
    L.polyline(route, { color: onboard.error ? "#ff6b5e" : "#11a39a", weight: 3, opacity: 0.95, interactive: false }).addTo(layer);

    const dense = onboard.path.length > 48;
    const every = Math.ceil(onboard.path.length / 24);
    const wpNumber = new Map(onboard.path.map((p, i) => [p.seq, i + 1]));
    const bySeq = new Map(onboard.path.map((p) => [p.seq, p]));
    const roiStep = rois.length > 80 ? Math.ceil(rois.length / 80) : 1;
    rois.forEach((r, i) => {
      if (i % roiStep && i !== rois.length - 1) return;
      const wp = bySeq.get(r.wp);
      if (wp) {
        L.polyline([[wp.lat, wp.lon], [r.lat, r.lon]],
          { color: "#ffe66a", weight: 1, opacity: 0.35, dashArray: "3 4", interactive: false }).addTo(layer);
      }
      L.circleMarker([r.lat, r.lon], { radius: 3, color: "#ffe66a", weight: 1, fillColor: "#ffe66a", fillOpacity: 0.85 })
        .bindTooltip(`ROI · item #${r.seq}${wp ? ` · camera aims here from WP${wpNumber.get(r.wp)}` : ""}`, { direction: "top" })
        .addTo(layer);
    });
    onboard.path.forEach((p, i) => {
      const fill = onboard.error ? "#ff6b5e" : "#1f6feb";
      L.circleMarker([p.lat, p.lon], { radius: dense ? 3 : 7, color: "#fff", weight: dense ? 1 : 2, fillColor: fill, fillOpacity: 1 })
        .bindTooltip(`WP${i + 1} · item #${p.seq} · ${Math.round(p.alt)} m above Home`, { direction: "top" })
        .addTo(layer);
      if (dense && i && i !== onboard.path.length - 1 && i % every) return;
      L.marker([p.lat, p.lon], {
        interactive: false,
        keyboard: false,
        icon: L.divIcon({ className: "", html: `<div class="wp-label">WP${i + 1}</div>`, iconSize: [40, 16], iconAnchor: [20, 22] }),
      }).addTo(layer);
    });
  }

  /** Highlights the waypoint the drone is flying to and the ROI it is aiming at. */
  function paintActive(name) {
    const s = services[name];
    const st = s.state;
    const target = st?.telemetry?.armed ? st.target : null;
    const roi = target && (onboardOf(name)?.roi || []).find((r) => r.wp === target.seq);
    const key = target ? JSON.stringify([target.seq, target.lat, target.lon, roi?.seq]) : "";
    if (key === s.activeKey) return;
    s.activeKey = key;
    s.activeLayer.clearLayers();
    if (!target) return;
    if (roi) {
      L.polyline([[target.lat, target.lon], [roi.lat, roi.lon]],
        { color: "#ffe66a", weight: 2.5, opacity: 0.95, interactive: false }).addTo(s.activeLayer);
      L.marker([roi.lat, roi.lon], {
        interactive: false, keyboard: false, zIndexOffset: 900,
        icon: L.divIcon({ className: "active-roi", html: "<i></i><b>ROI</b>", iconSize: [28, 28], iconAnchor: [14, 14] }),
      }).addTo(s.activeLayer);
    }
    L.marker([target.lat, target.lon], {
      interactive: false, keyboard: false, zIndexOffset: 1000,
      icon: L.divIcon({
        className: "active-wp",
        html: `<i></i><b>${target.wp ? `WP${target.wp}` : "Next"}</b>`,
        iconSize: [34, 34],
        iconAnchor: [17, 17],
      }),
    }).addTo(s.activeLayer);
  }

  function paintMap(name, tel, connected) {
    const s = services[name];
    const kind = name === "sim" ? "sim" : "live";
    if (tel.generation !== s.generation) {
      s.generation = tel.generation;
      s.trackPts = [];
      s.track.setLatLngs([]);
    }
    if (!tel.transport) {
      [s.marker, s.home].forEach((layer) => layer && map.removeLayer(layer));
      s.marker = null;
      s.home = null;
      s.rotation = null;
      return;
    }
    if (tel.position) {
      const ll = [tel.position.lat, tel.position.lon];
      if (!s.marker) s.marker = L.marker(ll, { icon: aircraftIcon(kind), interactive: false, zIndexOffset: 2000, keyboard: false });
      s.marker.setLatLng(ll);
      const node = s.marker.getElement();
      if (node) {
        node.classList.toggle("stale", !connected || !tel.fresh?.position);
        if (finite(tel.heading)) {
          // Unwrap so the CSS transition never spins the long way round.
          const prev = s.rotation ?? tel.heading;
          s.rotation = prev + ((((tel.heading - prev) % 360) + 540) % 360 - 180);
          node.querySelector(".ac-body").style.transform = `rotate(${s.rotation}deg)`;
        }
      }
      if (connected && tel.fresh?.position && (!s.trackPts.length || map.distance(s.trackPts.at(-1), ll) > 1)) {
        s.trackPts.push(ll);
        if (s.trackPts.length > 3000) s.trackPts.shift();
        s.track.setLatLngs(s.trackPts);
      }
      if (following && connected && active() === name && view !== "plan") map.panTo(ll, { animate: true, duration: 0.25 });
    }
    if (tel.home) {
      const ll = [tel.home.lat, tel.home.lon];
      if (!s.home) s.home = L.marker(ll, { icon: homeIcon, interactive: false, keyboard: false });
      s.home.setLatLng(ll);
    }
    syncMapLayers();
  }

  function renderPorts(st) {
    const ports = st?.auto?.ports || [];
    const key = JSON.stringify(ports);
    if (key === portsKey) return;
    portsKey = key;
    const select = el("connPort");
    const keep = select.value;
    const usb = document.createElement("optgroup");
    usb.label = "USB";
    ports.forEach((p) => {
      const desc = (p.description || "").replace(/\s*\(COM\d+\)\s*$/i, "");
      usb.append(new Option(`${p.device} · ${desc || "Serial"}`, `serial:${p.device}`));
    });
    const net = document.createElement("optgroup");
    net.label = "Network";
    Object.entries(NETWORK).forEach(([value, info]) => net.append(new Option(info.label, value)));
    select.replaceChildren(new Option("AUTO", "auto"), ...(ports.length ? [usb] : []), net);
    select.value = [...select.options].some((o) => o.value === keep) ? keep : "auto";
  }

  function syncConnFields() {
    const choice = el("connPort").value;
    const info = NETWORK[choice];
    el("connBaud").disabled = attachedOf("flight") || !choice.startsWith("serial:");
    if (info) {
      el("flightHost").value = info.host;
      el("flightPort").value = String(info.port);
      el("flightHostLabel").textContent = info.hostLabel;
    }
  }

  function connectOptions() {
    const choice = el("connPort").value;
    const common = { system_id: Number(el("flightSystem").value) || 1 };
    if (choice.startsWith("serial:")) {
      return { transport: "serial", device: choice.slice(7), baud: Number(el("connBaud").value), ...common };
    }
    return { transport: choice, host: el("flightHost").value.trim(), port: Number(el("flightPort").value), ...common };
  }

  function renderConnection() {
    const s = services.flight;
    const st = s.state;
    const attached = attachedOf("flight");
    renderPorts(st);
    const button = el("connButton");
    button.textContent = attached ? "Disconnect" : s.pending ? "Connecting…" : "Connect";
    button.classList.toggle("stop", attached);
    button.disabled = s.pending && !attached;
    el("connPort").disabled = attached;
    if (attached && st.link) {
      const value = st.link.device ? `serial:${st.link.device}` : st.link.transport;
      if ([...el("connPort").options].some((o) => o.value === value)) el("connPort").value = value;
      if (st.link.baud) el("connBaud").value = String(st.link.baud);
    }
    el("connBaud").disabled = attached || !el("connPort").value.startsWith("serial:");
    el("connAuto").checked = !!st?.auto?.enabled;

    const sim = services.sim;
    const simOn = attachedOf("sim");
    el("simButton").textContent = simOn ? "Stop simulator" : sim.pending ? "Starting…" : "Start simulator";
    el("simButton").classList.toggle("stop", simOn);
    el("simButton").disabled = sim.pending && !simOn;
  }

  function renderStatusBar(name, tel, connected) {
    const attached = !!tel.transport;
    el("abStatus").classList.toggle("hidden", !attached);
    const fly = services.flight.state?.telemetry || {};
    const flyConnected = !!fly.connected && Date.now() - services.flight.at < 4000;
    el("dotFly").className = `tab-dot${fly.transport ? (flyConnected ? " on" : " stale") : ""}`;
    el("dotSim").className = `tab-dot sim${attachedOf("sim") ? " on" : ""}`;
    if (!attached) return;
    const st = services[name].state;
    const mode = el("abMode");
    mode.textContent = connected ? tel.mode || "—" : "—";
    const gps = el("abGps");
    gps.textContent = connected && tel.fresh?.gps ? `${tel.gps_fix >= 3 ? "3D" : "No fix"} · ${tel.satellites}` : "—";
    gps.className = connected && tel.fresh?.gps ? (tel.gps_fix >= 3 && tel.satellites >= 6 ? "good" : "warn") : "";
    const batt = el("abBatt");
    const pct = connected && tel.fresh?.battery && tel.battery_pct >= 0 ? tel.battery_pct : null;
    batt.textContent = pct == null ? "—" : `${pct}%${finite(tel.battery_voltage) ? ` · ${tel.battery_voltage.toFixed(1)} V` : ""}`;
    batt.className = pct == null ? "" : pct < 30 ? "bad" : pct < 50 ? "warn" : "good";
    const link = el("abLink");
    link.textContent = !connected ? "LOST" : name === "sim" ? "SIM" : `${Number(tel.heartbeat_age || 0).toFixed(1)} s`;
    link.className = connected ? "good" : "bad";
    const ready = el("abReady");
    ready.dataset.state = !connected ? "lost" : tel.armed ? "armed" : st?.preflight?.ready ? "ready" : "check";
    el("abReadyText").textContent = !connected ? "Link lost" : tel.armed ? "Armed" : st?.preflight?.ready ? "Ready to launch" : "Not ready";
  }

  function renderEmpty(name) {
    const radar = el("emptyRadar");
    const primary = el("emptyPrimary");
    const secondary = el("emptySecondary");
    if (name === "sim") {
      radar.dataset.state = "idle";
      el("emptyKicker").textContent = "Simulate";
      el("emptyTitle").textContent = "Simulator is off";
      el("emptyText").textContent = "Fly your mission on a simulated quadcopter before the real one. Nothing is sent to hardware.";
      primary.textContent = services.sim.pending ? "Starting…" : "Start simulator";
      primary.disabled = services.sim.pending;
      secondary.textContent = "Build a mission";
      secondary.classList.toggle("hidden", !!plan);
      el("emptyHint").textContent = plan
        ? `${plan.exports.filename} is ready. The simulator starts at your launch pad.`
        : "The simulator starts at your launch pad. Build a mission on Plan to fly it.";
      return;
    }
    const s = services.flight;
    const auto = s.state?.auto || {};
    const ports = auto.ports || [];
    el("emptyKicker").textContent = "Fly";
    secondary.textContent = "Other connections";
    secondary.classList.remove("hidden");
    primary.disabled = s.pending;
    if (!s.at && s.error) {
      radar.dataset.state = "idle";
      el("emptyTitle").textContent = "Ground control is not responding";
      el("emptyText").textContent = "Restart python app.py, then reload this page.";
      primary.textContent = "Retry";
    } else if (!auto.enabled) {
      radar.dataset.state = "idle";
      el("emptyTitle").textContent = "Auto-connect is off";
      el("emptyText").textContent = "Pick a port or network link in the top bar and press Connect, or turn auto-connect back on.";
      primary.textContent = "Turn on auto-connect";
    } else if (auto.paused) {
      radar.dataset.state = "idle";
      el("emptyTitle").textContent = "Disconnected";
      el("emptyText").textContent = auto.status || "Press Connect to reconnect.";
      primary.textContent = "Reconnect";
    } else {
      radar.dataset.state = "scan";
      el("emptyTitle").textContent = "Looking for your aircraft";
      el("emptyText").textContent = auto.status || "Plug the flight controller into USB. HERON connects on its own.";
      primary.textContent = s.pending ? "Connecting…" : "Scan now";
    }
    const autopilots = ports.filter((p) => p.autopilot).map((p) => p.device);
    el("emptyHint").textContent = !ports.length
      ? "No USB serial devices detected. For radio or SITL links, use Other connections."
      : autopilots.length
        ? `Flight controller seen on ${autopilots.join(", ")}. Close Mission Planner or QGroundControl if they hold the port.`
        : `Serial ports seen: ${ports.map((p) => p.device).join(", ")}. None identify as a flight controller; pick one in the top bar to connect manually.`;
  }

  function renderMission(name, st, tel, connected) {
    const s = services[name];
    const busy = s.pending || st?.job?.state === "running";
    const reading = busy && st?.job?.action === "read";
    const disarmed = connected && tel.armed === false;
    const onboard = onboardOf(name);
    const fromDrone = s.source === "aircraft" && !!onboard;
    const id = missionId(name);
    const summary = st?.mission && st.mission.plan_id === id ? st.mission : fromDrone && onboard.plan_id ? onboard : null;
    const verified = !!id && st?.verified?.plan_id === id;
    const ready = !!st?.preflight?.ready;
    const checks = st?.preflight?.checks || [];
    const enabled = checks.filter((c) => c.enabled);
    const passed = enabled.filter((c) => c.ok).length;
    const failing = enabled.filter((c) => !c.ok).length;

    const sameMission = onboard && onboard.plan_id && onboard.plan_id === planId;
    el("missionSource").classList.toggle("hidden", !onboard || !!sameMission);
    el("missionSource").querySelectorAll("[data-source]").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.source === (fromDrone ? "aircraft" : "plan")));
      b.disabled = busy || (b.dataset.source === "plan" && !plan);
    });

    const badge = el("missionBadge");
    const unflyable = fromDrone && !!onboard.error;
    const hasMission = fromDrone || !!plan;
    badge.textContent = !hasMission ? "No mission" : unflyable ? "Can't fly" : verified ? "On drone ✓" : id ? "Not on drone" : "Changed";
    badge.className = `pill ${!hasMission ? "" : unflyable ? "bad" : verified ? "good" : "warn"}`;
    el("missionName").textContent = fromDrone
      ? `Mission on drone · ${onboard.count - 1} items`
      : plan ? plan.exports.filename : "Nothing loaded";
    el("flightPlanNote").textContent = unflyable
      ? onboard.error
      : summary
        ? `${summary.n_wp} waypoints · up to ${Math.round(summary.max_alt)} m above Home · ${fmtDist(summary.length_m)} · ≈${fmtTime(summary.est_s)}`
          + (fromDrone ? (launchPoint(name).source === "gps"
            ? " · takes off from the drone's GPS Home"
            : " · shown from your launch pad; the drone takes off wherever it gets GPS and arms") : "")
        : fromDrone || !plan
          ? "Build a mission on the Plan page, or read the one already on the drone."
          : !planId
            ? "The plan changed after it was built. Build it again before writing it."
            : `${plan.meta.n_hovers} waypoints · ${plan.meta.h_agl} m above Home`;

    el("missionRead").disabled = !connected || busy;
    el("missionRead").lastChild.textContent = reading ? "Reading…" : "Read from drone";
    el("flightUpload").classList.toggle("hidden", fromDrone);
    el("flightUpload").disabled = !disarmed || busy || !planId;
    const writing = busy && st?.job?.action === "upload";
    el("flightUpload").lastChild.textContent = writing ? "Writing…" : "Write to drone";
    el("stepUploadTitle").textContent = verified ? "Mission on drone" : fromDrone ? "Mission read from drone" : "Put the mission on the drone";
    el("stepUploadText").textContent = verified
      ? fromDrone ? "Read back from the flight controller" : "Written and read back to verify"
      : unflyable ? "Rebuild it in HERON or fix it in Mission Planner" : "Write the plan, or read the drone's mission";
    el("stepUpload").classList.toggle("done", verified);

    const flyable = disarmed && !busy && !!id && ready && verified;
    const launching = busy && st?.job?.action === "launch";
    const launchBtn = el("flightLaunch");
    launchBtn.disabled = !flyable;
    launchBtn.classList.toggle("sim", name === "sim");
    launchBtn.classList.toggle("hidden", !connected || !hasMission || unflyable || (!!tel.armed && !launching));
    launchBtn.classList.toggle("ready", flyable);
    el("flyHint").textContent = launching ? st.job.message
      : !connected ? "No aircraft link"
        : tel.armed ? "Aircraft is armed"
          : !id ? "Load a mission first"
            : !verified ? "Write or read the mission first"
              : !ready ? `${failing} launch check${failing === 1 ? "" : "s"} still failing`
                : "Auto takeoff, fly every waypoint, return home";
    if (el("flightConfirm").open) {
      el("flightConfirmLaunch").disabled = !flyable && !holdDone;
      if (!flyable && !holdDone) holdCancel();
    }

    el("flightRefresh").disabled = !connected || busy;
    const skipped = checks.length - enabled.length;
    el("flightCheckSummary").textContent = checks.length
      ? `Launch checks · ${passed}/${enabled.length}${skipped ? ` · ${skipped} off` : ""}`
      : "Launch checks";
    el("stepChecks").classList.toggle("done", ready);
    renderChecks(name, checks, connected);
    renderProgress(st, tel, connected);
    el("flightUseHome").classList.toggle("hidden", name === "sim");
    el("flightUseHome").disabled = !disarmed || busy || !tel.home || !tel.fresh?.home;
    el("flightRtl").disabled = !connected;
    if (st?.job?.message && st.job.message !== s.lastJob) {
      s.lastJob = st.job.message;
      message(st.job.message, st.job.state === "error");
    }
    if (!connected) message("Aircraft link is stale. Live values are hidden and launch is blocked. Use the RC override if needed.", true);
  }

  function renderProgress(st, tel, connected) {
    const box = el("missionProgress");
    const target = st?.target;
    const flying = connected && tel.armed && (tel.landed !== 1 || tel.mode === "AUTO");
    box.classList.toggle("hidden", !flying);
    if (!flying) return;
    let text = tel.mode || "Flying";
    let frac = 0;
    let eta = "";
    if (tel.mode === "AUTO" && target) {
      frac = target.total ? Math.max(0, target.wp - 1) / target.total : 0;
      text = target.wp ? `Waypoint ${target.wp} of ${target.total}` : "Taking off";
      if (tel.position) {
        const d = meters(tel.position, target);
        eta = `${fmtDist(d)}${tel.speed > 0.5 ? ` · ${fmtTime(d / tel.speed)}` : ""}`;
      }
    } else if (tel.mode === "AUTO") {
      text = "Taking off";
    } else if (tel.mode === "RTL") {
      text = "Returning home";
      frac = 1;
      if (tel.position && tel.home) eta = fmtDist(meters(tel.position, tel.home));
    } else if (tel.mode === "LAND") {
      text = "Landing";
      frac = 1;
    } else {
      text = `${tel.mode || "Manual"} · mission paused`;
    }
    el("missionProgressText").textContent = text;
    el("missionProgressEta").textContent = eta;
    el("missionProgressBar").style.width = `${Math.round(frac * 100)}%`;
    box.dataset.mode = tel.mode || "";
  }

  function renderInstruments(name, st, tel, connected) {
    const pos = connected && !!tel.fresh?.position && !!tel.position;
    const att = connected && !!tel.fresh?.attitude && finite(tel.roll) && finite(tel.pitch);
    const hdg = connected && !!tel.fresh?.attitude && finite(tel.heading);
    const cog = connected && !!tel.fresh?.gps && finite(tel.course) ? tel.course : null;
    const target = st?.target || null;
    pfd.set({
      roll: att ? tel.roll : 0,
      pitch: att ? tel.pitch : 0,
      heading: hdg ? tel.heading : pfd.target.heading,
      speed: finite(tel.speed) ? tel.speed : 0,
      alt: finite(tel.relative_alt) ? tel.relative_alt : 0,
      vs: finite(tel.vertical_speed) ? tel.vertical_speed : 0,
      targetAlt: target ? target.alt : null,
      mode: tel.mode || "—",
      armed: !!tel.armed,
      att,
      pos,
      link: connected,
      sim: name === "sim",
    });
    const homeBrg = pos && tel.home ? bearing(tel.position, tel.home) : null;
    const wpBrg = pos && target ? bearing(tel.position, target) : null;
    hsi.set({ heading: hdg ? tel.heading : hsi.target.heading, home: homeBrg, wp: wpBrg, course: cog, valid: hdg });
    el("tHdg").textContent = hdg ? `${String(Math.round(tel.heading) % 360).padStart(3, "0")}°` : "—";
    el("tCog").textContent = cog != null ? `${String(Math.round(cog) % 360).padStart(3, "0")}°` : tel.fresh?.gps && tel.gps_fix >= 3 ? "Not moving" : "No GPS";
    el("tSpeed").textContent = pos && finite(tel.speed) ? `${tel.speed.toFixed(1)} m/s` : "—";
    el("tAlt").textContent = pos && finite(tel.relative_alt) ? `${tel.relative_alt.toFixed(1)} m` : "—";
    el("tClimb").textContent = pos && finite(tel.vertical_speed) ? `${tel.vertical_speed > 0.05 ? "+" : ""}${tel.vertical_speed.toFixed(1)} m/s` : "—";
    el("tHome").textContent = pos && tel.home ? fmtDist(meters(tel.position, tel.home)) : "—";
    el("tWp").textContent = target
      ? `WP ${target.seq}${pos ? ` · ${fmtDist(meters(tel.position, target))}` : ""}`
      : connected && tel.mission_current != null ? `Item ${tel.mission_current}` : "—";
    el("tLanded").textContent = connected && tel.fresh?.landed ? LANDED[tel.landed] || "Unknown" : "—";
    el("flightCenter").disabled = !tel.position;
    el("flightFollow").disabled = !pos;
  }

  function renderChecks(name, checks, connected) {
    const key = JSON.stringify([name, connected, checks]);
    if (key === checksKey) return;
    checksKey = key;
    el("flightChecks").replaceChildren(...checks.map((check) => {
      const li = document.createElement("li");
      li.className = !check.enabled ? "off" : check.ok && connected ? "pass" : "fail";
      const text = document.createElement("span");
      text.textContent = check.message;
      li.append(text);
      if (check.required) {
        const lock = document.createElement("i");
        lock.className = "lock";
        lock.title = "Always checked";
        lock.setAttribute("aria-label", "Always checked");
        li.append(lock);
      } else {
        const toggle = document.createElement("input");
        toggle.type = "checkbox";
        toggle.className = "check-switch";
        toggle.checked = check.enabled;
        toggle.title = check.enabled ? "Checked before launch. Click to skip." : "Skipped. Click to check before launch.";
        toggle.setAttribute("aria-label", `Check before launch: ${check.message}`);
        toggle.onchange = () => action(name, "checks", { key: check.key, enabled: toggle.checked }).finally(() => {
          checksKey = "";
          render();
        });
        li.append(toggle);
      }
      return li;
    }));
  }

  const SEVERITY = ["Emergency", "Alert", "Critical", "Error", "Warning", "Notice", "Info", "Debug"];
  function severityClass(sev) {
    return sev <= 3 ? "sev-error" : sev === 4 ? "sev-warn" : sev === 7 ? "sev-debug" : "sev-info";
  }

  function renderLog(st) {
    const events = (st?.events || []).filter((e) => logFilter === "all" || (e.source || "heron") === logFilter);
    el("flightMessageCount").textContent = String(events.length);
    const latest = events.at(-1);
    el("logLatest").textContent = latest ? latest.text : "No aircraft messages yet";
    el("logLatest").className = `log-latest ${latest ? severityClass(latest.severity ?? 6) : ""}`;
    const key = `${logFilter}:${events.length}:${latest?.id ?? latest?.time ?? ""}`;
    if (key === eventsKey) return;
    eventsKey = key;
    el("flightEvents").replaceChildren(...events.slice().reverse().map((event) => {
      const sev = event.severity ?? 6;
      const li = document.createElement("li");
      li.className = severityClass(sev);
      li.title = SEVERITY[sev] || "";
      const time = document.createElement("time");
      time.textContent = new Date(event.time * 1000).toLocaleTimeString([], { hour12: false });
      const src = document.createElement("b");
      src.className = `src ${event.source === "ardupilot" ? "ap" : "heron"}`;
      src.textContent = event.source === "ardupilot" ? "AP" : "HERON";
      const text = document.createElement("span");
      text.textContent = event.text;
      li.append(time, src, text);
      return li;
    }));
  }

  function setLogOpen(open) {
    el("deckLog").classList.toggle("collapsed", !open);
    el("logToggle").setAttribute("aria-expanded", String(open));
    el("logToggle").setAttribute("aria-label", open ? "Collapse messages" : "Expand messages");
    try {
      localStorage.setItem("heron.logOpen", open ? "1" : "0");
    } catch (_) {}
  }

  function toastLive(device) {
    let toast = document.querySelector(".toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "toast";
      toast.setAttribute("role", "status");
      document.body.append(toast);
    }
    toast.innerHTML = "";
    const text = document.createElement("span");
    text.textContent = `Aircraft connected${device ? ` on ${device}` : ""}.`;
    const open = document.createElement("button");
    open.type = "button";
    open.textContent = "Open Fly";
    open.onclick = () => {
      toast.remove();
      setView("fly");
    };
    toast.append(text, open);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.remove(), 9000);
  }

  function render() {
    const name = active();
    const s = services[name];
    const st = s.state;
    const tel = st?.telemetry || {};
    const attached = !!tel.transport;
    const connected = attached && !!tel.connected && Date.now() - s.at < 4000;

    const liveAttached = attachedOf("flight");
    if (liveAttached && !wasLiveAttached && view === "plan") toastLive(services.flight.state?.link?.device);
    wasLiveAttached = liveAttached;

    for (const other of ["flight", "sim"]) {
      const os = services[other];
      const otel = os.state?.telemetry || {};
      if (os.awaitingRead && os.state?.job?.action === "read" && os.state.job.state !== "running") {
        os.awaitingRead = false;
        if (os.state.onboard) os.source = "aircraft";
      }
      if (!os.state?.onboard && os.source === "aircraft") os.source = "plan";
      paintMap(other, otel, !!otel.connected && Date.now() - os.at < 4000);
      paintOnboard(other);
      paintActive(other);
    }
    renderConnection();
    renderStatusBar(name, tel, connected);
    el("abConnFly").classList.toggle("hidden", view === "sim");
    el("abConnSim").classList.toggle("hidden", view !== "sim");
    if (view === "plan") return;

    el("deck").classList.toggle("attached", attached);
    if (!attached) {
      renderEmpty(name);
      return;
    }
    renderMission(name, st, tel, connected);
    renderInstruments(name, st, tel, connected);
    renderLog(st);
  }

  function openPopover(open) {
    el("connPopover").classList.toggle("hidden", !open);
    el("connSettings").setAttribute("aria-expanded", String(open));
  }
  function closePopover() {
    openPopover(false);
  }

  function invalidate() {
    planId = null;
    confirmingPlan = null;
    if (el("flightConfirm").open) el("flightConfirm").close();
    render();
  }

  document.querySelectorAll(".ab-tabs [data-view]").forEach((tab) => {
    tab.onclick = () => setView(tab.dataset.view);
  });
  window.addEventListener("hashchange", () => {
    const next = location.hash.slice(1);
    if (next !== view && VIEWS.includes(next)) setView(next);
  });

  window.addEventListener("heron:plan-invalid", invalidate);
  window.addEventListener("heron:plan-ready", (event) => {
    plan = event.detail;
    planId = plan.flight_plan_id || null;
    for (const s of Object.values(services)) {
      s.source = "plan";
      s.nextPoll = 0;
    }
    render();
  });
  el("form").addEventListener("input", invalidate);
  el("form").addEventListener("change", invalidate);
  ["btnUndoWp", "btnClearWp", "btnClearSurvey", "btnClear", "btnUndo", "btnDraw"].forEach((id) => el(id)?.addEventListener("click", invalidate));
  map.on("click", () => {
    if (view === "plan") invalidate();
  });
  (function bindPad() {
    if (typeof padMarker !== "undefined" && padMarker) padMarker.on("dragstart", invalidate);
    else setTimeout(bindPad, 250);
  })();

  el("connPort").onchange = syncConnFields;
  el("connButton").onclick = () => {
    if (attachedOf("flight")) return action("flight", "disconnect");
    if (el("connPort").value === "auto") return action("flight", "auto", { enabled: true });
    closePopover();
    action("flight", "connect", connectOptions());
  };
  el("connAuto").onchange = () => action("flight", "auto", { enabled: el("connAuto").checked });
  el("connSettings").onclick = (event) => {
    event.stopPropagation();
    openPopover(el("connPopover").classList.contains("hidden"));
  };
  document.addEventListener("click", (event) => {
    if (!el("connPopover").contains(event.target) && event.target !== el("connSettings")) closePopover();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePopover();
  });

  el("simButton").onclick = () => {
    if (attachedOf("sim")) return action("sim", "disconnect");
    const pad = typeof padMarker !== "undefined" && padMarker ? padMarker.getLatLng() : null;
    action("sim", "connect", { transport: "demo", ...(pad ? { home: { lat: pad.lat, lon: pad.lng } } : {}) });
  };
  el("emptyPrimary").onclick = () => {
    if (view === "sim") return el("simButton").click();
    if (!services.flight.at && services.flight.error) {
      services.flight.nextPoll = 0;
      return;
    }
    action("flight", "auto", { enabled: true });
  };
  el("emptySecondary").onclick = (event) => {
    if (view === "sim") return setView("plan");
    event.stopPropagation();
    openPopover(true);
    el("connPort").focus();
  };

  el("abReady").onclick = () => {
    if (view === "plan") setView("fly");
    el("flightPreflight").classList.remove("hidden");
    el("flightChecksOpen").setAttribute("aria-expanded", "true");
  };
  el("flightChecksOpen").onclick = () => {
    const open = el("flightPreflight").classList.toggle("hidden") === false;
    el("flightChecksOpen").setAttribute("aria-expanded", String(open));
  };
  el("flightRefresh").onclick = () => action(active(), "refresh");
  el("flightUpload").onclick = () => action(active(), "upload", { plan_id: planId });
  el("flightRtl").onclick = () => {
    if (el("flightConfirm").open) el("flightConfirm").close();
    action(active(), "rtl");
  };
  el("flightUseHome").onclick = () => {
    const home = services.flight.state?.telemetry?.home;
    if (!home || !padMarker) return;
    padMarker.setLatLng([home.lat, home.lon]);
    syncPadWrap();
    paintClickWps();
    invalidate();
    setView("plan");
    setStatus("Launch pad moved to the aircraft's Home. Build the mission again at this field.");
  };
  el("missionToPlan").onclick = () => setView("plan");

  el("flightCenter").onclick = () => {
    const p = services[active()].state?.telemetry?.position;
    if (p) map.setView([p.lat, p.lon], Math.max(map.getZoom(), 17));
  };
  el("flightFollow").onclick = () => {
    following = !following;
    el("flightFollow").setAttribute("aria-pressed", String(following));
    render();
  };
  map.on("dragstart", () => {
    following = false;
    el("flightFollow").setAttribute("aria-pressed", "false");
  });
  el("flightTrack").onclick = () => {
    showTrail = !showTrail;
    el("flightTrack").setAttribute("aria-pressed", String(showTrail));
    syncMapLayers();
  };
  el("logToggle").onclick = () => setLogOpen(el("deckLog").classList.contains("collapsed"));
  document.querySelectorAll("#deckLog [data-filter]").forEach((button) => {
    button.onclick = () => {
      logFilter = button.dataset.filter;
      document.querySelectorAll("#deckLog [data-filter]").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
      if (el("deckLog").classList.contains("collapsed")) setLogOpen(true);
      render();
    };
  });
  try {
    setLogOpen(localStorage.getItem("heron.logOpen") !== "0");
  } catch (_) {
    setLogOpen(true);
  }

  el("missionRead").onclick = async () => {
    const name = active();
    await action(name, "read");
    services[name].awaitingRead = true;
    render();
  };
  el("missionSource").querySelectorAll("[data-source]").forEach((button) => {
    button.onclick = () => {
      const s = services[active()];
      s.source = button.dataset.source;
      s.nextPoll = 0;
      checksKey = "";
      render();
    };
  });

  el("flightLaunch").onclick = () => {
    const name = active();
    const id = missionId(name);
    if (!id || el("flightLaunch").disabled) return;
    const st = services[name].state;
    const summary = st?.mission?.plan_id === id ? st.mission : onboardOf(name);
    const demo = name === "sim";
    const fromDrone = services[name].source === "aircraft";
    confirmingPlan = { name, id };
    el("confirmKicker").textContent = demo ? "Fly mission · Simulation" : "Fly mission · Real aircraft";
    el("flightConfirm").classList.toggle("sim", demo);
    el("flightConfirmTitle").textContent = demo ? "Fly the simulated mission?" : "Are you sure you want to fly?";
    el("flightConfirmText").textContent = `${fromDrone ? "The mission stored on the drone" : plan?.exports?.filename || "HERON mission"}`
      + ". HERON downloads it again and re-runs launch checks before arming.";
    el("cWp").textContent = summary ? String(summary.n_wp) : "—";
    el("cAlt").textContent = summary ? `${Math.round(summary.max_alt)} m` : "—";
    el("cLen").textContent = summary ? fmtDist(summary.length_m) : "—";
    el("cTime").textContent = summary ? `≈${fmtTime(summary.est_s)}` : "—";
    el("cTakeoff").textContent = summary ? `Take off to ${Math.round(summary.takeoff_alt)} m above Home` : "Take off";
    el("cFly").textContent = summary ? `Fly all ${summary.n_wp} waypoints in AUTO` : "Fly every waypoint in AUTO";
    holdReset();
    el("flightConfirmLaunch").disabled = false;
    el("flightConfirm").showModal();
  };
  el("flightCancelLaunch").onclick = () => el("flightConfirm").close();
  el("flightConfirm").addEventListener("close", () => {
    holdCancel();
    confirmingPlan = null;
  });

  // Hold-to-fly: the launch only goes out after an uninterrupted 3 s press.
  const HOLD_MS = 3000;
  const RING = 2 * Math.PI * 19;
  el("holdRing").style.strokeDasharray = String(RING);

  function holdReset() {
    holdDone = false;
    el("holdRing").style.strokeDashoffset = String(RING);
    el("flightConfirmLaunch").classList.remove("holding", "done");
    el("holdLabel").textContent = "Hold to fly";
  }
  function holdCancel() {
    if (!hold) return;
    cancelAnimationFrame(hold.raf);
    hold = null;
    if (!holdDone) holdReset();
  }
  function holdTick() {
    if (!hold) return;
    const elapsed = performance.now() - hold.t0;
    const p = Math.min(1, elapsed / HOLD_MS);
    el("holdRing").style.strokeDashoffset = String(RING * (1 - p));
    el("holdLabel").textContent = p < 1 ? `Keep holding… ${Math.ceil((HOLD_MS - elapsed) / 1000)}` : "Launching";
    if (p >= 1) return holdComplete();
    hold.raf = requestAnimationFrame(holdTick);
  }
  function holdStart(event) {
    const button = el("flightConfirmLaunch");
    if (hold || holdDone || button.disabled) return;
    event.preventDefault();
    hold = { t0: performance.now(), raf: 0 };
    button.classList.add("holding");
    navigator.vibrate?.(15);
    holdTick();
  }
  function holdComplete() {
    hold = null;
    holdDone = true;
    el("flightConfirmLaunch").classList.remove("holding");
    el("flightConfirmLaunch").classList.add("done");
    navigator.vibrate?.([30, 40, 30]);
    const pending = confirmingPlan;
    const name = pending?.name;
    const stillValid = pending && pending.id === missionId(name) && active() === name && !el("flightLaunch").disabled;
    setTimeout(() => el("flightConfirm").close(), 250);
    if (!stillValid) {
      message("Mission or aircraft state changed. Review launch checks again.", true);
      return;
    }
    action(name, "launch", { plan_id: pending.id, confirmation: "LAUNCH" });
  }
  const holdButton = el("flightConfirmLaunch");
  holdButton.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    holdButton.setPointerCapture(event.pointerId);
    holdStart(event);
  });
  ["pointerup", "pointercancel", "lostpointercapture"].forEach((type) => holdButton.addEventListener(type, holdCancel));
  holdButton.addEventListener("keydown", (event) => {
    if ((event.key === " " || event.key === "Enter") && !event.repeat) holdStart(event);
    else if (event.key === " " || event.key === "Enter") event.preventDefault();
  });
  holdButton.addEventListener("keyup", (event) => {
    if (event.key === " " || event.key === "Enter") holdCancel();
  });
  holdButton.addEventListener("blur", holdCancel);
  holdButton.addEventListener("click", (event) => event.preventDefault());
  holdButton.addEventListener("contextmenu", (event) => event.preventDefault());

  L.DomEvent.disableClickPropagation(el("deck"));
  L.DomEvent.disableScrollPropagation(el("deck"));
  setView(location.hash.slice(1) || "plan");
  syncConnFields();
  setInterval(schedule, 100);
  setInterval(render, 1000); // Expire displayed data even if an HTTP poll hangs.
})();
