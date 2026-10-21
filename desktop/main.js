// Courtside desktop shell (macOS + Windows).
//
// Loads the same React build as the website from a privileged app:// scheme,
// so client-side routing works and the code is identical across platforms.
// The app talks to a Courtside server (your own or a hosted one); the address
// is set on the sign-in screen and defaults to COURTSIDE_SERVER or localhost.

const { app, BrowserWindow, Menu, net, protocol, shell } = require("electron");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const fs = require("node:fs");

const SCHEME = "app";
const HOST = "fbb";
const WEB_ROOT = path.join(__dirname, "web-dist");
const DEFAULT_SERVER = process.env.COURTSIDE_SERVER || "http://localhost:8000";

protocol.registerSchemesAsPrivileged([
  { scheme: SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } },
]);

function serveApp() {
  protocol.handle(SCHEME, (request) => {
    const url = new URL(request.url);
    let rel = decodeURIComponent(url.pathname);
    let file = path.normalize(path.join(WEB_ROOT, rel));
    // Never serve outside the bundle; fall back to index.html for client routes.
    if (!file.startsWith(WEB_ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      file = path.join(WEB_ROOT, "index.html");
    }
    return net.fetch(pathToFileURL(file).toString());
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    title: "Courtside",
    backgroundColor: "#0F1115",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: [`--courtside-server=${DEFAULT_SERVER}`, `--courtside-version=${app.getVersion()}`],
    },
  });
  win.once("ready-to-show", () => win.show());

  // Links to ESPN, docs, etc. open in the system browser, never inside the app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(`${SCHEME}://${HOST}`)) {
      event.preventDefault();
      if (/^https?:/.test(url)) shell.openExternal(url);
    }
  });

  if (process.argv.includes("--dev")) {
    win.loadURL(process.env.COURTSIDE_DEV_URL || "http://localhost:5173");
  } else {
    win.loadURL(`${SCHEME}://${HOST}/`);
  }
  return win;
}

function buildMenu() {
  const isMac = process.platform === "darwin";
  const template = [
    ...(isMac ? [{ role: "appMenu" }] : []),
    { role: "fileMenu" },
    { role: "editMenu" },
    {
      label: "View",
      submenu: [{ role: "reload" }, { role: "toggleDevTools" }, { type: "separator" }, { role: "resetZoom" },
        { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" }],
    },
    { role: "windowMenu" },
    { role: "help", submenu: [{ role: "about" }] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(() => {
  serveApp();
  buildMenu();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
