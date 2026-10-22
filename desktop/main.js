// Courtside desktop shell (macOS + Windows).
//
// Loads the same React build as the website from a privileged app:// scheme, so client-side
// routing works and the code is identical across platforms.
//
// Release builds carry the Courtside engine (server/engine.py, built with PyInstaller) and start
// it on 127.0.0.1 with the league database in the user's app-data folder: nothing to install or
// host. Without a bundled engine (development), the app talks to COURTSIDE_SERVER or localhost.
//
// Installed builds keep themselves up to date from the GitHub releases (see setupUpdates).

const { app, BrowserWindow, Menu, dialog, ipcMain, net, powerMonitor, protocol, shell } = require("electron");
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
const RELEASES_URL = "https://github.com/KrishModi6/fantasybasketball/releases/latest";
const UPDATE_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;
// Windows downloads and installs updates by itself. The Mac app is only ad-hoc signed, and macOS
// won't swap in an update that isn't signed by a registered developer, so there the app says a new
// version is out and opens the download.
const CAN_INSTALL_UPDATES = process.platform === "win32";

protocol.registerSchemesAsPrivileged([
  { scheme: SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } },
]);

let engine = null; // { child, url }
let quitting = false;
let updater = null; // { check, install } once auto-update is running
// What the app shows: idle | checking | current | downloading | ready (to install) | available (to download) | error
let update = { status: "idle" };

const engineRunning = () => Boolean(engine) && engine.child.exitCode === null && engine.child.signalCode === null;

/** Stops the engine and waits (briefly) until it has exited and released its files. */
function stopEngine() {
  if (!engineRunning()) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, 5000);
    engine.child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
    engine.child.kill();
  });
}

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

// Back/forward through the app's own pages (the React router pushes real history entries).
function stepHistory(win, delta) {
  if (!win || win.isDestroyed()) return;
  const history = win.webContents.navigationHistory ?? win.webContents; // navigationHistory: Electron 32+
  if (delta < 0 && history.canGoBack()) history.goBack();
  else if (delta > 0 && history.canGoForward()) history.goForward();
}

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

  // Mouse back/forward buttons (Windows, Linux) and trackpad swipes (macOS).
  win.on("app-command", (_e, cmd) => {
    if (cmd === "browser-backward") stepHistory(win, -1);
    else if (cmd === "browser-forward") stepHistory(win, 1);
  });
  win.on("swipe", (_e, direction) => {
    if (direction === "right") stepHistory(win, -1);
    else if (direction === "left") stepHistory(win, 1);
  });

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

function publishUpdate(next) {
  update = { checkedAt: update.checkedAt, ...next, current: app.getVersion(), canInstall: CAN_INSTALL_UPDATES };
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed()) win.webContents.send("courtside:update", update);
  }
}

function fileLogger(file) {
  const write = (level) => (...parts) =>
    fs.appendFile(file, `${new Date().toISOString()} ${level} ${parts.join(" ")}\n`, () => undefined);
  return { info: write("INFO"), warn: write("WARN"), error: write("ERROR"), debug: () => undefined };
}

function promptRestart() {
  dialog.showMessageBox({
    type: "info",
    title: "Courtside",
    message: `Courtside ${update.version} is ready to install.`,
    detail: "Restart now, or it installs the next time you close Courtside. Your leagues stay as they are.",
    buttons: ["Restart now", "Later"],
    defaultId: 0,
  }).then(({ response }) => response === 0 && updater?.install());
}

