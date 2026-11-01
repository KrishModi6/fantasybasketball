Courtside runs on your computer: projections, power rankings, matchup simulations, trade analysis and the AI assistant. Nothing to host and no account to make.

## What's new in 0.5.5

- **Opening-week projections match players' first games.** Players start a season below their usual level: over the last three seasons they scored about 9% below it in their first game, 7% in games 2 and 3 and 3% through game 8, then settled. Courtside projected those games at the full season level, so opening-week projections ran 6–11% above what players really scored. Each player's first few games of the season are now projected lower, and opening-week projections land within about 3% of what players scored.
- **A slow first week doesn't drag a player down.** The fewer minutes players get in their first games count as part of that opening dip, not as a smaller role, so his projection for the weeks after isn't marked down for it. Rest-of-season projections come out a little more accurate.
- **Players back from an absence start below their level too.** A player's first game back after missing time ran below his projection, by more the longer he was out: about 4% after one game missed, 11% after three or four, 17% after 10 to 19 and 25% after 20 or more, and the longer absences took a few games to wear off. Players whose season starts late, mostly after an injury, scored a third below their projection in their first game. Their first games back are now projected lower, by the length of the absence, and those games' short minutes don't mark down his projection for the weeks after.
- **Healthy players are expected to play their team's first games.** At the start of a season, with the injury report fresh from training camp, healthy players sit even fewer games than later on: this season, all 90 players on our league's rosters that ESPN listed as active before their team's opener played it. Their chance of sitting a team's first few games is now lower.
- **Early-season score ranges fit.** The Matchups page's 80% range for week 1 held only 65% of real scores, because opening-week projections ran high. With them on target, the ranges for the first two weeks turned out too wide instead: they held 90% and 86% of real scores, and early win odds were too close to 50/50. Early in a season whole rosters don't run hot or cold together the way they do later, so that part of the range now grows in over the first weeks. The ranges hold 83% and 79%, and weekly win odds are a little more accurate.
- **Rookies are projected to grow into their roles.** Over the last three seasons rookies played about half their projected minutes in their first game and took around 20 games to settle in. Courtside read those short early minutes as the role they'd keep, so after 10 games it projected their rest of the season about 10% below what they went on to score. Their early minutes now count toward the role they grow into, and the next few games of a rookie still working into the rotation are projected lower. A rookie already playing big minutes keeps his minutes. Rest-of-season projections for rookies are 4–7% more accurate.
- This season is already under way, so most players are past most of the games this changes. It mostly shows for rookies, who are still early in their climb, and for players who haven't played yet, such as those coming back from an injury. From next October it shows in full.

## In 0.5.4

- **xFP data on time every morning.** The daily file of NBA.com tracking data behind xFP and Luck was published with an AWS sign-in that runs out after 12 hours, so on October 31 it went out at 11:12 a.m. instead of 8:00. It now has its own AWS key that doesn't run out. When the file is late, Courtside fetches the missing day from NBA.com itself, so nothing was lost.
- **Healthy players are expected to play their next few games.** Each player's chance of sitting out used to be his miss rate for the whole season, about 24% for rotation players. But most injuries that would keep a player out in the next few days are already on the injury report: over the last three seasons, healthy rotation players missed only 8% of their next two games. A healthy player's chance of missing his next game is now 30% of his season rate, rising back to the full rate over about 9 games. Players coming back from an injury keep the season rate. Weekly projections no longer shave games off healthy starters, so projected team scores rise about 9%: they ran about 6% below real scores and now land about 2% above.
- **No extra minutes for a team's injuries.** Projections used to scale each NBA team's healthy players toward 236 minutes a game, so that an injury handed out playing time. Checked against 28,526 player-games from the last three seasons, projections were on target without that step and about 7% too high with it for the players it raised the most. Teammates' recent games already show who picks up the minutes, so the step counted it twice. It's gone.
- **Score ranges you can trust.** The 80% range on the Matchups page now holds the real score 80% of the time, where before it held 69%. Ranges are about 6% wider.
- **Better weekly win odds.** Courtside's simulator was checked against 18,240 real weekly matchups from the last three seasons, run on AWS Lambda, and its settings now match how much scores really swing from week to week. Win odds move about 5 points either way on average, with no overall shift toward or away from 50/50.
- **Playoff and title odds are less extreme.** They used to be far too sure: a team shown at 98% now shows about 92%, 83% about 68%, and 2% about 6%, much closer to how often teams like that really made it. Draft-day odds are still a little too sure.
- **Powered by AWS.** The bottom of the league list says so now. Hover it to see what runs there: the daily NBA.com tracking data is published on Amazon S3 and CloudFront, and the simulations are tested at scale on AWS Lambda and re-checked there every morning against this season's results.

