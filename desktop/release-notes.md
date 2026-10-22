Courtside runs on your computer: projections, power rankings, matchup simulations, trade analysis and the AI assistant. Nothing to host and no account to make.

## What's new in 0.2.3

- **Projected points by week for every team.** Power rankings now has a grid of each team's projected points for every remaining week, with this week counting the points already scored. Switch it to **Win odds** to see each team's chance against that week's opponent. Hover a cell for the opponent and range.
- **Projections start at 2024-25.** The statistical baseline now uses only the last two seasons (2024-25 and 2025-26) instead of three, and player pages show seasons from 2024-25 on. Retested on past seasons, this was slightly more accurate.
- **Recent games count from day one.** Until this season has enough games, each player's last 10 games of 2025-26 also feed the projection, at a quarter of the weight of a game this season. Replaying the last two seasons, this made early-season projections about 1% more accurate. The **Last 10** column and the player page now include those games too, so they aren't blank in the first week.
- **See which version you're on.** The top bar shows your version (v0.2.3) and whether it's up to date. Click it to check for updates now, or to get the new version when one is out.
- **Update checks at least once a day.** Courtside already checked when it opened and every 4 hours, but those timers pause while the computer sleeps. It now also checks when your computer wakes up or when you come back to Courtside after a few hours, so a new version shows up within a day.

Already on 0.2.0 or later? This update installs itself. Restart Courtside when it asks.

## Install on Windows

1. Download **Courtside-Setup-<version>.exe** below and double-click it.
2. Windows may say "Windows protected your PC", because the installer isn't code-signed (that needs a paid certificate). Click **More info**, then **Run anyway**.
3. Click through the installer. No administrator password and no Python are needed.

Start Courtside from the Start menu or the desktop shortcut. To remove it, use **Settings > Apps > Courtside > Uninstall**.

**Updates are automatic from now on.** Courtside downloads new versions in the background and asks you to restart; if you don't, it updates the next time you close it. Your leagues stay. (Coming from 0.1.0? Install this version once by hand. After that you won't need this page again.)

## Install on a Mac (Apple Silicon: M1 or newer)

1. Download **Courtside-<version>-mac-arm64.zip** below and double-click it to unzip.
2. Drag **Courtside** into your **Applications** folder.
3. Open it. macOS will say it can't verify the developer, because the app isn't notarized by Apple. Click **Done**, open **System Settings > Privacy & Security**, scroll down and click **Open Anyway** next to Courtside, then confirm.

You only do step 3 once.

When a new version is out, Courtside shows a banner with a **Download** button. Replace the old app in Applications with the new one; your leagues stay. (Macs can't update by themselves because the app isn't signed with a paid Apple developer certificate.)

## First run

Got an invite from your league owner? Choose **Join with invite** and paste it. That's all: no league ID or cookies needed.

Then pick your team once (on Overview or the Team tab). Courtside remembers it and opens every page on it from then on.

Setting up your league yourself? Pick **Connect ESPN** and paste your league's URL. If you add your own ESPN cookies, Courtside recognizes your team automatically. Private leagues also need the `espn_s2` and `SWID` cookies from your browser (the app explains where to find them). Or start with **Demo league** to look around.

Your leagues are stored on your computer only: `%APPDATA%\Courtside` on Windows, `~/Library/Application Support/Courtside` on a Mac.

The **Assistant** tab answers questions like "who should I pick up?" using Courtside's own projections and simulations. It has a daily allowance per computer, or you can add your own OpenAI key on that tab.

To bring the rest of your league in, press **Invite** in the top bar and send them the message it copies.

Both downloads were built from this release's source by the "Build desktop app" workflow, which self-tests the projection engine in the build and in a test installation before publishing.
