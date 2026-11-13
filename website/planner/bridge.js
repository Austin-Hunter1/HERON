/* bridge.js - Join Jack's unchanged planner page to the planner in the worker.
 *
 * build_site.py copies Specular_Planner/static/index.html to /sim/ and
 * adds this script in <head>, before the planner's own scripts. This file:
 *
 * 1. Starts worker.js (the real planner, in Python).
 * 2. Replaces window.fetch for /api/... and /overlays/... so the page's
 *    requests go to the worker. Other URLs go to the network as usual.
 * 3. Makes links to /api/... (Download .waypoints, almanac files) work:
 *    a click fetches the file from the worker and saves it.
 * 4. Shows a loading screen until the planner is ready, and a banner
 *    that says this is a simulation.
 */
(function () {
  "use strict";
  const realFetch = window.fetch.bind(window);
  const worker = new Worker("worker.js");
  const pending = new Map();
  let nextId = 1;
  let readyResolve, readyReject;
  const ready = new Promise((res, rej) => { readyResolve = res; readyReject = rej; });

  const isBackend = (path) => path.startsWith("/api/") || path.startsWith("/overlays/");

  worker.onmessage = (e) => {
    const m = e.data;
    if (m.kind === "progress") setLoading(m.text);
    else if (m.kind === "ready") { readyResolve(m.source); doneLoading(m.source); }
    else if (m.kind === "failed") { readyReject(new Error(m.text)); setLoading("The planner could not start: " + m.text, true); }
    else if (m.kind === "response") {
      const p = pending.get(m.id); pending.delete(m.id);
      if (p) p(new Response(m.body, { status: m.status, headers: m.headers }));
    }
  };

  /** Send one request to the worker; resolve with a Response. */
  async function viaWorker(url, init) {
    await ready;
    let body = null;
    if (init && init.body != null) body = typeof init.body === "string" ? new TextEncoder().encode(init.body).buffer : await new Response(init.body).arrayBuffer();
    const headers = {};
    new Headers((init && init.headers) || {}).forEach((v, k) => (headers[k] = v));
    const id = nextId++;
    return new Promise((resolve) => {
      pending.set(id, resolve);
      worker.postMessage({ id, method: ((init && init.method) || "GET").toUpperCase(), url: url.pathname + url.search, headers, body }, body ? [body] : []);
    });
  }

  window.fetch = (input, init) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.origin === location.origin && isBackend(url.pathname)) return viaWorker(url, init);
    return realFetch(input, init);
  };

  // Links to backend files (exports): fetch them from the worker and save them.
  document.addEventListener("click", async (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a) return;
    const url = new URL(a.getAttribute("href"), location.href);
    if (url.origin !== location.origin || !isBackend(url.pathname)) return;
    e.preventDefault();
    const r = await viaWorker(url, { method: "GET" });
    const blob = await r.blob();
    const tmp = document.createElement("a");
    tmp.href = URL.createObjectURL(blob);
    tmp.download = a.getAttribute("download") || url.pathname.split("/").pop();
    document.body.append(tmp); tmp.click(); tmp.remove();
    setTimeout(() => URL.revokeObjectURL(tmp.href), 5000);
  }, true);

  /* ------------------------------------------------------ banner + loading */
  let overlay = null;
  function setLoading(text, failed) {
    const draw = () => {
      if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "web-loading";
        overlay.innerHTML = '<div class="box"><img src="../img/heron-logo.png" alt=""><b>HERON mission planner</b><span class="msg"></span><span class="sub">The real planner runs in your browser. The first visit downloads about 25 MB; later visits are faster.</span></div>';
        document.body.append(overlay);
      }
      overlay.querySelector(".msg").textContent = text;
      overlay.classList.toggle("failed", !!failed);
    };
    if (document.body) draw(); else document.addEventListener("DOMContentLoaded", draw);
  }
  function doneLoading(source) {
    const go = () => {
      if (overlay) overlay.remove();
      const b = document.getElementById("web-banner");
      if (b) b.querySelector(".src").textContent = source;
    };
    if (document.body) go(); else document.addEventListener("DOMContentLoaded", go);
  }
  document.addEventListener("DOMContentLoaded", () => {
    const tpl = document.getElementById("web-banner-tpl");
    if (tpl) {
      document.body.prepend(tpl.content.cloneNode(true));
      // The banner starts as a small tag; a click on the tag opens the text.
      const b = document.getElementById("web-banner");
      b.classList.add("min");
      b.querySelector(".tag").addEventListener("click", () => b.classList.toggle("min"));
    }
    // Simulate tab: SITL and the dry run need the desktop app (a SITL
    // binary, a real flight controller). Offer the quick preview, which
    // runs Jack's DemoLink aircraft in this browser.
    const kind = document.getElementById("simKind");
    if (kind) {
      for (const opt of kind.options) {
        if (opt.value !== "demo") { opt.disabled = true; opt.textContent += " (desktop app only)"; }
      }
      kind.value = "demo";
      kind.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });
  setLoading("Starting…");
  ready.catch(() => {});
})();
