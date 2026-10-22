// One-time setup for the Courtside AI proxy. From the repository root:
//
//   node cloudflare/ai-proxy/setup.mjs
//
// 1. Signs Wrangler in to Cloudflare (a browser tab asks you to allow it) if needed.
// 2. Deploys the Worker (the D1 database and schema already exist; see wrangler.toml).
// 3. Makes a new client token and stores it as the Worker's CLIENT_TOKEN and as the
//    COURTSIDE_AI_TOKEN GitHub secret, with COURTSIDE_AI_URL, so release builds can call it.
// 4. Optionally asks Wrangler to store your OpenAI key as the Worker's OPENAI_API_KEY. You type
//    it into Wrangler's own prompt; this script never reads it. Or add it in the dashboard later.
//
// Re-running it rotates the client token: publish a new release afterwards so the app matches.
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline/promises";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const win = process.platform === "win32";

function run(cmd, args, { cwd = here, input, capture = false } = {}) {
  const r = spawnSync(cmd, args, {
    cwd, input, shell: win, encoding: "utf8",
    stdio: [input === undefined ? "inherit" : "pipe", capture ? "pipe" : "inherit", capture ? "pipe" : "inherit"],
  });
  if (r.status !== 0) {
    if (capture) process.stderr.write(`${r.stdout ?? ""}${r.stderr ?? ""}`);
    throw new Error(`${cmd} ${args[0]} ${args[1] ?? ""} failed`);
  }
  return `${r.stdout ?? ""}${r.stderr ?? ""}`;
}
const wrangler = (args, opts) => run("npx", ["--yes", "wrangler@4", ...args], opts);

async function main() {
  run("gh", ["auth", "status"], { cwd: repo, capture: true });

  const who = spawnSync("npx", ["--yes", "wrangler@4", "whoami"], { cwd: here, shell: win, encoding: "utf8" });
  if (/not authenticated/i.test(`${who.stdout}${who.stderr}`)) {
    console.log("\nSigning Wrangler in to Cloudflare. Allow access in the browser tab that opens.\n");
    wrangler(["login"]);
  }

  console.log("\nDeploying the courtside-ai Worker…");
  const out = wrangler(["deploy"], { capture: true });
  const url = out.match(/https:\/\/courtside-ai\.[a-z0-9-]+\.workers\.dev/)?.[0];
  if (!url) throw new Error(`Deployed, but couldn't find the Worker URL in Wrangler's output:\n${out}`);
  console.log(`Deployed: ${url}`);

  const token = randomBytes(32).toString("base64url");
  wrangler(["secret", "put", "CLIENT_TOKEN"], { input: token, capture: true });
  run("gh", ["secret", "set", "COURTSIDE_AI_URL", "--body", url], { cwd: repo, capture: true });
  run("gh", ["secret", "set", "COURTSIDE_AI_TOKEN"], { cwd: repo, input: token, capture: true });
  console.log("Client token stored in the Worker and as GitHub secrets COURTSIDE_AI_URL / COURTSIDE_AI_TOKEN.");

  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const answer = (await rl.question("\nAdd your OpenAI API key to the Worker now? [y/N] ")).trim().toLowerCase();
  rl.close();
  if (answer === "y" || answer === "yes") {
    console.log("Paste the key at Wrangler's prompt (it isn't shown or saved anywhere else).");
    wrangler(["secret", "put", "OPENAI_API_KEY"]);
  } else {
    console.log("Later: Cloudflare dashboard > Workers & Pages > courtside-ai > Settings > Variables and Secrets > Add (Secret) OPENAI_API_KEY.");
  }

  const health = await fetch(`${url}/health`).then((r) => r.json()).catch(() => null);
  console.log(`\nHealth: ${JSON.stringify(health)}`);
  console.log(health?.ready ? "The AI proxy is ready." : "Not ready yet: add OPENAI_API_KEY, then check /health again.");
  console.log("\nPublish a desktop build that includes the assistant:\n  gh workflow run release.yml -f publish=true\n");
}

main().catch((err) => {
  console.error(`\nSetup stopped: ${err.message}`);
  process.exit(1);
});
