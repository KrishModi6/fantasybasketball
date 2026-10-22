// Builds the Courtside engine (server/engine.py) with PyInstaller, then runs its self-test.
// Uses server/.venv when it exists, else $PYTHON or `python` (the release workflow's interpreter).
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const server = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "server");
const venv = join(server, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const python = process.env.PYTHON || (existsSync(venv) ? venv : "python");
const exe = join(server, "dist", "courtside-engine", process.platform === "win32" ? "courtside-engine.exe" : "courtside-engine");

function run(cmd, args) {
  const r = spawnSync(cmd, args, { cwd: server, stdio: "inherit" });
  if (r.status !== 0) {
    console.error(`${cmd} ${args.join(" ")} failed (${r.status ?? r.error})`);
    process.exit(1);
  }
}

run(python, ["-m", "PyInstaller", "--noconfirm", "--log-level", "WARN", "courtside-engine.spec"]);
run(exe, ["--self-test"]);
