// Exposes a tiny, read-only bridge: the default server address and app metadata.
const { contextBridge } = require("electron");

function arg(name) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : undefined;
}

contextBridge.exposeInMainWorld("courtside", {
  defaultServer: arg("courtside-server"),
  version: arg("courtside-version"),
  platform: process.platform,
});
