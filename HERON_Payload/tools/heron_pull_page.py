# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6"]
# ///
"""A download page on the laptop: copy NUC recordings into D:\\01_Data\\collects with buttons, not a terminal.

Start it with HERON_Download.bat (double-click), or:  uv run tools/heron_pull_page.py
It opens http://localhost:8770 in the browser. The page lists the sessions on the NUC; "Download" saves one
into the collects folder exactly as tools/heron_pull.py does (one .dat and .yml per channel, metadata.yml
updated, NUC JSON files in nuc/<session>/). Downloads run one at a time, with progress and Cancel.

Why a page on the laptop: a web page from the NUC (plain http://) is not allowed to write into a folder on
this computer. This small server runs here, so it can. It listens on 127.0.0.1 only, so other computers on
the network cannot use it. The NUC address and the collects folder are saved in ~/.heron_pull.json.
"""
import argparse
import json
import queue
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import heron_pull as pull  # noqa: E402

SETTINGS_FILE = Path.home() / ".heron_pull.json"
EXPERIMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,100}$")


class Cancelled(Exception):
    pass


class Puller:
    """Settings, the NUC session list, and a queue of downloads run by one worker thread."""

    def __init__(self, settings_file=SETTINGS_FILE, nuc_factory=pull.Nuc):
        self.settings_file, self.nuc_factory = Path(settings_file), nuc_factory
        self.settings = dict(nuc=pull.DEFAULT_NUC, dest=pull.DEFAULT_DEST)
        try:
            saved = json.loads(self.settings_file.read_text())
            self.settings.update({k: saved[k] for k in ("nuc", "dest") if isinstance(saved.get(k), str)})
        except (OSError, ValueError):
            pass
        self.lock = threading.Lock()
        self.jobs = []                         # newest last; the page shows them all
        self.queue = queue.Queue()
        self.next_id = 1
        threading.Thread(target=self._worker, daemon=True, name="pull-worker").start()

    # ---------------- settings and lists
    def save_settings(self, nuc, dest):
        nuc, dest = str(nuc or "").strip().rstrip("/"), str(dest or "").strip()
        if not re.match(r"^https?://[A-Za-z0-9.\-]+(:\d+)?$", nuc):
            raise ValueError("NUC address looks like http://172.27.16.140:8080")
        if not Path(dest).is_absolute():
            raise ValueError("Collects folder must be a full path, like D:\\01_Data\\collects")
        with self.lock:
            self.settings.update(nuc=nuc, dest=dest)
            self.settings_file.write_text(json.dumps(self.settings, indent=2))

    def sessions(self, include_simulated=False):
        """The NUC's sessions, newest first, with where each one already is on this computer."""
        data = self.nuc_factory(self.settings["nuc"], timeout=15).get_json("/api/recordings")
        rows = []
        for s in data["sessions"]:
            if s.get("simulated") and not include_simulated:
                continue
            rows.append(dict(name=s["name"], setup=s.get("setup"), seconds=s.get("seconds"), bytes=s.get("bytes"),
                             complete=s.get("complete"), dropped=(s.get("overflows") or 0) + (s.get("gaps") or 0),
                             simulated=bool(s.get("simulated")), channels=sum(len(r["channels"]) for r in s["radios"]),
                             default_experiment=pull.default_experiment(s["name"]),
                             pulled_into=pull.pulled_into(self.settings["dest"], s["name"])))
        return rows

    def experiments(self):
        root = Path(self.settings["dest"])
        return sorted((p.name for p in root.iterdir() if p.is_dir()), reverse=True) if root.is_dir() else []

    # ---------------- jobs
    def add(self, session, experiment):
        if not isinstance(session, str) or not re.match(r"^HERON_\d{8}_\d{6}(_\d+)?$", session):
            raise ValueError("Unknown session name.")
        experiment = str(experiment or pull.default_experiment(session)).strip()
        if not EXPERIMENT_RE.match(experiment):
            raise ValueError("Experiment folder names use letters, digits, _ . and - only.")
        with self.lock:
            if any(j["session"] == session and j["state"] in ("queued", "running") for j in self.jobs):
                raise ValueError(session + " is already queued.")
            job = dict(id=self.next_id, session=session, experiment=experiment, state="queued", done=0, total=None,
                       message="Waiting", collects=[], started=None, finished=None, cancel=False,
                       dest=self.settings["dest"], nuc=self.settings["nuc"])
            self.next_id += 1
            self.jobs.append(job)
        self.queue.put(job)
        return self.public(job)

    def cancel(self, job_id):
        with self.lock:
            for j in self.jobs:
                if j["id"] == job_id and j["state"] in ("queued", "running"):
                    j["cancel"] = True
                    if j["state"] == "queued":
                        j.update(state="cancelled", message="Cancelled before it started")

    def public(self, job):
        out = {k: v for k, v in job.items() if k != "cancel"}
        if job["started"] and job["state"] == "running":
            elapsed = max(time.monotonic() - job["started"], 1e-3)
            out["mb_per_s"] = round(job["done"] / elapsed / 1e6, 1)
        out.pop("started", None)
        out.pop("finished", None)
        return out

    def job_list(self):
        with self.lock:
            return [self.public(j) for j in self.jobs[-30:]]

    def _worker(self):
        while True:
            job = self.queue.get()
            if job["state"] == "cancelled":
                continue
            self._run(job)

    def _run(self, job):
        def set_(**kw):
            with self.lock:
                job.update(kw)

        def progress(n):
            with self.lock:
                job["done"] += n
                if job["cancel"]:
                    raise Cancelled()

        set_(state="running", started=time.monotonic(), message="Reading the session list from the NUC")
        try:
            nuc = self.nuc_factory(job["nuc"])
            found = [s for s in nuc.get_json("/api/recordings")["sessions"] if s["name"] == job["session"]]
            if not found:
                raise RuntimeError("The NUC no longer lists " + job["session"])
            session = found[0]
            total = sum(c["bytes"] for r in session["radios"] for c in r["channels"])
            set_(total=total, message="Downloading")
            names = pull.download_session(nuc, session, job["dest"], job["experiment"],
                                          report=lambda m: set_(message=m.split(": ", 1)[-1]), progress=progress)
            folder = str(Path(job["dest"]) / job["experiment"])
            set_(state="done", collects=names, folder=folder,
                 message=f"{len(names)} collect(s) saved" if names else "Already in this experiment folder")
        except Cancelled:
            set_(state="cancelled", message="Cancelled; the half-written file was removed")
        except Exception as exc:
            set_(state="failed", message=str(exc))
        finally:
            set_(finished=time.monotonic())


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>HERON Download</title>
<style>
:root { --ink: #15292b; --muted: #5d7071; --rule: #d2dbd6; --teal: #0b7068; --teal-soft: #e3f2ef; --red: #b3372e;
  --amber: #8a5a00; --bg: #f3f6f4; --panel: #fff; }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.45 system-ui, "Segoe UI", sans-serif; color: var(--ink); background: var(--bg); }
header { padding: 16px 24px; background: var(--panel); border-bottom: 1px solid var(--rule); }
h1 { font-size: 1.25rem; margin: 0 0 2px; } h2 { font-size: 1.05rem; margin: 0 0 10px; }
main { max-width: 1180px; margin: 0 auto; padding: 20px 24px; display: grid; gap: 18px; }
section { background: var(--panel); border: 1px solid var(--rule); border-radius: 8px; padding: 16px; }
.note { color: var(--muted); font-size: .9rem; margin: 4px 0; }
.row { display: flex; gap: 12px; flex-wrap: wrap; align-items: end; }
label { display: grid; gap: 3px; font-size: .85rem; color: var(--muted); }
input { font: inherit; padding: 6px 8px; border: 1px solid var(--rule); border-radius: 5px; min-width: 260px; color: var(--ink); }
button { font: inherit; padding: 6px 14px; border-radius: 5px; border: 1px solid var(--teal); background: #fff; color: var(--teal); cursor: pointer; }
button.primary { background: var(--teal); color: #fff; } button:disabled { opacity: .45; cursor: default; }
table { width: 100%; border-collapse: collapse; font-size: .9rem; }
th { text-align: left; color: var(--muted); font-weight: 600; } th, td { padding: 7px 8px; border-bottom: 1px solid var(--rule); vertical-align: middle; }
td input { min-width: 210px; padding: 4px 6px; }
.tag { font-size: .78rem; font-weight: 600; padding: 1px 8px; border-radius: 10px; background: var(--teal-soft); color: var(--teal); white-space: nowrap; }
.tag.warn { background: #fbefd9; color: var(--amber); } .tag.bad { background: #f8e1dd; color: var(--red); }
.err { color: var(--red); } .bar { height: 6px; background: var(--rule); border-radius: 3px; overflow: hidden; margin-top: 4px; }
.bar i { display: block; height: 100%; background: var(--teal); }
code { font-size: .85rem; overflow-wrap: anywhere; }
</style></head>
<body>
<header><h1>HERON Download</h1>
<p class="note">Copies recordings from the NUC into your collects folder: one .dat and .yml per channel, and the experiment's metadata.yml. The NUC refuses while it records or is armed.</p></header>
<main>
<section>
  <h2>Settings</h2>
  <div class="row">
    <label>NUC (Payload Setup address)<input id="nuc" spellcheck="false"></label>
    <label>Collects folder on this computer<input id="dest" spellcheck="false"></label>
    <button id="save">Save</button>
    <label style="flex-direction:row;display:flex;gap:6px;align-items:center"><input id="sim" type="checkbox" style="min-width:0"> Show simulated sessions</label>
  </div>
  <p id="settingsMsg" class="note"></p>
</section>
<section>
  <div class="row" style="justify-content:space-between;align-items:center"><h2 style="margin:0">Sessions on the NUC</h2><button id="refresh">Refresh</button></div>
  <p id="listMsg" class="note"></p>
  <table><thead><tr><th>Session (UTC)</th><th>Setup</th><th>Length</th><th>Size</th><th>State</th><th>Experiment folder</th><th></th></tr></thead>
  <tbody id="sessions"></tbody></table>
  <datalist id="experiments"></datalist>
</section>
<section>
  <h2>Downloads</h2>
  <table><thead><tr><th>Session</th><th>Into</th><th>Progress</th><th>Status</th><th></th></tr></thead><tbody id="jobs"></tbody></table>
</section>
</main>
<script>
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const gb = (b) => b == null ? "—" : b >= 1e9 ? (b / 1e9).toFixed(2) + " GB" : (b / 1e6).toFixed(1) + " MB";
const len = (s) => !s ? "—" : s < 90 ? s.toFixed(1) + " s" : Math.floor(s / 60) + " min " + String(Math.round(s % 60)).padStart(2, "0") + " s";
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-Heron": "1" }, body: JSON.stringify(body) });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || r.statusText);
  return d;
}
let sessions = [], jobs = [], typed = {};
function when(name) { const m = name.match(/^HERON_(\d{4})(\d\d)(\d\d)_(\d\d)(\d\d)(\d\d)/); return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : name; }
async function loadSettings() {
  const s = await api("/api/settings");
  $("nuc").value = s.nuc; $("dest").value = s.dest;
}
async function loadSessions() {
  $("listMsg").textContent = "Asking the NUC…";
  try {
    const d = await api("/api/sessions?simulated=" + ($("sim").checked ? 1 : 0));
    sessions = d.sessions;
    $("experiments").innerHTML = d.experiments.map((e) => `<option value="${esc(e)}">`).join("");
    $("listMsg").textContent = sessions.length ? `${sessions.length} session(s), newest first.` : "No sessions on the NUC.";
  } catch (e) { sessions = []; $("listMsg").innerHTML = `<span class="err">Cannot read the NUC: ${esc(e.message)}</span>`; }
  renderSessions();
}
function renderSessions() {
  const busy = new Set(jobs.filter((j) => j.state === "queued" || j.state === "running").map((j) => j.session));
  $("sessions").innerHTML = sessions.map((s, i) => {
    const state = s.complete === null ? `<span class="tag warn">in progress</span>` : !s.complete ? `<span class="tag bad">incomplete</span>`
      : s.dropped ? `<span class="tag warn">${s.dropped} dropped</span>` : `<span class="tag">complete</span>`;
    const where = s.pulled_into ? `<span class="tag">in ${esc(s.pulled_into)}</span>`
      : `<input data-exp="${i}" list="experiments" value="${esc(typed[s.name] ?? s.default_experiment)}" spellcheck="false">`;
    const btn = s.pulled_into ? "" : `<button class="primary" data-get="${i}" ${busy.has(s.name) || s.complete === null ? "disabled" : ""}>${busy.has(s.name) ? "Queued" : "Download"}</button>`;
    return `<tr><td><b>${when(s.name)}</b><br><code>${esc(s.name)}</code></td><td>${esc(s.setup || "—")}${s.simulated ? " (simulated)" : ""}</td>
      <td>${len(s.seconds)}</td><td>${gb(s.bytes)}<br><span class="note">${s.channels} channel(s)</span></td><td>${state}</td><td>${where}</td><td>${btn}</td></tr>`;
  }).join("");
  $("sessions").querySelectorAll("[data-exp]").forEach((el) => el.addEventListener("input", () => { typed[sessions[+el.dataset.exp].name] = el.value; }));
  $("sessions").querySelectorAll("[data-get]").forEach((b) => b.addEventListener("click", async () => {
    const s = sessions[+b.dataset.get];
    try { await api("/api/pull", { session: s.name, experiment: typed[s.name] ?? s.default_experiment }); await poll(); }
    catch (e) { alert(e.message); }
  }));
}
function renderJobs() {
  $("jobs").innerHTML = jobs.slice().reverse().map((j) => {
    const pct = j.total ? Math.min(100, 100 * j.done / j.total) : 0;
    const cls = { done: "tag", failed: "tag bad", cancelled: "tag warn", running: "tag", queued: "tag warn" }[j.state];
    return `<tr><td><code>${esc(j.session)}</code></td><td><code>${esc(j.experiment)}</code></td>
      <td style="min-width:180px">${j.total ? `${gb(j.done)} of ${gb(j.total)}${j.mb_per_s ? " · " + j.mb_per_s + " MB/s" : ""}<div class="bar"><i style="width:${pct}%"></i></div>` : "—"}</td>
      <td><span class="${cls}">${esc(j.state)}</span> <span class="${j.state === "failed" ? "err" : "note"}">${esc(j.message)}</span>
        ${j.folder ? `<br><code>${esc(j.folder)}</code>` : ""}</td>
      <td>${j.state === "running" || j.state === "queued" ? `<button data-cancel="${j.id}">Cancel</button>` : ""}</td></tr>`;
  }).join("") || `<tr><td colspan="5" class="note">Nothing yet. Press Download on a session.</td></tr>`;
  $("jobs").querySelectorAll("[data-cancel]").forEach((b) => b.addEventListener("click", () => api("/api/cancel", { id: +b.dataset.cancel }).then(poll)));
}
let wasBusy = false, busyKey = "";
async function poll() {
  try {
    jobs = (await api("/api/jobs")).jobs;
    renderJobs();
    const busy = jobs.some((j) => j.state === "queued" || j.state === "running");
    // Rebuild the session table only when its Download buttons change, so typing in a folder box is not lost.
    const key = jobs.filter((j) => j.state === "queued" || j.state === "running").map((j) => j.session).join();
    if (wasBusy && !busy) await loadSessions(); else if (key !== busyKey) renderSessions();
    busyKey = key; wasBusy = busy;
  } catch (e) { /* the page keeps polling */ }
}
$("save").addEventListener("click", async () => {
  try { await api("/api/settings", { nuc: $("nuc").value, dest: $("dest").value }); $("settingsMsg").textContent = "Saved."; await loadSessions(); }
  catch (e) { $("settingsMsg").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
});
$("refresh").addEventListener("click", loadSessions);
$("sim").addEventListener("change", loadSessions);
loadSettings().then(loadSessions);
poll(); setInterval(poll, 1000);
</script>
</body></html>
"""


def make_handler(puller, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):          # keep the console quiet; errors still show on the page
            pass

        def _send(self, code, body, kind="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _guard(self):
            # Only this computer's browser: a page from another site cannot add X-Heron, and a rebound DNS name
            # does not match the Host check.
            if self.headers.get("Host") not in allowed_hosts:
                self._send(403, dict(error="Use http://localhost:%d" % port))
                return False
            return True

        def do_GET(self):
            if not self._guard():
                return
            path, _, query = self.path.partition("?")
            try:
                if path == "/":
                    self._send(200, PAGE, "text/html")
                elif path == "/api/settings":
                    self._send(200, puller.settings)
                elif path == "/api/sessions":
                    self._send(200, dict(sessions=puller.sessions("simulated=1" in query),
                                         experiments=puller.experiments()))
                elif path == "/api/jobs":
                    self._send(200, dict(jobs=puller.job_list()))
                else:
                    self._send(404, dict(error="Not found"))
            except Exception as exc:
                self._send(502, dict(error=str(exc)))

        def do_POST(self):
            if not self._guard():
                return
            if self.headers.get("X-Heron") != "1":
                self._send(403, dict(error="Missing X-Heron header."))
                return
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 64000)) or b"{}")
                if self.path == "/api/settings":
                    puller.save_settings(body.get("nuc"), body.get("dest"))
                    self._send(200, puller.settings)
                elif self.path == "/api/pull":
                    self._send(200, dict(job=puller.add(body.get("session"), body.get("experiment"))))
                elif self.path == "/api/cancel":
                    puller.cancel(body.get("id"))
                    self._send(200, dict(ok=True))
                else:
                    self._send(404, dict(error="Not found"))
            except ValueError as exc:
                self._send(400, dict(error=str(exc)))
            except Exception as exc:
                self._send(500, dict(error=str(exc)))

    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(description="Local page to download HERON recordings from the NUC.")
    p.add_argument("--port", type=int, default=8770)
    p.add_argument("--no-browser", action="store_true", help="do not open the page in the browser")
    a = p.parse_args(argv)
    puller = Puller()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(puller, a.port))
    except OSError:
        # Already running (the shortcut was double-clicked twice): just open the page.
        print(f"HERON Download is already running: http://localhost:{a.port}")
        if not a.no_browser:
            webbrowser.open(f"http://localhost:{a.port}")
        return 0
    url = f"http://localhost:{a.port}"
    print(f"HERON Download: {url}  (NUC {puller.settings['nuc']}, saving to {puller.settings['dest']})")
    print("Keep this window open while you download. Close it (or Ctrl+C) to stop.")
    if not a.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
