Courtside runs on your computer: projections, power rankings, matchup simulations, trade analysis and the AI assistant. Nothing to host and no account to make.

## What's new in 0.2.3

- **Projected points by week for every team.** Power rankings now has a grid of each team's projected points for every remaining week, with this week counting the points already scored. Switch it to **Win odds** to see each team's chance against that week's opponent. Hover a cell for the opponent and range.
- **Projections start at 2024-25.** The statistical baseline now uses only the last two seasons (2024-25 and 2025-26) instead of three, and player pages show seasons from 2024-25 on. Retested on past seasons, this was slightly more accurate.
- **Recent games count from day one.** Until this season has enough games, each player's last 10 games of 2025-26 also feed the projection, at a quarter of the weight of a game this season. Replaying the last two seasons, this made early-season projections about 1% more accurate. The **Last 10** column and the player page now include those games too, so they aren't blank in the first week.
- **See which version you're on.** The top bar shows your version (v0.2.3) and whether it's up to date. Click it to check for updates now, or to get the new version when one is out.
- **Update checks at least once a day.** Courtside already checked when it opened and every 4 hours, but those timers pause while the computer sleeps. It now also checks when your computer wakes up or when you come back to Courtside after a few hours, so a new version shows up within a day.

Already on 0.2.0 or later? On Windows this update installs itself: restart Courtside when it asks. On a Mac, follow **Updating on a Mac** below.

## Install on Windows

1. Download **Courtside-Setup-<version>.exe** below and double-click it.
2. Windows may say "Windows protected your PC", because the installer isn't code-signed (that needs a paid certificate). Click **More info**, then **Run anyway**.
3. Click through the installer. No administrator password and no Python are needed.

Start Courtside from the Start menu or the desktop shortcut. To remove it, use **Settings > Apps > Courtside > Uninstall**.

**Updates are automatic from now on.** Courtside downloads new versions in the background and asks you to restart; if you don't, it updates the next time you close it. Your leagues stay. (Coming from 0.1.0? Install this version once by hand. After that you won't need this page again.)

## Install on a Mac (Apple Silicon: M1 or newer)

The Mac app isn't signed with a paid Apple developer certificate, so macOS makes you approve it once. These steps are written for macOS Sequoia (15) and later; older versions work the same way.

### 1. Download and unzip

1. Download **Courtside-<version>-mac-arm64.zip** below.
2. Open **Downloads** in Finder and double-click the zip. A **Courtside** app (basketball icon) appears next to it.

### 2. Move it to Applications

1. Click the Finder icon in the Dock, then press **Cmd + Shift + A** (or choose **Go > Applications** in the menu bar). The Applications folder opens.
2. Open a second Finder window with **Cmd + N** and go to **Downloads** with **Cmd + Option + L**.
3. Drag **Courtside** from Downloads into the Applications window. If it asks to replace an older Courtside, click **Replace**. Your leagues are stored separately, so they stay.

Don't see Applications in the Finder sidebar? Choose **Finder > Settings**, open the **Sidebar** tab and tick **Applications**.

### 3. Let macOS open it (first time only)

1. Open **Terminal**: press **Cmd + Space**, type **Terminal** and press **Return**.
2. Copy this line, paste it into Terminal with **Cmd + V** and press **Return**. Nothing prints if it worked.

   ```
   xattr -cr /Applications/Courtside.app
   ```

3. Open Courtside from Applications, or press **Cmd + Space**, type **Courtside** and press **Return**.

Why this step? macOS flags apps downloaded in a browser, and for apps without a paid Apple signature it says **"Courtside is damaged and can't be opened"** with **Cancel** and **Move to Trash**. It isn't damaged. Click **Cancel** (never Move to Trash) and run the command above; it clears the download flag.

**If you see a different message:**

- **"Apple could not verify 'Courtside' is free of malware"** with **Done**: click **Done**, open **Apple menu > System Settings > Privacy & Security**, scroll down to **Security**, click **Open Anyway** next to Courtside and enter your Mac password. Open Courtside again and click **Open Anyway**. (Right-click > Open no longer works for this since macOS Sequoia.)
- **"Terminal would like to modify apps on your Mac"**: click **Allow**, then run the command again.
- **"Operation not permitted"** in Terminal: open **System Settings > Privacy & Security > App Management**, turn on **Terminal**, quit Terminal (**Cmd + Q**), reopen it and run the command again.
- **"No such file or directory"** in Terminal: Courtside isn't in Applications yet. Go back to step 2.
- **"You can't open the application because it is not supported on this type of Mac"**: this build is for Apple Silicon (M1 or newer) only. Intel Macs aren't supported yet.
- It shows **"Starting the projection engine…"** and then **"Courtside could not start"**: run the Terminal command from step 3 again and reopen Courtside. If it still fails, the details are in `~/Library/Application Support/Courtside/logs/engine.log`.

To keep Courtside in the Dock, right-click its icon while it's open and choose **Options > Keep in Dock**. You can delete the zip from Downloads afterwards.

### Updating on a Mac

Macs can't install updates by themselves (that needs the paid certificate), so Courtside tells you instead. It checks at least once a day; when a new version is out, the version label in the top bar turns blue (for example **0.2.4 available**) and a banner offers **Download**.

1. Click **Download** (or the version label) and unzip the new zip from Downloads.
2. Quit Courtside with **Cmd + Q**.
3. Drag the new **Courtside** into Applications and click **Replace**.
4. Run the Terminal command from step 3 again, then open Courtside. Your leagues stay.

## First run

Got an invite from your league owner? Choose **Join with invite** and paste it. That's all: no league ID or cookies needed.

Then pick your team once (on Overview or the Team tab). Courtside remembers it and opens every page on it from then on.

Setting up your league yourself? Pick **Connect ESPN** and paste your league's URL. If you add your own ESPN cookies, Courtside recognizes your team automatically. Private leagues also need the `espn_s2` and `SWID` cookies from your browser (the app explains where to find them). Or start with **Demo league** to look around.

Your leagues are stored on your computer only: `%APPDATA%\Courtside` on Windows, `~/Library/Application Support/Courtside` on a Mac.

The **Assistant** tab answers questions like "who should I pick up?" using Courtside's own projections and simulations. It has a daily allowance per computer, or you can add your own OpenAI key on that tab.

To bring the rest of your league in, press **Invite** in the top bar and send them the message it copies.

Both downloads were built from this release's source by the "Build desktop app" workflow, which self-tests the projection engine in the build and in a test installation before publishing.