## In 0.5.3

- **A title bar that matches Courtside.** On Windows, the white title bar and the File, Edit, View, Window, Help menu bar are gone. Courtside's own top bar is now the title bar, in the dark or light theme, with the minimize, maximize and close buttons at its right end. Drag it to move the window and double-click it to maximize. The menus open from the **☰** button at the left. On a Mac, the top bar leaves room for the window buttons and moves the window when you drag it.
- **What's new.** The first time Courtside opens on a new version, it shows what changed since the version you had, like this list. To see it again, or any earlier version, click **What's new** at the bottom right or choose **Help > What's new in Courtside**.
- **An injured player's IR spot ends when he comes back.** A team with a player on IR was projected to play him and everyone else for the rest of the season, a player more than ESPN allows. Now, the day he's back, a full team drops its weakest player from then on. This mostly shows in trades for injured players: one that looked 64 points a week better comes out at about 10. The trade's calculation names the player dropped and the date.
- **Add/drop never suggests dropping your IR player.** Dropping him frees only the IR slot, which a healthy pickup can't take, so ESPN wouldn't allow the move.
- **Finished games stop counting.** Once a player's game tonight is over, nothing more is expected from it: a player who sat or played short minutes no longer gets the rest of a game that already ended.
- **Pickups play when they can.** A player on waivers counts only the games after his claim clears (the **Streaming** table marks him **waivers**), and the player you'd drop for him keeps playing until then. A free agent can't join a game that has already tipped off.
- **Playoff odds by win percentage.** In a league with an odd number of teams, a team with more byes plays fewer games. Seeding now goes by win percentage instead of total wins, so those teams' playoff odds aren't marked down for it.
- Courtside refreshes your league when it starts on 0.5.3, to read each team's tip-off times and when waiver claims clear.

## In 0.5.2

- **xFP/G and Luck on your roster.** The team page's **Roster** tab now has the **xFP/G** and **Luck** columns from **Players**, next to **Last 10**, so you can see at a glance which of your players are running hot (sell-high) or cold (buy-low). Point at either label for what it means. On a narrow window they hide, like the other extra columns.
- **Team luck on Power rankings.** A new **Luck** column: how many points a week each team's players have been scoring above or below what their chances were worth, counted as often as its best lineups will start them. A team well above zero is likelier to cool off. Select a team for who it comes from, like "hottest Fred VanVleet +22, coldest Caleb Wilson −24".
- **Fairer xFP for rebounds.** NBA.com's rebound counts changed during last season, so xFP expected about 27% more rebounds than anyone was getting. Almost every player looked unlucky, and rebound projections ran high. Expected rebounds, assists, steals, blocks and free throws are now scaled to this season's league totals, the way shots already were. Projections come down a little, about 15–25 points a week per team, and Luck now averages out across the league. Courtside refreshes your league when it starts on 0.5.2, so this applies right away.

## In 0.5.1

- **xFP and Luck show up right after updating.** 0.5.0 added them, but until the next hourly refresh Courtside kept showing the league data the old version had saved, which has none. Now Courtside refreshes your league as soon as it starts on a new version. The projections, power rankings and win odds are recalculated with xFP too.
- **Trades no longer drop a player for no reason.** A team with a day-to-day player still in its IR slot looked one player over the limit, so every trade it made dropped someone (in one 2-for-2, Joel Embiid, about 50 points a week). That player now keeps his IR spot, for every team. And an injured player you get in a 2-for-1 now goes in your empty IR slot instead of costing you a drop, as he would in ESPN.
- **Against today's roster.** A trade's **Change per week** compares each team with picking up free agents for any player who projects below one, so the two sides don't add up to zero. Each side now also shows the change against its roster as it is, and the calculation explains the difference. Point at the label for more.
- **Projected records add up.** The **Proj.** record on **Power rankings**, **Overview** and the team page always adds up to the team's games. In a league with an odd number of teams someone sits out every week, so teams with more bye weeks play fewer games: point at the record to see its games and byes.
- A week that's over but has no result yet is no longer left out of the projected record and the playoff odds.

