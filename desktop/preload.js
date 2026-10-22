// Exposes a small bridge: the server address, whether it is the bundled engine, app metadata, and
// auto-update status (read it, follow changes, install a downloaded update, open the download page).
const { contextBridge, ipcRenderer } = require("electron");

function arg(name) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : undefined;
}

contextBridge.exposeInMainWorld("courtside", {
  defaultServer: arg("courtside-server"),
  version: arg("courtside-version"),
  platform: process.platform,
  local: arg("courtside-local") === "1", // talking to the engine bundled with this app
  updates: {
    state: () => ipcRenderer.invoke("courtside:update-state"),
    install: () => ipcRenderer.invoke("courtside:update-install"),
    download: () => ipcRenderer.invoke("courtside:update-download"),
    onChange: (callback) => {
      const listener = (_event, state) => callback(state);
      ipcRenderer.on("courtside:update", listener);
      return () => ipcRenderer.removeListener("courtside:update", listener);
    },
  },
});
