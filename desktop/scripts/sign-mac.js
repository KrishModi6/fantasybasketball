// electron-builder afterPack hook: gives the Mac app a real ad-hoc code signature before it's zipped.
//
// Why: electron-builder 25 treats `"identity": "-"` as the name of a keychain certificate, finds none
// and skips signing. Every Mac release up to 0.6.4 shipped that way: each program kept only the bare
// "linker" signature it was compiled with, and the app had no signature of its own (no
// Contents/_CodeSignature). macOS calls an app like that "damaged and can't be opened", so a friend
// had to download it again and run `xattr -cr` in Terminal.
//
// Now package.json sets `"identity": null` (electron-builder skips signing on purpose) and this hook
// signs every program in the app, innermost first, then the app itself, with the ad-hoc identity "-"
// (free, no Apple Developer ID). It then checks the signature the way macOS does and stops the build if
// it isn't valid. The release workflow checks it again on the zip that ships.
//
// The engine in Contents/Resources/engine is left as PyInstaller signed it (already a full ad-hoc
// signature): there it's sealed as part of the app's resources, so a changed file still breaks the seal.

const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const MACHO_MAGICS = new Set([0xfeedfacf, 0xcffaedfe, 0xfeedface, 0xcefaedfe, 0xcafebabe, 0xbebafeca]);

function isMachO(file) {
  const fd = fs.openSync(file, "r");
  try {
    const buf = Buffer.alloc(4);
    return fs.readSync(fd, buf, 0, 4, 0) === 4 && MACHO_MAGICS.has(buf.readUInt32BE(0));
  } finally {
    fs.closeSync(fd);
  }
}

/** Every program and nested bundle (.app, .framework) in `app`, outside Contents/Resources. */
function codeIn(app) {
  const resources = path.join(app, "Contents", "Resources");
  const found = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isSymbolicLink() || full === resources) continue; // a framework's links point at Versions/A
      if (entry.isDirectory()) {
        walk(full);
        if (entry.name.endsWith(".app")) found.push(full);
        // A framework is signed at its version folder (Foo.framework/Versions/A).
        if (entry.name === "A" && path.basename(path.dirname(dir)).endsWith(".framework")) found.push(full);
      } else if (entry.isFile() && isMachO(full)) {
        found.push(full);
      }
    }
  };
  walk(app);
  // Innermost first: a bundle's signature covers what's inside it, so the inside has to be signed first.
  return found.sort((a, b) => b.split(path.sep).length - a.split(path.sep).length);
}

function codesign(args) {
  execFileSync("/usr/bin/codesign", args, { stdio: ["ignore", "inherit", "inherit"] });
}

/** Ad-hoc signs `app` inside out and checks the result. */
function signApp(app) {
  // codesign refuses files that carry Finder info or other extended attributes.
  execFileSync("/usr/bin/xattr", ["-cr", app]);
  const parts = codeIn(app);
  for (const part of parts) codesign(["--force", "--sign", "-", "--timestamp=none", part]);
  codesign(["--force", "--sign", "-", "--timestamp=none", app]);
  codesign(["--verify", "--deep", "--strict", "--verbose=2", app]);
  console.log(`  • ad-hoc signed ${parts.length} programs and bundles in ${path.basename(app)}, and the app; the signature checks out`);
}

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "darwin") return;
  signApp(path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`));
};
exports.signApp = signApp;
