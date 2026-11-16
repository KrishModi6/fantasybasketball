# Courtside: notes for Claude

## Mac app: always check the code signature

Every Mac release must ship with a valid ad-hoc code signature. Without one, macOS says
**"Courtside is damaged and can't be opened"**, the self-updater's new copy won't open, and friends have
to download the app again and run `xattr -cr` in Terminal. This happened with every release up to 0.6.4.

- electron-builder 25 does **not** ad-hoc sign for `"identity": "-"` (it looks for a keychain certificate
  named "-", finds none and quietly skips). Keep `"identity": null` in `desktop/package.json` and let
  `desktop/scripts/sign-mac.js` (the `afterPack` hook) do the signing.
- Never remove or bypass the hook, the "Check the Mac app's code signature" step in
  `.github/workflows/release.yml`, or the `codesign --verify` check in `desktop/mac-update.js` `stage()`.
- Whenever a change touches `desktop/` packaging, Electron or electron-builder versions, `extraResources`,
  the PyInstaller engine (`server/courtside-engine.spec`), or anything else that ends up inside
  `Courtside.app`: run the release workflow with `publish=false` on the branch and confirm the Mac job's
  signature step prints `Signature=adhoc` and `valid on disk` before merging.
- To check a published release from Linux: download `Courtside-<version>-mac-arm64.zip`, unzip it and make
  sure `Courtside.app/Contents/_CodeSignature/CodeResources` exists. If it's missing, the release is broken.
- CI runners don't enforce Gatekeeper the way a friend's Mac does, so a green update test alone doesn't
  prove a Mac will open the app. The signature check is what catches this.

## Releases

- Bump `desktop/package.json` (and the two version fields at the top of `desktop/package-lock.json`) for
  every release, and add a `## What's new in X.Y.Z` section at the top of `desktop/release-notes.md` (the
  previous one becomes `## In X.Y.Z`).
- Publishing is pushing the tag `vX.Y.Z` on main after the PR merges. `update-test.yml` then checks that
  the two previous releases update themselves to it on Mac and Windows.

## Writing style

- No em dashes in anything written for the user: release notes, docs, comments, commit messages, replies.
