// Copies the web build into the desktop bundle.
import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "web", "dist");
const dest = join(here, "..", "web-dist");
if (!existsSync(join(src, "index.html"))) {
  console.error("web/dist is missing; run `npm --prefix ../web run build` first.");
  process.exit(1);
}
rmSync(dest, { recursive: true, force: true });
cpSync(src, dest, { recursive: true });
console.log(`copied ${src} -> ${dest}`);