// Checks the GitHub releases (app-update.yml, written at build time from package.json's "publish")
// shortly after launch, every few hours, and when the computer wakes up or Courtside comes back to the
// front after a while, so even a laptop that mostly sleeps checks at least once a day. Windows downloads
// in the background and installs on restart; the Mac only reports the new version (see CAN_INSTALL_UPDATES).
function setupUpdates() {
  if (!app.isPackaged) return; // development runs have no release feed
  let autoUpdater;
  try {
    ({ autoUpdater } = require("electron-updater"));
  } catch (err) {
    console.error("Auto-update unavailable:", err);
    return;
  }
  const logs = path.join(app.getPath("userData"), "logs");
  fs.mkdirSync(logs, { recursive: true });
  autoUpdater.logger = fileLogger(path.join(logs, "updates.log"));
  autoUpdater.autoDownload = CAN_INSTALL_UPDATES;
  autoUpdater.autoInstallOnAppQuit = CAN_INSTALL_UPDATES;
  autoUpdater.disableWebInstaller = true; // releases ship the full installer

  let manual = false; // a check from the Help menu answers "up to date" and errors in a dialog
  autoUpdater.on("checking-for-update", () => publishUpdate({ status: "checking" }));
  autoUpdater.on("update-not-available", () => {
    publishUpdate({ status: "current", checkedAt: new Date().toISOString() });
    if (manual) {
      dialog.showMessageBox({ type: "info", title: "Courtside", message: "You're up to date.",
        detail: `Courtside ${app.getVersion()} is the latest version.` });
    }
    manual = false;
  });
  autoUpdater.on("update-available", (info) => {
    publishUpdate({ status: CAN_INSTALL_UPDATES ? "downloading" : "available", version: info.version, percent: 0,
      checkedAt: new Date().toISOString() });
    manual = false;
  });
  autoUpdater.on("download-progress", (p) =>
    publishUpdate({ status: "downloading", version: update.version, percent: Math.round(p.percent) }));
  autoUpdater.on("update-downloaded", (info) => publishUpdate({ status: "ready", version: info.version }));
  autoUpdater.on("error", (err) => {
    const message = err?.message ?? String(err);
    publishUpdate({ status: "error", version: update.version, error: message, checkedAt: new Date().toISOString() });
    if (manual) {
      dialog.showMessageBox({ type: "warning", title: "Courtside", message: "Couldn't check for updates.",
        detail: `${message.split("\n")[0]}\n\nThe latest version is always at ${RELEASES_URL}` });
    }
    manual = false;
  });

  let lastCheck = Date.now(); // wall-clock time of the last check (the launch check below counts)
  const check = (fromMenu = false) => {
    if (update.status === "ready") return fromMenu && promptRestart();
    if (update.status === "downloading" || update.status === "checking") return;
    manual = fromMenu;
    lastCheck = Date.now();
    autoUpdater.checkForUpdates().catch(() => undefined); // failures also arrive as "error" events
  };
  // Timers stop while the computer sleeps, so the interval alone can go days without firing on a laptop.
  const checkIfDue = () => Date.now() - lastCheck >= UPDATE_CHECK_INTERVAL_MS && check();
  updater = {
    check,
    install: async () => {
      if (update.status !== "ready") return;
      // The installer replaces the engine's files, so the engine has to be gone first.
      quitting = true;
      await stopEngine();
      autoUpdater.quitAndInstall(true, true); // install silently, then reopen Courtside
    },
  };
  setTimeout(check, 15_000);
  setInterval(check, UPDATE_CHECK_INTERVAL_MS);
  powerMonitor.on("resume", () => setTimeout(checkIfDue, 30_000)); // give the network a moment after waking
  app.on("browser-window-focus", checkIfDue);
}

// "Check for updates" from the Help menu or the version label: answers in a dialog either way.
function checkForUpdatesNow() {
  if (updater) return updater.check(true);
  return dialog.showMessageBox({ type: "info", title: "Courtside",
    message: "Updates are checked in installed copies of Courtside.", detail: `Downloads: ${RELEASES_URL}` });
}

function setupIpc() {
  // Only Courtside's own pages may drive updates.
  const fromApp = (event) => {
    const url = event.senderFrame?.url ?? "";
    return url.startsWith(`${SCHEME}://${HOST}/`)
      || (process.argv.includes("--dev") && url.startsWith(process.env.COURTSIDE_DEV_URL || "http://localhost:5173"));
  };
  ipcMain.handle("courtside:update-state", () => update);
  ipcMain.handle("courtside:update-install", (event) => (fromApp(event) ? updater?.install() : undefined));
  ipcMain.handle("courtside:update-download", (event) => (fromApp(event) ? shell.openExternal(RELEASES_URL) : undefined));
  ipcMain.handle("courtside:update-check", (event) => (fromApp(event) ? checkForUpdatesNow() : undefined));
}

function buildMenu() {
  const isMac = process.platform === "darwin";
  const template = [
    ...(isMac ? [{ role: "appMenu" }] : []),
    { role: "fileMenu" },
    { role: "editMenu" },
    {
      label: "View",
      submenu: [
        { label: "Back", accelerator: isMac ? "Cmd+[" : "Alt+Left", click: (_item, win) => stepHistory(win, -1) },
        { label: "Forward", accelerator: isMac ? "Cmd+]" : "Alt+Right", click: (_item, win) => stepHistory(win, 1) },
        { type: "separator" },
        { role: "reload" }, { role: "toggleDevTools" }, { type: "separator" }, { role: "resetZoom" },
        { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" }],
    },
    { role: "windowMenu" },
    {
      role: "help",
      submenu: [
        { label: "Check for updates…", click: checkForUpdatesNow },
        { label: "Download page", click: () => shell.openExternal(RELEASES_URL) },
        { type: "separator" },
        { label: "Open engine log", click: () => engine && shell.showItemInFolder(engine.logFile) },
        { role: "about" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function boot() {
  serveApp();
  setupIpc();
  buildMenu();
  setupUpdates();
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
app.on("before-quit", (event) => {
  quitting = true;
  if (update.status === "ready" && engineRunning()) {
    // A downloaded update installs as Courtside closes, and it can't replace the engine's files while
    // the engine still runs: stop it, then quit for real.
    event.preventDefault();
    stopEngine().then(() => app.quit());
  }
});
app.on("will-quit", () => {
  if (engineRunning()) engine.child.kill();
});
