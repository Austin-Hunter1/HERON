/* worker.js - Runs Jack's mission planner (Python) in a Web Worker.
 *
 * Why a worker: Pyodide and the planner take seconds to start and a
 * plan can take a second to compute. In a worker this never freezes
 * the page. bridge.js sends each /api request here as a message; this
 * file passes it to web_backend.handle() (Flask's test client) and
 * sends the answer back.
 *
 * Settings come from planner_config.json, written by build_site.py:
 * the Pyodide URL and version, the packages to load, and the bundle.
 */
"use strict";

let py = null;
const ready = (async () => {
  const cfg = await (await fetch("planner_config.json", { cache: "no-store" })).json();
  const say = (text) => postMessage({ kind: "progress", text });
  say("Loading the Python runtime…");
  importScripts(cfg.pyodide_url + "pyodide.js");
  py = await loadPyodide({ indexURL: cfg.pyodide_url });
  say("Loading numpy and shapely…");
  await py.loadPackage(cfg.packages);
  say("Loading the mission planner…");
  const zip = await (await fetch(cfg.bundle)).arrayBuffer();
  py.unpackArchive(zip, "zip", { extractDir: cfg.root });
  py.runPython(`import sys; sys.path.insert(0, ${JSON.stringify(cfg.root)})`);
  const wb = py.pyimport("web_backend");
  say("Starting the planner…");
  const source = wb.boot(cfg.root);
  // The poll loops (simulated payload) run every 100 ms, as their threads would.
  setInterval(() => { try { wb.tick(); } catch (e) { console.error(e); } }, 100);
  return { wb, source };
})();

ready.then(({ source }) => postMessage({ kind: "ready", source }))
  .catch((e) => postMessage({ kind: "failed", text: String(e && e.message || e) }));

onmessage = async (event) => {
  const { id, method, url, headers, body } = event.data;
  try {
    const { wb } = await ready;
    const out = wb.handle(method, url, headers || {}, body ? new Uint8Array(body) : null);
    const [status, hdrs, data] = out.toJs({ dict_converter: Object.fromEntries });
    out.destroy();
    const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
    postMessage({ kind: "response", id, status, headers: hdrs, body: bytes.buffer }, [bytes.buffer]);
  } catch (e) {
    const text = JSON.stringify({ error: "Web demo error: " + String(e && e.message || e) });
    postMessage({ kind: "response", id, status: 500, headers: { "Content-Type": "application/json" }, body: new TextEncoder().encode(text).buffer });
  }
};
