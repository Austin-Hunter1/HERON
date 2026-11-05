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
      launchAt: 0, nextReflect: 0, reflecting: false, reflectSeq: 0, liveSpecs: null,
      groundLL: null, tookOff: false,
    };
  }
  const services = { flight: service("#ffb13b"), sim: service("#ffb13b") };
  const liveLayer = L.layerGroup();
  const liveParts = new Map();
  const pfd = new window.HeronInstruments.PFD(el("pfd"));
  const hsi = new window.HeronInstruments.HSI(el("hsi"));
  let view = "plan";
  let plan = null;
  let planId = null;
  let confirmingPlan = null;
  let following = false;
  let showTrail = true;
  let showReflections = true;
  let portsKey = "";
  let eventsKey = "";
  let checksKey = "";
  let logFilter = "all";
  let wasLiveAttached = false;
  let toastTimer = null;
  let countdown = null;
  let launchSent = false;
  let waiting = false;
  let holding = false;
  let holdFrame = 0;

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
    if (next !== view) cancelWait();
    view = next;
    document.body.dataset.view = next;
    document.querySelectorAll(".ab-tabs [data-view]").forEach((tab) => {
      tab.setAttribute("aria-selected", String(tab.dataset.view === next));
    });
    const flying = next !== "plan";
    if (flying) stopPlay();
    // Predicted playback and its colored history stay on Plan. Live reflections are drawn separately.
    if (!flying) {
      clearLiveSpecs();
      if (map.hasLayer(liveLayer)) map.removeLayer(liveLayer);
    }
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
    if (!flying && typeof renderMixed === "function" && lastPlan) renderMixed(lastPlan, playT);
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
    // Only a mission the pilot just read. A remembered download must not appear on its own.
    const show = s.source === "aircraft" && onboard && onboard.path.length;
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
    if (roi && !target.returning) {
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
        html: `<i></i><b>${target.returning ? "Home" : target.wp ? `WP${target.wp}` : "Next"}</b>`,
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

  /** The pad becomes the spot the aircraft was sitting when it took off. */
  function syncPadToTakeoff(tel, connected) {
    if (!connected || !tel.position || typeof movePadTo !== "function") return;
    const s = services.flight;
    const alt = tel.relative_alt;
    const low = finite(alt) && alt < 2;
    const up = !!tel.armed && finite(alt) && alt >= 2;
    if (!up && (low || tel.landed === 1)) {
      s.groundLL = { lat: tel.position.lat, lon: tel.position.lon };
      if (tel.armed === false) s.tookOff = false;
    }
    if (!up || s.tookOff) return;
    const spot = s.groundLL || tel.position;
    s.tookOff = true;
    movePadTo(spot.lat, spot.lon);
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
    const fixName = { 3: "3D", 4: "DGPS", 5: "RTK Float", 6: "RTK Fixed" }[tel.gps_fix] || "No fix";
    gps.textContent = connected && tel.fresh?.gps ? `${fixName} · ${tel.satellites}` : "—";
    gps.className = connected && tel.fresh?.gps ? (tel.gps_fix >= 3 && tel.satellites >= 6 ? "good" : "warn") : "";
    const batt = el("abBatt");
    const pct = connected && tel.fresh?.battery && tel.battery_pct >= 0 ? tel.battery_pct : null;
    batt.textContent = pct == null ? "—" : `${pct}%${finite(tel.battery_voltage) ? ` · ${tel.battery_voltage.toFixed(1)} V` : ""}`;
    batt.className = pct == null ? "" : pct < 30 ? "bad" : pct < 50 ? "warn" : "good";
    const amps = pct != null && finite(tel.battery_current) ? tel.battery_current : null;
    el("abCurrent").textContent = amps == null ? "—" : `${amps.toFixed(1)} A`;
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
    if (waiting && !flyable) {
      waiting = false;
      confirmingPlan = null;
      stopCountdown();
      resetHold();
      message("Launch checks changed. Hold Fly Mission again.", true);
    }
    if (holding && !flyable) resetHold();
    const launchBtn = el("flightLaunch");
    launchBtn.disabled = !flyable;
    launchBtn.classList.toggle("ready", flyable && !launching);
    launchBtn.classList.toggle("sim", name === "sim");
    const when = plannedStart(name);
    const future = when && when.getTime() - Date.now() > 1000;
    const dock = connected && hasMission && !unflyable && (!tel.armed || launching);
    const face = !dock ? "none" : waiting ? "wait" : "fly";
    launchBtn.classList.toggle("hidden", face !== "fly");
    el("flightWait").classList.toggle("hidden", face !== "wait");
    el("flightWait").classList.toggle("sim", name === "sim");
    const clock = future ? clockParts(when) : null;
    el("flyHint").textContent = launching ? st.job.message
      : !connected ? "No aircraft link"
        : tel.armed ? "Aircraft is armed"
          : !id ? "Load a mission first"
            : !verified ? "Write or read the mission first"
              : !ready ? `${failing} launch check${failing === 1 ? "" : "s"} still failing`
                : clock ? `${clock.utc} UTC · ${clock.local} ${clock.tz}`
                  : "Hold to take off";

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
      const climbing = !!target.taking_off;
      const returning = !!target.returning;
      frac = returning ? 1 : climbing ? 0 : target.total ? Math.max(0, target.wp - 1) / target.total : 0;
      text = returning ? "Returning home" : climbing || !target.wp ? "Taking off" : `Waypoint ${target.wp} of ${target.total}`;
      const aim = returning && tel.home ? tel.home : target;
      if (tel.position && !climbing) {
        const d = meters(tel.position, aim);
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
      ? (target.returning
        ? `Home${pos ? ` · ${fmtDist(meters(tel.position, tel.home || target))}` : ""}`
        : target.taking_off
        ? "Takeoff"
        : `WP ${target.seq}${pos ? ` · ${fmtDist(meters(tel.position, target))}` : ""}`)
      : connected && tel.mission_current != null ? `Item ${tel.mission_current}` : "—";
    const inferredGround = connected && tel.armed === false && tel.fresh?.position
      && finite(tel.relative_alt) && tel.relative_alt > -5 && tel.relative_alt < 3
      && Math.abs(tel.vertical_speed || 0) < 1;
    el("tLanded").textContent = connected && tel.fresh?.landed
      ? LANDED[tel.landed] || "Unknown"
      : inferredGround ? "On ground" : "—";
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

  function clearLiveSpecs() {
    liveParts.forEach((part) => {
      liveLayer.removeLayer(part.outline);
      liveLayer.removeLayer(part.fill);
      part.labels.forEach((marker) => liveLayer.removeLayer(marker));
      if (part.ray) liveLayer.removeLayer(part.ray);
    });
    liveParts.clear();
  }

  /** The satellite the current waypoint is tracking, when the plan recorded one. */
  function aimPrn(name) {
    const hovers = plan?.hovers || [];
    if (!hovers.length) return null;
    const target = services[name].state?.target;
    const index = !target || target.taking_off ? 0 : Math.max(0, (target.wp || 1) - 1);
    return hovers[index]?.prn || hovers[0].prn || null;
  }

  function paintLiveSpecs(speculars, drone) {
    const seen = new Set();
    for (const s of speculars || []) {
      if (typeof prnEnabled === "function" && !prnEnabled(s.prn)) continue;
      seen.add(s.prn);
      const tracked = typeof areaTrackPrns === "function" ? areaTrackPrns(plan) : new Set();
      const aimed = !!s.aimed || tracked.has(s.prn);
      const onWater = !!s.on_water && !aimed;
      const hit = aimed ? "aim" : onWater;
      const col = aimed ? "#ffb000" : onWater ? "#ffe66a" : "#6b7584";
      const ring = fresnelRing(s.lat, s.lon, s.fresnel_along_m, s.fresnel_across_m, s.az);
      const tip = `${s.prn}  ${Number(s.fresnel_along_m).toFixed(0)}×${Number(s.fresnel_across_m).toFixed(0)} m  el ${Number(s.el).toFixed(0)}°`;
      let part = liveParts.get(s.prn);
      if (!part) {
        const outline = L.polygon(ring, {
          color: "#000", weight: hit ? 6 : 5, fillOpacity: 0, opacity: 0.55, interactive: false,
        }).addTo(liveLayer);
        const fill = L.polygon(ring, {
          color: col, weight: hit ? 3.5 : 2.5, fillColor: col,
          fillOpacity: hit === "aim" ? 0.55 : hit ? 0.32 : 0.18,
        }).bindTooltip(tip, { sticky: true }).addTo(liveLayer);
        const labels = [L.marker([s.lat, s.lon], {
          icon: prnLabIcon(s.prn, hit), interactive: false, zIndexOffset: 800,
        }).addTo(liveLayer)];
        const ray = L.polyline([[drone.lat, drone.lon], [s.lat, s.lon]], {
          color: col, weight: hit ? 2.5 : 1.5, opacity: hit ? 0.9 : 0.45,
          dashArray: hit ? null : "4 4", interactive: false,
        }).addTo(liveLayer);
        part = { outline, fill, labels, ray };
        liveParts.set(s.prn, part);
      } else {
        part.outline.setLatLngs(ring);
        part.fill.setLatLngs(ring);
        part.outline.setStyle({ weight: hit ? 6 : 5 });
        part.fill.setStyle({
          color: col, fillColor: col, weight: hit ? 3.5 : 2.5,
          fillOpacity: hit === "aim" ? 0.55 : hit ? 0.32 : 0.18,
        });
        part.fill.setTooltipContent(tip);
        part.labels[0].setLatLng([s.lat, s.lon]);
        part.labels[0].setIcon(prnLabIcon(s.prn, hit));
        part.ray.setLatLngs([[drone.lat, drone.lon], [s.lat, s.lon]]);
        part.ray.setStyle({
          color: col, weight: hit ? 2.5 : 1.5, opacity: hit ? 0.9 : 0.45,
          dashArray: hit ? null : "4 4",
        });
      }
    }
    for (const prn of [...liveParts.keys()]) {
      if (seen.has(prn)) continue;
      const part = liveParts.get(prn);
      liveLayer.removeLayer(part.outline);
      liveLayer.removeLayer(part.fill);
      part.labels.forEach((marker) => liveLayer.removeLayer(marker));
      if (part.ray) liveLayer.removeLayer(part.ray);
      liveParts.delete(prn);
    }
  }

  function pullReflections(name, tel) {
    const s = services[name];
    if (s.reflecting || Date.now() < s.nextReflect) return;
    s.reflecting = true;
    const seq = ++s.reflectSeq;
    const body = {
      lat: tel.position.lat,
      lon: tel.position.lon,
      alt: tel.relative_alt,
      mask: document.getElementById("mask")?.value || plan?.meta?.elev_mask,
      constellations: (typeof selectedConsts === "function" && selectedConsts().length)
        ? selectedConsts()
        : plan?.meta?.constellations,
      targets: plan?.targets || null,
      aim: aimPrn(name),
    };
    fetch("/api/reflections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then((response) => response.json().then((data) => ({ ok: response.ok, data })))
      .then(({ ok, data }) => {
        if (!ok || seq !== s.reflectSeq || view === "plan" || active() !== name) return;
        const consts = new Set(body.constellations || []);
        const above = (data.sats || []).filter((row) => row.above_mask && (!consts.size || consts.has(row.constellation)));
        s.liveSpecs = {
          speculars: (data.speculars || []).filter((sp) => !consts.size || consts.has(sp.constellation)),
          lat: body.lat, lon: body.lon, alt: body.alt,
        };
        if (typeof paintSats === "function" && above.length) {
          const aim = aimPrn(name);
          const tracked = typeof areaTrackPrns === "function" ? areaTrackPrns(plan) : new Set();
          paintSats(above.map((row) => ({
            ...row,
            aimed: (!!aim && row.sid === aim) || tracked.has(row.sid),
            on_water: !!row.on_water && row.sid !== aim && !tracked.has(row.sid),
          })), new Set(), { lat: body.lat, lng: body.lon });
        }
        placeLiveSpecs(name, s.state?.telemetry || tel);
      })
      .catch(() => {})
      .finally(() => {
        s.reflecting = false;
        // Match the live telemetry poll so a new sky sample is ready each marker move.
        s.nextReflect = Date.now() + 250;
      });
  }

  /**
   * Draw the last reflections at where the aircraft is now, not where it was when they were asked for.
   * Over a second the sky barely moves, so each point keeps its offset from the aircraft. The offset
   * is h / tan(el), so it scales with height; the Fresnel zone scales with the square root of height.
   */
  function placeLiveSpecs(name, tel) {
    const live = services[name].liveSpecs;
    if (!live || !tel?.position || !finite(tel.relative_alt) || live.alt <= 0) return;
    const k = Math.max(0, tel.relative_alt) / live.alt;
    const mLat = 111132.92;
    const mLon = mLat * Math.cos((live.lat * Math.PI) / 180);
    const moved = live.speculars.map((sp) => {
      const [lat, lon] = destPoint(tel.position.lat, tel.position.lon,
        (sp.lat - live.lat) * mLat * k, (sp.lon - live.lon) * mLon * k);
      return { ...sp, lat, lon,
        fresnel_along_m: sp.fresnel_along_m * Math.sqrt(k), fresnel_across_m: sp.fresnel_across_m * Math.sqrt(k) };
    });
    paintLiveSpecs(moved, tel.position);
  }

  /** Reflections start at the real launch and use the current clock, not the planned takeoff time. */
  function syncLiveReflections(name, tel, connected) {
    const s = services[name];
    const shown = view !== "plan" && active() === name;
    if (connected && tel.armed && (tel.mode === "AUTO" || tel.mode === "RTL" || tel.mode === "LAND")) {
      if (!s.launchAt) {
        s.launchAt = Date.now();
        s.nextReflect = 0;
      }
    }
    if (connected && tel.fresh?.landed && !tel.armed && tel.landed === 1) s.launchAt = 0;
    const tracking = shown && showReflections && connected && tel.position && finite(tel.relative_alt);
    if (!tracking) {
      s.liveSpecs = null;
      if (shown) clearLiveSpecs();
      if (map.hasLayer(liveLayer)) map.removeLayer(liveLayer);
      return;
    }
    if (!map.hasLayer(liveLayer)) liveLayer.addTo(map);
    placeLiveSpecs(name, tel);
    pullReflections(name, tel);
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
      if (other === "flight") syncPadToTakeoff(otel, !!otel.connected && Date.now() - os.at < 4000);
      paintOnboard(other);
      paintActive(other);
    }
    if (typeof setFlightMapChrome === "function") {
      const showingRead = view !== "plan" && services[name].source === "aircraft";
      setFlightMapChrome(!showingRead);
    }
    syncLiveReflections(name, tel, connected);
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
    cancelWait();
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
    if (event.key === "Escape") {
      closePopover();
      if (waiting) {
        cancelWait();
        render();
      }
    }
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
    cancelWait();
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
  el("flightReflections").onclick = () => {
    showReflections = !showReflections;
    const button = el("flightReflections");
    button.setAttribute("aria-pressed", String(showReflections));
    button.title = showReflections ? "Hide live reflections" : "Show live reflections";
    button.setAttribute("aria-label", button.title);
    if (!showReflections) {
      clearLiveSpecs();
      if (map.hasLayer(liveLayer)) map.removeLayer(liveLayer);
    }
    render();
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

  function plannedStart(name) {
    const id = missionId(name);
    const state = services[name].state;
    // Reading a known HERON mission back must preserve its schedule.
    const raw = id && id === planId && plan?.meta?.start
      || (state?.mission?.plan_id === id ? state.mission.start : null)
      || (state?.onboard?.plan_id === id ? state.onboard.start : null);
    if (!raw) return null;
    // The planner treats timestamps without a zone as UTC.
    const iso = String(raw);
    const when = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`);
    return Number.isNaN(when.getTime()) ? null : when;
  }
  function clockParts(when) {
    const utc = when.toLocaleTimeString("en-GB", { timeZone: "UTC", hour: "2-digit", minute: "2-digit" });
    const local = when.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    const tz = new Intl.DateTimeFormat("en-GB", { timeZoneName: "short" })
      .formatToParts(when).find((part) => part.type === "timeZoneName")?.value || "Local";
    return { utc, local, tz };
  }
  function remainText(ms) {
    const s = Math.max(0, Math.ceil(ms / 1000));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const clock = `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    return h ? `${String(h).padStart(2, "0")}:${clock}` : clock;
  }
  const HOLD_MS = 900;
  function paintHold(p) {
    const ring = el("flyRing");
    if (p <= 0) {
      ring.style.strokeDasharray = "";
      ring.style.strokeDashoffset = "";
      return;
    }
    ring.style.strokeDasharray = "100";
    ring.style.strokeDashoffset = String((1 - p) * 100);
  }
  function resetHold() {
    holding = false;
    if (holdFrame) cancelAnimationFrame(holdFrame);
    holdFrame = 0;
    paintHold(0);
    el("flightLaunch").classList.remove("holding");
  }
  function stopCountdown() {
    if (countdown) clearTimeout(countdown);
    countdown = null;
  }
  function cancelWait() {
    waiting = false;
    confirmingPlan = null;
    stopCountdown();
    resetHold();
  }
  function startCountdown(when) {
    waiting = true;
    resetHold();
    const clock = clockParts(when);
    el("waitClock").textContent = `${clock.utc} UTC · ${clock.local} ${clock.tz}`;
    const tick = () => {
      const left = when.getTime() - Date.now();
      el("countdownValue").textContent = remainText(left);
      el("countdownValue").classList.toggle("long", remainText(left).length > 5);
      if (left <= 0) return sendLaunch();
      countdown = setTimeout(tick, 200);
    };
    tick();
    render();
    el("flightCancelLaunch").focus({ preventScroll: true });
  }
  function sendLaunch() {
    if (launchSent) return;
    const pending = confirmingPlan;
    const name = pending?.name;
    const stillValid = pending && pending.id === missionId(name) && active() === name && !el("flightLaunch").disabled;
    waiting = false;
    stopCountdown();
    resetHold();
    confirmingPlan = null;
    if (!stillValid) {
      launchSent = false;
      message("Mission or aircraft state changed. Hold Fly Mission again.", true);
      render();
      return;
    }
    launchSent = true;
    action(name, "launch", { plan_id: pending.id, confirmation: "LAUNCH", wait_until_start: false })
      .finally(() => { launchSent = false; });
  }
  function confirmHold() {
    const name = active();
    const id = missionId(name);
    if (!id || el("flightLaunch").disabled || waiting || launchSent) {
      resetHold();
      return;
    }
    const when = plannedStart(name);
    confirmingPlan = { name, id, plannedAt: when };
    navigator.vibrate?.([18, 24, 18]);
    if (when && when.getTime() - Date.now() > 1000) {
      startCountdown(when);
      return;
    }
    sendLaunch();
  }
  function beginHold(event) {
    const launch = el("flightLaunch");
    if (holding || launch.disabled || launch.classList.contains("hidden") || waiting) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    event.preventDefault();
    holding = true;
    launch.classList.add("holding");
    if (event.pointerId != null) launch.setPointerCapture(event.pointerId);
    const started = performance.now();
    const frame = (now) => {
      if (!holding) return;
      const p = Math.min(1, (now - started) / HOLD_MS);
      paintHold(p);
      if (p >= 1) {
        holding = false;
        confirmHold();
        return;
      }
      holdFrame = requestAnimationFrame(frame);
    };
    holdFrame = requestAnimationFrame(frame);
  }
  const launch = el("flightLaunch");
  launch.addEventListener("pointerdown", beginHold);
  launch.addEventListener("pointerup", resetHold);
  launch.addEventListener("pointercancel", resetHold);
  launch.addEventListener("lostpointercapture", () => { if (holding) resetHold(); });
  launch.addEventListener("keydown", (event) => {
    if (event.repeat || (event.key !== " " && event.key !== "Enter")) return;
    event.preventDefault();
    beginHold(event);
  });
  launch.addEventListener("keyup", (event) => {
    if (event.key === " " || event.key === "Enter") resetHold();
  });
  launch.addEventListener("contextmenu", (event) => event.preventDefault());
  el("flightSkipWait").onclick = () => sendLaunch();
  el("flightCancelLaunch").onclick = () => {
    cancelWait();
    render();
  };

  L.DomEvent.disableClickPropagation(el("deck"));
  L.DomEvent.disableScrollPropagation(el("deck"));
  setView(location.hash.slice(1) || "plan");
  syncConnFields();
  setInterval(schedule, 100);
  setInterval(render, 1000); // Expire displayed data even if an HTTP poll hangs.
})();

