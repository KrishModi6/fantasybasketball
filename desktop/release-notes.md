Courtside runs on your computer: projections, power rankings, matchup simulations, trade analysis and the AI assistant. Nothing to host and no account to make.

## Install on Windows

1. Download **Courtside-Setup-<version>.exe** below and double-click it.
2. Windows may say "Windows protected your PC", because the installer isn't code-signed (that needs a paid certificate). Click **More info**, then **Run anyway**.
3. Click through the installer. No administrator password and no Python are needed.

Start Courtside from the Start menu or the desktop shortcut. To update, run a newer installer over it; your leagues stay. To remove it, use **Settings > Apps > Courtside > Uninstall**.

## Install on a Mac (Apple Silicon: M1 or newer)

1. Download **Courtside-<version>-mac-arm64.zip** below and double-click it to unzip.
2. Drag **Courtside** into your **Applications** folder.
3. Open it. macOS will say it can't verify the developer, because the app isn't notarized by Apple. Click **Done**, open **System Settings > Privacy & Security**, scroll down and click **Open Anyway** next to Courtside, then confirm.

You only do step 3 once.

## First run

Pick **Connect ESPN** and paste your league's URL. Private leagues also need the `espn_s2` and `SWID` cookies from your browser (the app explains where to find them). Or start with **Demo league** to look around.

Your leagues are stored on your computer only: `%APPDATA%\Courtside` on Windows, `~/Library/Application Support/Courtside` on a Mac.

The **Assistant** tab answers questions like "who should I pick up?" using Courtside's own projections and simulations. It has a daily allowance per computer.

Both downloads were built from this release's source by the "Build desktop app" workflow, which self-tests the projection engine in the build and in a test installation before publishing.
