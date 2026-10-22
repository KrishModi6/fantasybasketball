// Exposes a tiny, read-only bridge: the server address, whether it is the bundled engine, and app metadata.
const { contextBridge } = require("electron");

function arg(name) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : undefined;
}

contextBridge.exposeInMainWorld("courtside", {
  defaultServer: arg("courtside-server"),
  version: arg("courtside-version"),
  platform: process.platform,
  local: arg("courtside-local") === "1", // talking to the engine bundled with this app
});
