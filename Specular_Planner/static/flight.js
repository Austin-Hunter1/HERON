/* Live flight is deliberately separate from the planner's predicted playback. */
(() => {
  "use strict";
  window.addEventListener("error", event => console.error("Flight UI error:", event.message));
  window.addEventListener("unhandledrejection", event => console.error("Flight UI request:", String(event.reason)));
  const el = (id) => document.getElementById(id);
  let token = "", planId = null, plan = null, state = null, pending = false, polling = false;
  let lastJobMessage = "", lastServerAt = 0, confirmingPlan = null;
  let aircraft = null, actualHome = null, wasAttached = false;
  let following = false, lastEventsKey = "";
  const icons = {
    mode: '<path d="M3 16V8l6 8V8l6 8V8l6 8V8"/>',
    satellite: '<path d="m8 4 4 4-4 4-4-4z m8 8 4 4-4 4-4-4z M11 13l-2 2 M7 17l-3 3 M12 4l8 8 M14 2l8 8"/>',
    battery: '<rect x="2" y="6" width="18" height="12" rx="2"/><path d="M22 10v4 M6 10v4 M10 10v4 M14 10v4"/>',
    link: '<path d="M4 17v3 M9 12v8 M14 7v13 M19 3v17"/>',
    message: '<path d="M4 4h16v12H9l-5 4z M8 8h8 M8 12h5"/>',
    close: '<path d="m6 6 12 12 M6 18 18 6"/>',
    locate: '<circle cx="12" cy="12" r="6"/><path d="M12 2v4 M12 18v4 M2 12h4 M18 12h4"/><circle cx="12" cy="12" r="1"/>',
    follow: '<path d="m12 3 8 18-8-5-8 5z"/>',
    route: '<path d="M5 5h11a4 4 0 0 1 0 8H8a3 3 0 0 0 0 6h11"/><circle cx="4" cy="5" r="2"/><path d="m17 16 3 3-3 3"/>',
    home: '<path d="m3 11 9-8 9 8 M6 10v11h12V10 M10 21v-7h4v7"/>',
    upload: '<path d="M12 16V3 M7 8l5-5 5 5 M4 16v5h16v-5"/>',
    takeoff: '<path d="M12 18V3 M7 8l5-5 5 5 M4 18v3h16v-3"/>',
  };
  document.querySelectorAll("[data-icon]").forEach(node => {
    node.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[node.dataset.icon] || ""}</svg>`;
  });
  for (let angle = -180; angle <= 540; angle += 15) {
    const tick = document.createElement("span");
    const normalized = (angle + 360) % 360;
    tick.textContent = ({0: "N", 90: "E", 180: "S", 270: "W"})[normalized] || String(normalized).padStart(3, "0");
    tick.style.left = `${angle * 2}px`;
    el("headingTape").append(tick);
  }
  function openMessages(open) {
    el("flightMessages").classList.toggle("hidden", !open);
    el("flightMessagesToggle").setAttribute("aria-expanded", String(open));
    if (open) el("flightMessageFilter").focus();
  }
  el("flightMessagesToggle").onclick = () => openMessages(el("flightMessages").classList.contains("hidden"));
  el("flightMessagesClose").onclick = () => openMessages(false);
  el("flightMessageFilter").oninput = () => { lastEventsKey = ""; render(); };
  document.addEventListener("keydown", e => { if (e.key === "Escape") openMessages(false); });
  el("flightReadiness").onclick = () => {
    if (el("flightPanel").classList.contains("hidden")) el("flightPanelToggle").click();
    el("flightPreflight").open = true;
    el("flightPreflight").scrollIntoView({block: "nearest", behavior: "smooth"});
  };
  el("flightFollow").onclick = () => {
    following = !following;
    el("flightFollow").setAttribute("aria-pressed", String(following));
    render();
  };
  map.on("dragstart", () => { following = false; el("flightFollow").setAttribute("aria-pressed", "false"); });
  const track = L.polyline([], { color: "#087b71", weight: 3, dashArray: "4 6" }).addTo(map);
  let trackPts = [], trackGeneration = null;
  el("flightTrack").onclick = () => {
    const show = !map.hasLayer(track);
    if (show) track.addTo(map); else map.removeLayer(track);
    el("flightTrack").setAttribute("aria-pressed", String(show));
  };
  document.body.classList.add("flight-panel-open");
  el("flightPanelToggle").onclick = () => {
    const open = !document.body.classList.contains("flight-panel-open");
    document.body.classList.toggle("flight-panel-open", open);
    el("flightPanel").classList.toggle("hidden", !open);
    el("flightPanelToggle").setAttribute("aria-expanded", String(open));
    el("flightPanelToggle").textContent = open ? "Hide aircraft panel" : "Aircraft connection";
  };

  function message(text, error = false) {
    el("flightMessage").textContent = text;
    el("flightMessage").classList.toggle("error", error);
  }

  async function api(path, body) {
    if (body !== undefined && !token) {
      const session = await api("session");
      token = session.token;
    }
    const response = await fetch(`/api/flight/${path}`, {
      cache: "no-store",
      ...(body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-Flight-Token": token }, body: JSON.stringify(body) }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Aircraft service returned ${response.status}`);
    return data;
  }

  function invalidate() {
    planId = null;
    confirmingPlan = null;
    if (el("flightConfirm").open) el("flightConfirm").close();
    el("flightPlanNote").textContent = "Plan changed. Build the mission again before upload or launch.";
    render();
  }
  window.addEventListener("heron:plan-invalid", invalidate);
  window.addEventListener("heron:plan-ready", (event) => {
    plan = event.detail;
    planId = plan.flight_plan_id || null;
    el("flightPlanNote").textContent = `${plan.exports.filename} · ${plan.meta.h_agl} m above Home · planned ${(plan.meta.start || "").slice(0, 16).replace("T", " ")} UTC. Launch starts now; it does not wait for the planned time.`;
    render();
    poll();
  });
  el("form").addEventListener("input", invalidate);
  el("form").addEventListener("change", invalidate);
  ["btnUndoWp", "btnClearWp", "btnClearSurvey", "btnClear", "btnUndo", "btnDraw"].forEach(id => el(id)?.addEventListener("click", invalidate));
  map.on("click", invalidate);
  // The default pad is created asynchronously by the planner.
  function bindPad() {
    if (typeof padMarker !== "undefined" && padMarker) {
      padMarker.on("dragstart", invalidate);
    } else setTimeout(bindPad, 250);
  }
  bindPad();

  function render() {
    const s = state?.telemetry || {};
    const serviceFresh = Date.now() - lastServerAt < 4000;
    const connected = !!s.connected && serviceFresh;
    const attached = !!s.transport;
    const busy = pending || state?.job.state === "running";
    const disarmed = connected && s.armed === false;
    const demo = s.transport === "demo";
    const badge = el("flightBadge");
    badge.textContent = !attached ? "Disconnected" : !connected ? "Link lost" : demo ? "SIMULATED" : "LIVE";
    badge.className = `flight-badge ${attached ? (!connected ? "stale" : demo ? "demo" : "connected") : ""}`;
    el("flightTitle").textContent = !attached ? "Connect & fly" : demo ? "Demo aircraft" : `Aircraft ${s.system_id}`;
    document.body.classList.toggle("aircraft-connected", attached);
    el("flightStatusBar").classList.toggle("hidden", !attached);
    el("flightInstruments").classList.toggle("hidden", !attached);
    if (!attached) { openMessages(false); following = false; el("flightFollow").setAttribute("aria-pressed", "false"); }
    if (attached && !wasAttached) {
      el("flightConnection").open = false;
      el("flightPanel").append(el("flightConnection"));
      stopPlay();
      map.removeLayer(droneLayer);
    } else if (!attached && wasAttached) {
      el("flightInstruments").after(el("flightConnection"));
      el("flightConnection").open = true;
      droneLayer.addTo(map);
    }
    wasAttached = attached;
    el("flightConnect").disabled = attached || busy;
    el("flightDisconnect").disabled = !attached || busy;
    el("flightTransport").disabled = attached || busy;
    el("flightRefresh").disabled = !connected || busy;
    el("flightUpload").disabled = !disarmed || busy || !planId;
    el("flightLaunch").disabled = !disarmed || busy || !planId || !state?.preflight.ready || state?.verified?.plan_id !== planId;
    el("flightRtl").disabled = !connected;
    el("flightQuickRtl").disabled = !connected;
    el("flightQuickRtl").classList.toggle("hidden", !attached);
    el("flightUseHome").disabled = !disarmed || busy || !s.home || !s.fresh?.home;
    el("flightCenter").disabled = !connected || !s.position || !s.fresh?.position;
    el("flightFollow").disabled = el("flightCenter").disabled;
    el("liveMode").textContent = connected ? `${s.mode || "—"}${s.armed ? " • ARMED" : ""}` : "—";
    el("liveBattery").textContent = connected && s.fresh?.battery && s.battery_pct >= 0 ? `${s.battery_pct}%` : "—";
    el("liveBattery").title = s.battery_voltage != null ? `${s.battery_voltage.toFixed(1)} V` : "Voltage unavailable";
    el("liveAlt").textContent = connected && s.fresh?.position && s.relative_alt != null ? `${s.relative_alt.toFixed(1)} m` : "—";
    el("liveGps").textContent = connected && s.fresh?.gps ? `${s.gps_fix >= 3 ? "3D" : "No fix"} / ${s.satellites}` : "—";
    el("liveSpeed").textContent = connected && s.fresh?.position ? `${(s.speed || 0).toFixed(1)} m/s` : "—";
    el("liveMission").textContent = connected && s.mission_current != null ? `${s.mission_current}${state?.verified ? ` / ${state.verified.count - 1}` : ""}` : "—";
    el("liveHeading").textContent = connected && s.fresh?.position && s.heading != null ? `${Math.round(s.heading)}°` : "—";
    el("liveAttitude").textContent = connected && (s.fresh?.attitude || demo) && s.roll != null && s.pitch != null ? `${Math.round(s.roll)}° / ${Math.round(s.pitch)}°` : "—";
    const attitudeFresh = connected && (s.fresh?.attitude || demo) && Number.isFinite(s.roll) && Number.isFinite(s.pitch);
    el("attitudeDisplay").classList.toggle("unavailable", !attitudeFresh);
    el("attitudeDisplay").setAttribute("aria-label", attitudeFresh ? `Roll ${s.roll.toFixed(1)} degrees, pitch ${s.pitch.toFixed(1)} degrees` : "Attitude unavailable");
    el("attitudeWorld").style.transform = attitudeFresh ? `rotate(${-s.roll}deg) translateY(${Math.max(-90, Math.min(90, s.pitch)) * 2}px)` : "none";
    el("headingTape").style.transform = `translateX(${-((connected && s.fresh?.position && s.heading != null) ? s.heading : 0) * 2}px)`;
    el("headingTape").style.visibility = connected && s.fresh?.position && s.heading != null ? "visible" : "hidden";
    el("instrumentSource").textContent = !connected ? "LINK LOST" : demo ? "SIMULATION" : attitudeFresh ? "LIVE TELEMETRY" : "NO ATTITUDE";
    el("liveClimb").textContent = connected && s.fresh?.position && Number.isFinite(s.vertical_speed) ? `${s.vertical_speed > 0 ? "+" : ""}${s.vertical_speed.toFixed(1)} m/s` : "—";
    el("liveHomeDistance").textContent = connected && s.fresh?.position && s.fresh?.home && s.position && s.home ? `${Math.round(map.distance([s.position.lat, s.position.lon], [s.home.lat, s.home.lon]))} m` : "—";
    el("statusMode").textContent = connected ? s.mode || "—" : "—";
    el("statusGps").textContent = el("liveGps").textContent;
    el("statusBattery").textContent = el("liveBattery").textContent;
    el("statusBattery").classList.toggle("low-battery", connected && s.fresh?.battery && s.battery_pct >= 0 && s.battery_pct < 30);
    el("statusLink").textContent = connected ? `${Number(s.heartbeat_age || 0).toFixed(1)} s` : "LOST";
    el("liveReady").textContent = !connected ? "Connection lost" : s.armed ? "Armed" : state?.preflight.ready ? "Ready to launch" : "Not ready to launch";
    el("flightReadiness").dataset.state = !connected ? "lost" : s.armed ? "armed" : state?.preflight.ready ? "ready" : "check";
    el("liveLanded").textContent = connected && s.fresh?.landed ? ({1: "On ground", 2: "In air", 3: "Takeoff", 4: "Landing"}[s.landed] || "Unknown") : "—";
    el("livePosition").textContent = s.position ? `${connected && s.fresh?.position ? "" : "Last known: "}${s.position.lat.toFixed(6)}, ${s.position.lon.toFixed(6)}` : "No aircraft position";
    const checks = state?.preflight.checks || [];
    el("flightCheckSummary").textContent = `Launch checks · ${checks.filter(c => c.ok).length}/${checks.length} passed`;
    el("flightChecks").replaceChildren(...checks.map(c => {
      const li = document.createElement("li");
      li.className = c.ok && connected ? "pass" : "";
      li.textContent = c.message;
      return li;
    }));
    if (state?.job.message && state.job.message !== lastJobMessage) {
      lastJobMessage = state.job.message;
      message(state.job.message, state.job.state === "error");
    }
    if (attached && !connected) message("Aircraft link is stale. Live values are hidden and launch is blocked. Use the RC override if needed.", true);
    const events = state?.events || [];
    el("flightMessageCount").textContent = events.length;
    const eventsKey = JSON.stringify(events) + el("flightMessageFilter").value;
    if (eventsKey !== lastEventsKey) {
      lastEventsKey = eventsKey;
      const filtered = events.filter(event => event.text.toLowerCase().includes(el("flightMessageFilter").value.toLowerCase()));
      el("flightMessagesEmpty").classList.toggle("hidden", filtered.length > 0);
      el("flightMessagesEmpty").textContent = events.length ? "No matching messages." : "No aircraft messages yet.";
      el("flightMessageLog").replaceChildren(...filtered.slice().reverse().map(event => {
        const li = document.createElement("li"), time = document.createElement("time"), content = document.createElement("span");
        time.textContent = new Date(event.time * 1000).toLocaleTimeString();
        content.textContent = event.text;
        li.append(time, content);
        return li;
      }));
    }
    el("flightEvents").replaceChildren(...events.slice(-12).reverse().map(event => {
      const li = document.createElement("li");
      li.textContent = `${new Date(event.time * 1000).toLocaleTimeString()} ${event.text}`;
      return li;
    }));
    if (s.generation !== trackGeneration) {
      trackGeneration = s.generation;
      trackPts = [];
      track.setLatLngs([]);
    }
    if (s.position && attached) {
      const ll = [s.position.lat, s.position.lon];
      const icon = L.divIcon({ className: `aircraft-marker ${demo ? "demo" : ""} ${!connected || !s.fresh?.position ? "stale" : ""}`, iconSize: [22, 22], iconAnchor: [11, 11] });
      if (!aircraft) aircraft = L.marker(ll, { icon }).addTo(map);
      aircraft.setLatLng(ll).setIcon(icon).bindTooltip(demo ? "Simulated aircraft" : "Live aircraft");
      if (following && connected && s.fresh?.position) map.panTo(ll, {animate: false});
      if (connected && s.fresh?.position && (!trackPts.length || map.distance(trackPts.at(-1), ll) > 1)) {
        trackPts.push(ll);
        if (trackPts.length > 2000) trackPts.shift();
        track.setLatLngs(trackPts);
      }
    } else if (aircraft) {
      map.removeLayer(aircraft);
      aircraft = null;
    }
    if (s.home && attached) {
      const ll = [s.home.lat, s.home.lon];
      if (!actualHome) actualHome = L.circleMarker(ll, { color: "#087b71", radius: 9, fillOpacity: 0.2 }).addTo(map);
      actualHome.setLatLng(ll).bindTooltip(demo ? "Simulated Home" : "Aircraft-reported Home");
    } else if (actualHome) {
      map.removeLayer(actualHome);
      actualHome = null;
    }
  }

  async function poll() {
    if (polling) return;
    polling = true;
    const requestedPlan = planId;
    try {
      const next = await api(`status${requestedPlan ? `?plan_id=${encodeURIComponent(requestedPlan)}` : ""}`);
      if (requestedPlan === planId) {
        state = next;
        lastServerAt = Date.now();
      }
    } catch (error) {
      message(`Aircraft service unavailable: ${error.message}`, true);
      lastServerAt = 0;
    } finally {
      polling = false;
      render();
    }
  }

  async function action(path, body = {}) {
    if (pending && path !== "rtl") return;
    pending = true;
    render();
    try {
      const result = await api(path, body);
      if (result.message) message(result.message);
      if (path === "connect") {
        el("flightConnection").open = false;
        el("flightPreflight").open = false;
        await api("refresh", {});
      }
      await poll();
    } catch (error) {
      message(error.message, true);
    } finally {
      pending = false;
      render();
    }
  }

  async function ports() {
    try {
      const data = await api("ports");
      el("flightDevice").replaceChildren(...data.ports.map(p => {
        const option = document.createElement("option");
        option.value = p.device;
        option.textContent = `${p.description} (${p.device})`;
        return option;
      }));
      if (!data.ports.length) message(data.error || "No serial ports found. Plug in the controller or ELRS module, then refresh.");
    } catch (error) { message(error.message, true); }
  }

  el("flightTransport").onchange = () => {
    const kind = el("flightTransport").value;
    el("flightSerial").classList.toggle("hidden", kind !== "serial");
    el("flightNetwork").classList.toggle("hidden", !["udp", "tcp", "herelink_hotspot", "herelink_client"].includes(kind));
    el("flightSysRow").classList.toggle("hidden", kind === "demo");
    el("flightHost").value = kind === "tcp" ? "127.0.0.1" : kind === "herelink_client" ? "192.168.42.129" : "0.0.0.0";
    el("flightPort").value = kind === "tcp" ? "5760" : kind === "herelink_client" ? "14552" : "14550";
    el("flightHostLabel").textContent = kind === "tcp" ? "Simulator address" : kind === "herelink_client" ? "HereLink address" : "Listen address";
    el("flightTransportNote").textContent = {
      demo: "Demo only. No hardware connection; flight and dwell times are accelerated.",
      serial: "Use flight-controller USB on the bench, or a compatible external ELRS module for the field link.",
      udp: "Connect this computer to the ELRS TX Backpack Wi-Fi. Native MAVLink mode must already be configured on the radio and aircraft.",
      tcp: "Connect to a running ArduPilot Copter SITL instance. This option does not start the simulator.",
      herelink_hotspot: "Join the HereLink hotspot on this computer. Listen on UDP 14550 for aircraft telemetry. Video is separate.",
      herelink_client: "USB tether: 192.168.42.129, port 14552. Shared Wi-Fi: enter the controller’s IP address. MAVLink telemetry and missions; video is separate.",
    }[kind];
    if (kind === "serial") ports();
  };
  el("flightPorts").onclick = ports;
  el("flightConnect").onclick = () => action("connect", {
    transport: el("flightTransport").value, device: el("flightDevice").value,
    baud: Number(el("flightBaud").value), host: el("flightHost").value,
    port: Number(el("flightPort").value), system_id: Number(el("flightSystem").value),
  });
  el("flightDisconnect").onclick = () => action("disconnect");
  el("flightRefresh").onclick = () => action("refresh");
  el("flightUpload").onclick = () => action("upload", { plan_id: planId });
  el("flightRtl").onclick = () => {
    el("flightConfirm").close();
    action("rtl");
  };
  el("flightQuickRtl").onclick = el("flightRtl").onclick;
  el("flightUseHome").onclick = () => {
    const home = state?.telemetry.home;
    if (!home || !padMarker) return;
    padMarker.setLatLng([home.lat, home.lon]);
    syncPadWrap();
    paintClickWps();
    invalidate();
    message("Launch pad moved to aircraft Home. Rebuild the mission at this location.");
  };
  el("flightCenter").onclick = () => {
    const p = state?.telemetry.position;
    if (p) map.setView([p.lat, p.lon], 17);
  };
  el("flightLaunch").onclick = () => {
    if (!planId || el("flightLaunch").disabled) return;
    confirmingPlan = planId;
    const demo = state?.telemetry.transport === "demo";
    el("flightConfirmTitle").textContent = demo ? "Launch simulated mission?" : "Launch aircraft now?";
    el("flightConfirmText").textContent = `${demo ? "SIMULATION · " : ""}${plan.exports.filename} · ${plan.meta.h_agl} m above Home · ${plan.meta.n_hovers} survey waypoints. The mission will be checked again before arming.`;
    el("flightConfirm").showModal();
  };
  el("flightCancelLaunch").onclick = () => el("flightConfirm").close();
  el("flightConfirmLaunch").onclick = () => {
    el("flightConfirm").close();
    if (!confirmingPlan || confirmingPlan !== planId || el("flightLaunch").disabled) {
      message("Mission or aircraft state changed. Review launch checks again.", true);
      return;
    }
    action("launch", { plan_id: confirmingPlan, confirmation: "LAUNCH" });
    confirmingPlan = null;
  };
  L.DomEvent.disableClickPropagation(el("flightPanel"));
  L.DomEvent.disableScrollPropagation(el("flightPanel"));
  poll();
  setInterval(poll, 1000);
  setInterval(render, 1000); // Expire displayed data even if an HTTP poll hangs.
})();