## In 0.5.0

Advanced stats, powered by AWS.

- **xFP: what a player's chances were worth.** Courtside now reads NBA.com's player tracking: every shot and where and how it was taken, every pass that led to a shot, every rebound chance, deflection and contested shot. From those it works out **xFP/G**, the fantasy points per game his chances were worth, and **Luck**, how far his real scoring is above or below that. A player scoring 45 FP/G on chances worth 38 has **+7.0** luck: he's been hitting more than usual, a sell-high candidate. One at −7.0 is a buy-low.
- **xFP/G and Luck columns on Players.** Click **Luck** to sort: the players running hottest come first. Point at either label for what it means.
- **Advanced stats on the player card.** Points, made shots, threes, free throws, assists, rebounds, steals and blocks per game, as they happened next to what his chances were worth, with the gap for each.
- **Steadier projections early in the season.** A hot or cold start now counts for less: before this season's games are added in, each stat moves part of the way toward what the chances were worth. Tested on the last three seasons, rest-of-season projections came out 2.5–3.3% closer to what players went on to score.
- **Powered by AWS.** The tracking data is published once a day to Amazon Web Services (S3, served by CloudFront), and every copy of Courtside downloads that one small file instead of asking NBA.com on each refresh. If the daily file is ever late, Courtside goes to NBA.com directly like before.
- The **Glossary** has a new **Advanced stats** section.

## In 0.3.5

- **Results of the weeks already played.** **Projected points by week** on **Power rankings** used to start at the current week. Now it starts with the weeks already played, marked **final**: each team's final score with a **W** or **L** next to it (in **Win odds**, the opponent and the result). The **Avg** column averages the real scores for those weeks.
- **Updates install the newest version in one go.** If a new version came out while an update was already downloaded and waiting, Courtside installed the older one and you had to update again (0.3.2 to 0.3.3, then 0.3.3 to 0.3.4). Now it keeps checking while an update waits, and **Update now** looks for a newer one first and installs that instead, with one restart. Starting with the next update after this one.
- **Update now runs once.** Clicking it again, or the version label, while Courtside is restarting no longer starts a second install. The popup says when Courtside will restart on its own once a newer download finishes.

## In 0.3.4

- **Every number explained.** Labels with a dotted underline now show what they mean when you point at them: **FP/G**, **± game**, **ROS**, **Over repl.**, **80% range**, **Swing**, **Power score**, a trade's **verdict** and **Fit**, and the rest. For example, **80% range** is where a team's score lands in 80% of the simulated matchups: one time in 10 it's lower than the first number, one time in 10 higher than the second.
- **Glossary.** A new **Glossary** tab (top right, next to Methodology) lists every term in plain English, grouped by page.
- The matchup simulator says **80% range** instead of just "80%".
- **A more modern look.** Rounded cards with a soft shadow, pill-shaped tabs and filters, rounded buttons, boxes and bars, and a player card that slides in with rounded corners. Same layout and numbers, in both the dark and light themes.

## In 0.3.3

- **Last season stops counting after 15 games.** Once a player has played 15 games this season, his projection comes from this season's games only: points, rebounds, minutes and everything else. Before, last season's stats kept pulling on it well into the season, so a breakout (or a slump) took a long time to show up. The player card says when last season no longer counts.
- **This season's stats on the player card.** The per-game box now shows a **This season** row (minutes, points, rebounds, assists, steals, blocks, turnovers, threes, FG% and FT%) right under the projection, so you can see how a player is actually doing next to what Courtside expects.
- **Trade labels say who comes out ahead.** The trade analyzer used to call almost every trade **balanced**, even when one team's verdict was disadvantageous. Now it's **balanced** only when neither team gains or loses more than 1%, **win-win** or **lose-lose** when both teams gain or both lose, and otherwise names who it **favors**. The **Fit** column in **Find trades**, **Target a player** and **Shop a player** uses the same labels.

