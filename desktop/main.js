// Courtside desktop shell (macOS + Windows).
//
// Loads the same React build as the website from a privileged app:// scheme, so client-side
// routing works and the code is identical across platforms.
//
// Release builds carry the Courtside engine (server/engine.py, built with PyInstaller) and start
// it on 127.0.0.1 with the league database in the user's app-data folder: nothing to install or
// host. Without a bundled engine (development), the app talks to COURTSIDE_SERVER or localhost.

const { app, BrowserWindow, Menu, dialog, net, protocol, shell } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const netSocket = require("node:net");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const SCHEME = "app";
const HOST = "fbb";
const WEB_ROOT = path.join(__dirname, "web-dist");
const ENGINE_NAME = process.platform === "win32" ? "courtside-engine.exe" : "courtside-engine";
const ENGINE_START_TIMEOUT_MS = 90_000;

protocol.registerSchemesAsPrivileged([
  { scheme: SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } },
]);

let engine = null; // { child, url }
let quitting = false;

function engineExecutable() {
  if (process.env.COURTSIDE_SERVER) return null; // explicitly pointed at a server
  const candidates = app.isPackaged
    ? [path.join(process.resourcesPath, "engine", ENGINE_NAME)]
    : [path.join(__dirname, "..", "server", "dist", "courtside-engine", ENGINE_NAME)];
  return candidates.find((p) => fs.existsSync(p)) ?? null;
}

// The AI proxy address and client token are written by the release workflow (never committed).
function aiConfig() {
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(__dirname, "ai-config.json"), "utf8"));
    return typeof cfg.url === "string" && typeof cfg.token === "string" ? cfg : null;
  } catch {
    return null;
  }
}

function persistentSecret(dir) {
  const file = path.join(dir, "engine-secret");
  try {
    const existing = fs.readFileSync(file, "utf8").trim();
    if (existing.length >= 32) return existing;
  } catch {
    /* first run */
  }
  const secret = crypto.randomBytes(48).toString("base64url");
  fs.writeFileSync(file, secret, { mode: 0o600 });
  return secret;
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = netSocket.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function startEngine(exe) {
  const data = app.getPath("userData");
  const logs = path.join(data, "logs");
  fs.mkdirSync(logs, { recursive: true });
  const logFile = path.join(logs, "engine.log");
  if (fs.existsSync(logFile)) fs.renameSync(logFile, path.join(logs, "engine.previous.log"));
  const log = fs.createWriteStream(logFile);

  const port = await freePort();
  const ai = aiConfig();
  const env = {
    ...process.env,
    FBB_DATABASE_URL: `sqlite:///${path.join(data, "courtside.db").replace(/\\/g, "/")}`,
    FBB_SECRET_KEY: persistentSecret(data),
    FBB_CACHE_DIR: path.join(data, "engine-cache"), // not "cache": Electron owns Cache (same folder on Windows)
    FBB_LOCAL_MODE: "true",
    FBB_CORS_ORIGINS: `${SCHEME}://${HOST}`,
    FBB_RETRAIN_ENABLED: "false",
    ...(ai ? { FBB_AI_PROXY_URL: ai.url, FBB_AI_PROXY_TOKEN: ai.token } : {}),
  };
  const child = spawn(exe, ["--port", String(port)], { env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  child.stdout.pipe(log);
  child.stderr.pipe(log);
  const url = `http://127.0.0.1:${port}`;
  engine = { child, url, logFile };

  const exited = new Promise((resolve) => child.once("exit", (code) => resolve(code)));
  const deadline = Date.now() + ENGINE_START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const early = await Promise.race([exited, new Promise((r) => setTimeout(() => r(undefined), 250))]);
    if (early !== undefined) throw new Error(`The engine stopped while starting (exit code ${early}).`);
    try {
      const res = await fetch(`${url}/api/health`);
      if (res.ok) {
        child.once("exit", (code) => !quitting && engineCrashed(code));
        return url;
      }
    } catch {
      /* not listening yet */
    }
  }
  child.kill();
  throw new Error("The engine took too long to start.");
}

function engineCrashed(code) {
  const choice = dialog.showMessageBoxSync({
    type: "error",
    title: "Courtside",
    message: "Courtside's engine stopped unexpectedly.",
    detail: `Exit code ${code}. Details are in ${engine?.logFile}.`,
    buttons: ["Restart Courtside", "Quit"],
    defaultId: 0,
  });
  if (choice === 0) app.relaunch();
  app.quit();
}

function serveApp() {
  protocol.handle(SCHEME, (request) => {
    const url = new URL(request.url);
    let file = path.normalize(path.join(WEB_ROOT, decodeURIComponent(url.pathname)));
    // Never serve outside the bundle; fall back to index.html for client routes.
    if (!file.startsWith(WEB_ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      file = path.join(WEB_ROOT, "index.html");
    }
    return net.fetch(pathToFileURL(file).toString());
  });
}

const SPLASH = `data:text/html;charset=utf-8,${encodeURIComponent(`<!doctype html><html><head><meta charset="utf-8">
<style>html,body{height:100%;margin:0;background:#0f1115;color:#8b949e;font:13px "Segoe UI",-apple-system,sans-serif}
body{display:grid;place-items:center}div{display:grid;gap:10px;justify-items:center}
b{color:#d7dce2;font-weight:600;font-size:15px}i{width:160px;height:2px;background:#1b2028;overflow:hidden;position:relative}
i::after{content:"";position:absolute;inset:0;width:40%;background:#3b82f6;animation:s 1.1s ease-in-out infinite}
@keyframes s{from{transform:translateX(-100%)}to{transform:translateX(250%)}}</style></head>
<body><div><b>Courtside</b><span>Starting the projection engine…</span><i></i></div></body></html>`)}`;

function createWindow(server, local) {
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
      additionalArguments: [
        `--courtside-server=${server}`,
        `--courtside-version=${app.getVersion()}`,
        `--courtside-local=${local ? "1" : "0"}`,
      ],
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
  return win;
}

function loadApp(win) {
  if (process.argv.includes("--dev")) win.loadURL(process.env.COURTSIDE_DEV_URL || "http://localhost:5173");
  else win.loadURL(`${SCHEME}://${HOST}/`);
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
    {
      role: "help",
      submenu: [
        { label: "Open engine log", click: () => engine && shell.showItemInFolder(engine.logFile) },
        { role: "about" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function boot() {
  serveApp();
  buildMenu();
  const exe = engineExecutable();
  if (!exe) {
    loadApp(createWindow(process.env.COURTSIDE_SERVER || "http://localhost:8000", false));
    return;
  }
  // The app window needs the engine's address at creation, so a splash covers the start-up.
  const splash = createWindow("", true);
  splash.loadURL(SPLASH);
  try {
    const url = await startEngine(exe);
    const win = createWindow(url, true);
    win.once("ready-to-show", () => {
      if (splash.isDestroyed()) return;
      win.setBounds(splash.getBounds());
      splash.destroy();
    });
    loadApp(win);
  } catch (err) {
    dialog.showErrorBox("Courtside could not start", `${err.message}\n\nDetails are in ${engine?.logFile ?? "the engine log"}.`);
    app.quit();
  }
}

// One copy at a time: two engines would share one database.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const win = BrowserWindow.getAllWindows()[0];
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });
  app.whenReady().then(boot);
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0 && engine) loadApp(createWindow(engine.url, true));
  });
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
app.on("before-quit", () => {
  quitting = true;
});
app.on("will-quit", () => {
  if (engine && engine.child.exitCode === null) engine.child.kill();
});
