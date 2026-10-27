// Mac updates without Terminal.
//
// electron-updater can't install updates on a Mac without an Apple Developer ID (Squirrel.Mac only
// accepts an update signed by the same registered developer), and a zip downloaded in a browser is
// flagged by macOS ("Courtside is damaged") until the flag is cleared with xattr in Terminal.
//
// So Courtside does it itself. electron-updater still finds the release; then this module downloads
// the zip (only browsers and other quarantine-aware apps flag their downloads, so this copy isn't
// flagged), checks it against the SHA-512 in latest-mac.yml, unzips it with ditto and checks the app's
// version and that its programs are there. When Courtside quits, a small shell script waits for it to
// exit, swaps the new Courtside.app into place and reopens it. If macOS won't let it move the app, the
// old copy is put back, and on its next start Courtside shows the downloaded copy in Finder to drag
// over by hand.
//
// No Electron imports, so desktop/scripts/test-mac-update.mjs can run all of it on a Mac in CI.

const { execFile, spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const fsp = require("node:fs/promises");
const path = require("node:path");
const { Readable } = require("node:stream");
const { pipeline } = require("node:stream/promises");

const DOWNLOAD_BASE = "https://github.com/KrishModi6/fantasybasketball/releases/download";
const STALL_TIMEOUT_MS = 60_000; // give up on a download that sends nothing for this long

// Runs after Courtside has quit: $1 Courtside's PID, $2 the installed app, $3 the new app,
// $4 where the old app waits during the swap, $5 the result file, $6 1 to reopen Courtside.
const SWAP_SCRIPT = `#!/bin/sh
pid="$1"; target="$2"; staged="$3"; backup="$4"; result="$5"; relaunch="$6"
log="$result.log"
n=0
while kill -0 "$pid" 2>/dev/null; do
  n=$((n + 1))
  if [ "$n" -gt 300 ]; then echo "failed" > "$result"; echo "Courtside didn't quit within a minute" >> "$log"; exit 1; fi
  /bin/sleep 0.2
done
/bin/rm -rf "$backup"
status=failed
if /bin/mv "$target" "$backup" 2>>"$log"; then
  if /bin/mv "$staged" "$target" 2>>"$log"; then
    status=installed
    /bin/rm -rf "$backup"
  else
    /bin/mv "$backup" "$target" 2>>"$log"
  fi
fi
echo "$status" > "$result"
if [ "$relaunch" = 1 ]; then /usr/bin/open "$target"; fi
`;

/** The .app bundle the running Courtside was started from (execPath is <app>/Contents/MacOS/<name>). */
function bundlePath(execPath) {
  const bundle = path.resolve(execPath, "..", "..", "..");
  return bundle.endsWith(".app") ? bundle : null;
}

/** Why Courtside can't replace its own app, or null when it can. */
function installProblem(bundle) {
  if (!bundle) return "Courtside isn't running from an app bundle.";
  // macOS runs a still-flagged app from a read-only copy ("App Translocation").
  if (bundle.includes("/AppTranslocation/")) return "Courtside is running from a temporary read-only copy.";
  try {
    fs.accessSync(bundle, fs.constants.W_OK);
    fs.accessSync(path.dirname(bundle), fs.constants.W_OK);
  } catch {
    return `Your Mac account can't change ${path.dirname(bundle)}.`;
  }
  return null;
}

function run(file, args) {
  return new Promise((resolve, reject) => {
    execFile(file, args, { timeout: 5 * 60_000, maxBuffer: 1 << 20 }, (err, stdout, stderr) => {
      if (err) reject(new Error(`${path.basename(file)} failed: ${(stderr || err.message).trim()}`));
      else resolve(stdout.trim());
    });
  });
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

/** The release zip for this Mac's processor, from the files listed in latest-mac.yml. */
function pickZip(info, arch = process.arch) {
  const zips = (info.files ?? []).filter((f) => /\.zip$/i.test(f.url ?? "") && f.sha512);
  return zips.find((f) => f.url.includes(`-${arch}.`)) ?? zips[0] ?? null;
}

async function download(fetch, url, file, onProgress) {
  const abort = new AbortController();
  let source = null;
  // Abort the request, and end the stream too, in case the fetch doesn't pass an abort on to its body.
  const stop = () => {
    abort.abort();
    source?.destroy(new Error("stalled"));
  };
  let stall = setTimeout(stop, STALL_TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: abort.signal, cache: "no-store" }); // no copy in the HTTP cache
    if (!res.ok || !res.body) throw new Error(`The download failed (HTTP ${res.status}).`);
    const total = Number(res.headers.get("content-length")) || 0;
    const hash = crypto.createHash("sha512");
    let received = 0;
    let shown = -1;
    source = Readable.fromWeb(res.body);
    await pipeline(
      source,
      async function* (chunks) {
        for await (const chunk of chunks) {
          clearTimeout(stall);
          stall = setTimeout(stop, STALL_TIMEOUT_MS);
          hash.update(chunk);
          received += chunk.length;
          const percent = total ? Math.min(99, Math.floor((received / total) * 100)) : 0;
          if (percent !== shown) onProgress((shown = percent));
          yield chunk;
        }
      },
      fs.createWriteStream(file),
    );
    return hash.digest("base64");
  } catch (err) {
    throw abort.signal.aborted ? new Error("The download stopped responding.") : err;
  } finally {
    clearTimeout(stall);
  }
}

