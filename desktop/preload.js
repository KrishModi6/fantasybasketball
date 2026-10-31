// Exposes a small bridge: the server address, whether it is the bundled engine, app metadata,
// auto-update status (read it, follow changes, check now, install a downloaded update, open the download page),
// the window's title bar (follow the theme, open the menu) and the release notes for What's new.
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
  setTheme: (theme) => ipcRenderer.invoke("courtside:theme", theme),
  openMenu: (x, y) => ipcRenderer.invoke("courtside:menu", x, y),
  releaseNotes: () => ipcRenderer.invoke("courtside:release-notes"),
  // Help > What's new in Courtside.
  onWhatsNew: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("courtside:whats-new", listener);
    return () => ipcRenderer.removeListener("courtside:whats-new", listener);
  },
  updates: {
    state: () => ipcRenderer.invoke("courtside:update-state"),
    install: () => ipcRenderer.invoke("courtside:update-install"),
    download: () => ipcRenderer.invoke("courtside:update-download"),
    check: () => ipcRenderer.invoke("courtside:update-check"),
    onChange: (callback) => {
      const listener = (_event, state) => callback(state);
      ipcRenderer.on("courtside:update", listener);
      return () => ipcRenderer.removeListener("courtside:update", listener);
    },
  },
});