On a Mac this is the first update that installs itself: click **Update now** in the popup.

## In 0.3.2

- **Macs update themselves.** No more going back to GitHub and running the Terminal command for every new version. Starting with the version after this one, a Mac downloads updates in the background the way Windows does, and the **New update released** popup offers **Update now**: click it and Courtside reopens on the new version a few seconds later, leagues and all. Or click **Later**, and it installs the next time you quit Courtside. The Terminal command was only ever needed because macOS flags apps downloaded in a browser; Courtside's own downloads aren't flagged.
- **One last manual update.** 0.3.1 and older can't do this yet, so get 0.3.2 the old way, Terminal command included (see **Updating on a Mac** below). It's the last time.

## In 0.3.1

- **Pick both teams in a three-team trade.** On **Find trades > 3 teams**, picking a team under **Including** now shows a second **and** menu. Leave it on **Any team** and Courtside tries every other team as the third, or pick one to only see rotations between you and those two teams, in both directions. Leave **Including** on **Any two teams** to search the whole league as before.

## In 0.3.0

A big trades update: two new ways to find deals, and trade math that matches how real managers trade.

- **Shop a player.** A new tab on **Trades** for when someone wants your player but won't make an offer. Tick up to 3 of your players, pick the team that wants them (or any team), and Courtside lists what you can fairly ask for: packages of their players that are fair to both teams, the most you can get first. Say a league mate keeps asking about your Jamal Murray: pick their team and you'll see which of their players (a Stephen Curry, say) is a fair return. Open it from your own player's card with **What to ask for**, or ask the assistant "what should I ask for ...".
- **Find three-team trades.** **Find trades** has a **3 teams** option. It searches rotations where you send a player to one team, that team sends one to a third, and the third sends one back to you, and lists the ones that help you without either other team losing more than 0.4%. **Analyze** opens any of them in the three-team analyzer. The assistant can search these too.
- **More realistic trade math.** Trades no longer get credit for things you could do on waivers. Before, almost any 2-for-1 scored about +80 points a week for the team getting two players, because it filled an open roster spot, and offers like three bench players for Giannis looked like a win for both teams. Now each team's open spots are filled from free agents before the trade is compared, pickups are the third-best free agent and down (the best go fast), getting an injured player doesn't open an IR spot, and a player who projects below a free agent counts as one. Every trade tool and the assistant use the new math.

## In 0.2.9