/**
 * Downloads and checks the update in `info` (electron-updater's UpdateInfo) into `dir` and returns the
 * path of the new Courtside.app, ready for install(). Reuses an earlier download of the same release.
 */
async function stage({ info, dir, fetch = globalThis.fetch, base = DOWNLOAD_BASE, onProgress = () => undefined, log = console }) {
  const file = pickZip(info);
  if (!file) throw new Error("This release has no Mac download.");
  const stagedDir = path.join(dir, "new");
  const earlier = readJson(path.join(dir, "staged.json"));
  if (earlier?.version === info.version && earlier.sha512 === file.sha512 && earlier.app && fs.existsSync(earlier.app)) {
    log.info(`Reusing the download of ${info.version} at ${earlier.app}`);
    return earlier.app;
  }

  // GitHub release assets live at <base>/<tag>/<file>; electron-builder uploads spaces as dashes.
  const folder = new URL(`${base.replace(/\/$/, "")}/${encodeURIComponent(info.tag ?? `v${info.version}`)}/`);
  const url = new URL(file.url.replace(/ /g, "-"), folder);
  if (url.origin !== folder.origin) throw new Error(`Refusing to download the update from ${url.origin}.`);

  await fsp.rm(dir, { recursive: true, force: true });
  await fsp.mkdir(stagedDir, { recursive: true });
  const zip = path.join(dir, "download.zip");
  log.info(`Downloading ${url.href}`);
  const sha512 = await download(fetch, url.href, zip, onProgress);
  if (sha512 !== file.sha512) throw new Error("The download doesn't match the release's checksum.");

  await run("/usr/bin/ditto", ["-x", "-k", zip, stagedDir]);
  await fsp.rm(zip, { force: true });
  const name = (await fsp.readdir(stagedDir)).find((n) => n.endsWith(".app"));
  if (!name) throw new Error("The download has no app in it.");
  const app = path.join(stagedDir, name);
  const plist = (key) => run("/usr/libexec/PlistBuddy", ["-c", `Print :${key}`, path.join(app, "Contents", "Info.plist")]);
  const version = await plist("CFBundleShortVersionString");
  if (version !== info.version) throw new Error(`The download is version ${version}, not ${info.version}.`);
  // The release has no valid code signature to check (electron-builder can't sign without an Apple
  // Developer ID), so check that the app and the engine it starts unzipped whole.
  for (const program of [path.join("MacOS", await plist("CFBundleExecutable")), path.join("Resources", "engine", "courtside-engine")]) {
    try {
      fs.accessSync(path.join(app, "Contents", program), fs.constants.X_OK);
    } catch {
      throw new Error(`The download is missing ${program}.`);
    }
  }
  // Nothing should be flagged, since Courtside downloaded it, but clear any flag so it opens cleanly.
  await run("/usr/bin/xattr", ["-cr", app]).catch((err) => log.warn(err.message));

  fs.writeFileSync(path.join(dir, "staged.json"), JSON.stringify({ version, sha512, app }));
  log.info(`Courtside ${version} is ready at ${app}`);
  return app;
}

/**
 * Starts the script that swaps `app` (from stage) in for `bundle` once process `pid` (Courtside) has
 * exited, and reopens it when `relaunch` is set. The caller quits Courtside right after.
 */
function install({ app, bundle, dir, relaunch, pid = process.pid }) {
  const script = path.join(dir, "install.sh");
  const result = path.join(dir, "result");
  fs.writeFileSync(script, SWAP_SCRIPT, { mode: 0o755 });
  fs.rmSync(result, { force: true });
  fs.rmSync(`${result}.log`, { force: true });
  fs.mkdirSync(path.join(dir, "old"), { recursive: true });
  const backup = path.join(dir, "old", path.basename(bundle));
  const child = spawn("/bin/sh", [script, String(pid), bundle, app, backup, result, relaunch ? "1" : "0"], {
    detached: true, // keeps running after Courtside quits
    stdio: "ignore",
  });
  child.unref();
}

/**
 * How the last install went: null if none was tried, else { installed, version, app, error }, where
 * `app` is the downloaded copy (still there when the swap failed).
 */
function lastInstall(dir) {
  let status;
  try {
    status = fs.readFileSync(path.join(dir, "result"), "utf8").trim();
  } catch {
    return null;
  }
  const staged = readJson(path.join(dir, "staged.json")) ?? {};
  let error = "";
  try {
    error = fs.readFileSync(path.join(dir, "result.log"), "utf8").trim().split("\n").pop() ?? "";
  } catch {
    /* nothing logged */
  }
  return { installed: status === "installed", version: staged.version, app: staged.app, error };
}

/** Deletes downloads and leftovers from earlier updates. */
function clear(dir) {
  return fsp.rm(dir, { recursive: true, force: true });
}

module.exports = { DOWNLOAD_BASE, SWAP_SCRIPT, bundlePath, installProblem, pickZip, stage, install, lastInstall, clear };
