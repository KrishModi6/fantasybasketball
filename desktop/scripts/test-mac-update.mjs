// Runs Courtside's Mac updater (mac-update.js) on the app this build just made, the way an installed
// copy would: downloads the zip from a local server, checks it, swaps it in for an older copy, and
// checks that a swap macOS refuses leaves the old copy working. The release workflow runs it on macOS
// after `npm run dist:mac`.
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const macUpdate = require("../mac-update.js");
const yaml = require("js-yaml");

const release = path.join(import.meta.dirname, "..", "release");
const feed = yaml.load(fs.readFileSync(path.join(release, "latest-mac.yml"), "utf8"));
const info = { ...feed, tag: `v${feed.version}` };
const built = fs.readdirSync(release)
  .map((name) => path.join(release, name, "Courtside.app"))
  .find((app) => fs.existsSync(app));
assert.ok(built, `No Courtside.app in ${release}`);

const plistVersion = (app) => execFileSync("/usr/libexec/PlistBuddy",
  ["-c", "Print :CFBundleShortVersionString", path.join(app, "Contents", "Info.plist")], { encoding: "utf8" }).trim();

async function waitForResult(dir) {
  for (let i = 0; i < 300; i++) {
    const last = macUpdate.lastInstall(dir);
    if (last) return last;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("The swap script never finished.");
}

// Courtside's stand-in: a process that exits a moment after the swap script starts waiting for it.
async function installAndWait(app, bundle, dir) {
  const courtside = spawn("/bin/sleep", ["1"]);
  macUpdate.install({ app, bundle, dir, relaunch: false, pid: courtside.pid });
  return waitForResult(dir);
}

// The release files, served the way GitHub does: /<tag>/<file>.
let downloads = 0;
const server = http.createServer((req, res) => {
  const file = path.join(release, path.basename(decodeURIComponent(new URL(req.url, "http://localhost").pathname)));
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return res.writeHead(404).end();
  downloads++;
  res.writeHead(200, { "content-length": fs.statSync(file).size });
  fs.createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const base = `http://127.0.0.1:${server.address().port}`;

const root = fs.mkdtempSync(path.join(os.tmpdir(), "courtside-update-"));
const applications = path.join(root, "Applications");
const installed = path.join(applications, "Courtside.app");
const dir = path.join(root, "update");
try {
  // An older Courtside in its own Applications folder.
  fs.mkdirSync(applications);
  execFileSync("/usr/bin/ditto", [built, installed]);
  execFileSync("/usr/libexec/PlistBuddy", ["-c", "Set :CFBundleShortVersionString 0.0.1", path.join(installed, "Contents", "Info.plist")]);
  assert.equal(macUpdate.bundlePath(path.join(installed, "Contents", "MacOS", "Courtside")), installed);
  assert.equal(macUpdate.installProblem(installed), null);

  // Download, check and unzip.
  const progress = [];
  const app = await macUpdate.stage({ info, dir, base, fetch, onProgress: (p) => progress.push(p) });
  assert.equal(plistVersion(app), feed.version);
  assert.ok(progress.length > 1, "no download progress was reported");
  assert.equal(await macUpdate.stage({ info, dir, base, fetch }), app, "a second check should reuse the download");
  assert.equal(downloads, 1);
  await assert.rejects(macUpdate.stage({ info: { ...info, files: info.files.map((f) => ({ ...f, sha512: "x" })) },
    dir: path.join(root, "bad"), base, fetch }), /checksum/);
  console.log(`Downloaded and checked ${feed.version}: ${app}`);

  // The swap.
  let last = await installAndWait(app, installed, dir);
  assert.equal(last.installed, true, `the swap failed: ${last.error}`);
  assert.equal(plistVersion(installed), feed.version);
  assert.ok(!fs.existsSync(app), "the downloaded copy should have moved into place");
  assert.ok(!fs.existsSync(path.join(dir, "old", "Courtside.app")), "the old copy should be gone");
  execFileSync(path.join(installed, "Contents", "Resources", "engine", "courtside-engine"), ["--self-test"], { stdio: "inherit" });
  console.log(`Swapped ${feed.version} in for 0.0.1, and the installed engine passes its self-test.`);

  // macOS refuses to move the app (here, a read-only Applications folder): the old copy stays.
  const again = await macUpdate.stage({ info, dir, base, fetch });
  fs.chmodSync(applications, 0o555);
  try {
    assert.ok(macUpdate.installProblem(installed), "a read-only Applications folder should be reported");
    last = await installAndWait(again, installed, dir);
  } finally {
    fs.chmodSync(applications, 0o755);
  }
  assert.equal(last.installed, false);
  assert.equal(last.app, again);
  assert.ok(fs.existsSync(again), "the downloaded copy should still be there to drag over by hand");
  assert.equal(plistVersion(installed), feed.version, "the installed copy should be untouched");

  // The new copy can't be moved in after the old one is moved out: the old one goes back.
  fs.rmSync(again, { recursive: true });
  last = await installAndWait(again, installed, dir);
  assert.equal(last.installed, false);
  assert.equal(plistVersion(installed), feed.version, "the old copy should be put back");
  console.log("A refused swap leaves the installed copy in place.");
} finally {
  server.close();
  if (fs.existsSync(applications)) fs.chmodSync(applications, 0o755);
  fs.rmSync(root, { recursive: true, force: true });
}