- **Three-team trades.** On **Trades > Analyze**, switch to **3 teams** to work out a multi-team deal. Tick the players each team gives, then pick which team gets each one in the **To** column. Every team gets its own result (points per week, rest of season, playoff and title odds) and its own math, and the players table shows where everyone ends up, including free agents picked up to fill an open spot. You can post three-team trades to the **League board** and ask the AI assistant about them too. (ESPN only takes two-team trades, so you'd make the deal there as two trades.)

## In 0.2.8

- **Target a player.** A new tab on **Trades**: tick up to 3 players on another team and Courtside tells you what to offer for them. It tries every offer of 1, 2 or 3 of your players, simulates the best ones for both teams, and lists the ones that team should accept, best for you first. If nothing works, it shows the offers that came closest. Open it from any player's card on another team with **What to offer**, or ask the assistant "what should I offer for ...".
- **3-for-3 trades.** **Find trades** has a new **3-for-3 only** package.
- **Better 2-for-2 suggestions.** Find trades used to shortlist only swaps of exactly equal value, which barely changed either team: the best 2-for-2s added under half a point a week. It now aims for trades the other team still accepts. In a 10-team test league the best 2-for-2s (and 3-for-3s) now add about 4 points a week, and 1-for-1s a bit more than before.

## In 0.2.7

- **Live during games.** While NBA games are on and Courtside is open, it refreshes from ESPN every 3 minutes, and the status bar shows **Live**. Your matchup score and a new **Today** column on the **Team** page (each player's fantasy points so far today) keep up as the games go. Projections, Season and Last 10 wait for tonight's games to finish and take them in the next morning, so a game at halftime no longer reads as a quiet full game.
- **"New update released" popup.** Courtside now checks for new versions every 15 minutes instead of every 4 hours, and pops up as soon as one is out: **Download now** on a Mac, **Update now** on Windows once it's downloaded (a few seconds). You'll see it starting with the version after this one.
- **Teams instead of "1 member".** Everyone runs Courtside on their own computer, so league mates who join with your invite code never show up on yours, and the app always said "1 member". The status bar, the **League** page and **Your leagues** now show how many teams the league has. The League page also drops the "Courtside member" column, which said "unclaimed" for every team but yours.

## In 0.2.6

- **Find trades shows 1-for-1 and 2-for-2 by default.** No more lists full of 2-for-1s: the default **Package** is now "1-for-1 and 2-for-2", and the assistant's trade ideas are even trades too unless you ask for 2-for-1s. Uneven packages (give 2 get 1, give 1 get 2, any size) are still in the **Package** menu.

## In 0.2.5

- **Find trades: 2-for-2 means 2-for-2.** The **Package** option that used to say "Up to 2-for-2" is now **2-for-2** and lists only trades with exactly two players each way, instead of filling up with 2-for-1s. **1-for-1** and **2-for-2** now list up to 12 trades (they stopped at 6). For 2-for-1 deals, pick **Give up to 2, get 1**.

## In 0.2.4

- **Power rankings count this season's record.** The power score used to be projection only. Now your record counts for the share of the regular season already played, and the projection counts for the rest: in week 1 it's all projection, halfway through it's half each, and by the last week it's almost all record. Hover a team's power score to see the mix.

## In 0.2.3

- **Projected points by week** for every team on Power rankings, with a **Win odds** toggle.
- **Projections start at 2024-25**, and each player's last 10 games of 2025-26 count until this season has enough games.
- **The top bar shows your version** and whether it's up to date; click it to check now.
- **Update checks at least once a day**, including when your computer wakes up.

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

From 0.3.2 on, Courtside updates itself. It checks every 15 minutes while it's open and downloads a new version in the background; then a **New update released** popup offers **Update now**, and the version label in the top bar says the new version is ready (for example **0.3.3 ready**). Click either one and Courtside reopens on the new version, or it installs the next time you quit Courtside. Your leagues stay. There's nothing to download and no Terminal command.

If macOS won't let Courtside replace itself, Courtside stays on the old version and says so the next time it opens, with a **Show in Finder** button for the new version it downloaded. Quit Courtside, drag that **Courtside** into Applications and click **Replace**; no Terminal needed for that one either. If macOS asks whether Courtside may modify apps, allow it (or turn Courtside on in **System Settings > Privacy & Security > App Management**) and the next update installs by itself.

**On 0.3.1 or older?** Those versions can't update themselves, so get 0.3.2 by hand, one last time:

1. Click **Download now** in the popup (or the version label) and unzip the new zip from Downloads.
2. Quit Courtside with **Cmd + Q**.
3. Drag the new **Courtside** into Applications and click **Replace**.
4. Run the Terminal command from step 3 of the install steps again, then open Courtside. Your leagues stay.

## First run

Got an invite from your league owner? Choose **Join with invite** and paste it. That's all: no league ID or cookies needed.

Then pick your team once (on Overview or the Team tab). Courtside remembers it and opens every page on it from then on.

Setting up your league yourself? Pick **Connect ESPN** and paste your league's URL. If you add your own ESPN cookies, Courtside recognizes your team automatically. Private leagues also need the `espn_s2` and `SWID` cookies from your browser (the app explains where to find them). Or start with **Demo league** to look around.

Your leagues are stored on your computer only: `%APPDATA%\Courtside` on Windows, `~/Library/Application Support/Courtside` on a Mac.

The **Assistant** tab answers questions like "who should I pick up?" using Courtside's own projections and simulations. It has a daily allowance per computer, or you can add your own OpenAI key on that tab.

To bring the rest of your league in, press **Invite** in the top bar and send them the message it copies.

Both downloads were built from this release's source by the "Build desktop app" workflow, which self-tests the projection engine in the build and in a test installation before publishing.
