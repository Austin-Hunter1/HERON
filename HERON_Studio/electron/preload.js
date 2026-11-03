// Small, explicit bridge: the page can ask for native file pickers and a dropped file's real path.
const { contextBridge, ipcRenderer, webUtils } = require("electron");
contextBridge.exposeInMainWorld("heron", {
  platform: process.platform,
  pickFiles: () => ipcRenderer.invoke("pick-files"),
  pickFolder: () => ipcRenderer.invoke("pick-folder"),
  pathForFile: (file) => { try { return webUtils.getPathForFile(file) || null; } catch { return null; } },
  reveal: (p) => ipcRenderer.invoke("reveal", p),
});
