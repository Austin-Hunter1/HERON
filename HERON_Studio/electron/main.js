// HERON Studio desktop shell: starts the Python server, shows it in a native window.
//   dev:       cd electron && npm install && npm start      (uses python3 + ../run.py)
//   packaged:  the PyInstaller build in resources/backend is started instead
const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn, execFile } = require("child_process");
const path = require("path");
const fs = require("fs");
const net = require("net");
const http = require("http");
const os = require("os");

let win = null, py = null, port = 5056, quitting = false;
const ROOT = path.resolve(__dirname, "..");

function freePort(start) {
  return new Promise((resolve) => {
    const s = net.createServer(); s.once("error", () => resolve(freePort(start + 1)));
    s.once("listening", () => s.close(() => resolve(start))); s.listen(start, "127.0.0.1");
  });
}

function ffmpegPath() {
  // bundled static ffmpeg/ffprobe so users don't need Homebrew
  const dirs = [];
  for (const mod of ["ffmpeg-static", "ffprobe-static"]) {
    try {
      let p = require(mod); p = typeof p === "string" ? p : p.path;
      dirs.push(path.dirname(p.replace("app.asar", "app.asar.unpacked")));
    } catch { }
  }
  return dirs;
}

function backendCommand() {
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, "backend", process.platform === "win32" ? "heron-studio.exe" : "heron-studio");
    return { cmd: exe, args: ["--no-browser", "--port", String(port)], cwd: path.dirname(exe) };
  }
  const python = process.env.HERON_PYTHON || (process.platform === "win32" ? "python" : "python3");
  return { cmd: python, args: [path.join(ROOT, "run.py"), "--no-browser", "--port", String(port)], cwd: ROOT };
}

function startBackend() {
  const { cmd, args, cwd } = backendCommand();
  const env = { ...process.env, HERON_STUDIO_PORT: String(port), PYTHONUNBUFFERED: "1" };
  env.PATH = [...ffmpegPath(), "/opt/homebrew/bin", "/usr/local/bin", env.PATH].join(path.delimiter);
  const logDir = path.join(app.getPath("logs")); fs.mkdirSync(logDir, { recursive: true });
  const log = fs.createWriteStream(path.join(logDir, "backend.log"), { flags: "a" });
  py = spawn(cmd, args, { cwd, env, detached: process.platform !== "win32" });
  let tail = "";
  const keep = (d) => { log.write(d); tail = (tail + d.toString()).slice(-4000); };
  py.stdout.on("data", keep); py.stderr.on("data", keep);
  py.on("exit", (code) => {
    if (!quitting) {
      const msg = `Engine stopped (exit ${code}).\n\n${tail}\n\nLog: ${path.join(logDir, "backend.log")}`;
      if (win) win.webContents.executeJavaScript(`window.postMessage(${JSON.stringify({ error: msg })}, "*")`).catch(() => { });
      dialog.showErrorBox("HERON Studio", msg.slice(0, 1500));
    }
  });
  py.on("error", (e) => dialog.showErrorBox("HERON Studio", `Couldn't start Python (${cmd}): ${e.message}\nSet HERON_PYTHON to your python3.`));
}

function waitForServer(timeoutMs = 60000) {
  const t0 = Date.now();
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      http.get({ host: "127.0.0.1", port, path: "/api/state", timeout: 1500 }, (res) => { res.resume(); res.statusCode === 200 ? resolve() : retry(); }).on("error", retry);
    };
    const retry = () => Date.now() - t0 > timeoutMs ? reject(new Error("timeout")) : setTimeout(tryOnce, 300);
    tryOnce();
  });
}

function stopBackend() {
  quitting = true;
  if (!py || py.exitCode != null) return;
  if (process.platform === "win32") execFile("taskkill", ["/pid", String(py.pid), "/T", "/F"]);
  else { try { process.kill(-py.pid, "SIGTERM"); } catch { py.kill("SIGTERM"); } }
}

async function createWindow() {
  port = await freePort(5056);
  win = new BrowserWindow({
    width: 1440, height: 900, minWidth: 960, minHeight: 640, backgroundColor: "#0c141b", show: false,
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default", trafficLightPosition: { x: 16, y: 20 },
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true, nodeIntegration: false },
  });
  win.once("ready-to-show", () => win.show());
  win.loadFile(path.join(__dirname, "splash.html"));
  startBackend();
  try { await waitForServer(); win.loadURL(`http://127.0.0.1:${port}/`); }
  catch { /* splash shows the error via the exit handler */ }

  // links to other sites open in the browser, not inside the app
  win.webContents.setWindowOpenHandler(({ url }) => { if (!url.startsWith(`http://127.0.0.1:${port}`)) shell.openExternal(url); return { action: "deny" }; });
  win.webContents.on("will-navigate", (e, url) => { if (!url.startsWith(`http://127.0.0.1:${port}`) && !url.startsWith("file:")) { e.preventDefault(); shell.openExternal(url); } });
  // downloads (exports, AR video): native Save dialog, default to Downloads
  win.webContents.session.on("will-download", (e, item) => {
    item.setSaveDialogOptions({ defaultPath: path.join(app.getPath("downloads"), item.getFilename()) });
  });
}

ipcMain.handle("pick-files", async () => {
  const r = await dialog.showOpenDialog(win, {
    properties: ["openFile", "multiSelections"],
    filters: [{ name: "Flight files", extensions: ["mp4", "MP4", "mov", "MOV", "bin", "BIN", "waypoints", "plan", "geojson", "json", "kml", "csv"] }, { name: "All files", extensions: ["*"] }],
  });
  return r.canceled ? [] : r.filePaths;
});
ipcMain.handle("pick-folder", async () => {
  const r = await dialog.showOpenDialog(win, { properties: ["openDirectory"], message: "Folder with GYRO.csv + ACCL.csv, or the log CSVs" });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("reveal", (_e, p) => shell.showItemInFolder(p));

app.setName("HERON Studio");
app.whenReady().then(createWindow);
app.on("window-all-closed", () => app.quit());
app.on("before-quit", stopBackend);
app.on("activate", () => { if (!BrowserWindow.getAllWindows().length) createWindow(); });
