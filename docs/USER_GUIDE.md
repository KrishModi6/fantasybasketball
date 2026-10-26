# User guide

## Getting in

- **Invited by your commissioner?** In the desktop app, choose **Join with invite** and paste the message or code they sent (it starts with `CS1.`). Courtside connects the league for you, with no league ID or ESPN cookies to find. On a shared server, open the invite link instead, create an account and pick your team.
- **Starting a league hub?** Create an account, then choose one of:
  - **Connect ESPN league:** paste the league URL. Private leagues also need two browser cookies; see the help text in the form.
  - **Import a file:** CSV/JSON rosters, for Yahoo, Sleeper or anything else.
  - **Try a demo league:** explore with a realistic 10-team league.

Then press **Invite** in the top bar. In the desktop app that makes an invite code: copy the invite message (download link, steps and code) and send it to your league. For a private ESPN league the code includes your ESPN cookies, so send it only to people in your league. On a shared server it makes an invite link instead.

Pick your team once, on Overview or the Team tab, and Courtside remembers it: every page opens on your team after that. If you connected an ESPN league with your own cookies, Courtside recognizes your team from your ESPN login on its next refresh, so there's nothing to pick. You can change it later under **League > Members**.

Courtside reopens where you left off. The back arrow (or your mouse's back button, or Alt+Left) steps back through pages within the league; **All leagues** in the league menu lists every league.

## Pages

**Overview.** Your team at a glance:
- this week's projected score and win probability;
- power rank, projected record, and playoff and title odds;
- the best add/drop right now;
- the live league feed (trades shared, injuries, joins) and NBA news.

**Rankings.** Every team ranked by *power score*. This season's record counts for the share of the regular season already played; for the rest, it's the projected *all-play win rate*: how often the team would beat every other team, every remaining week. In week 1 it's all projection, halfway it's half each. Hover a power score to see the mix. Select a team's row to see **why**:
- fantasy points per week from each stat vs league average;
- who produces them;
- empty lineup slots, games stuck on the bench and schedule volume;
- injuries.

**Team** (any team via the switcher; the league table on the left of wide screens also opens teams)
- **Roster:** projected FP/G, recent form, games in the next 7 days, expected starts and points per week, value over replacement. On game days, **Today** shows each player's fantasy points so far. **Last 10** needs at least 3 games, counting the end of last season while this one is young, so it stays blank for players who sat out last season's final stretch until they've played 3 games this season.
- **Daily lineup:** the optimal lineup for each of the next 7 days. *Confidence* is the chance a starter outscores the best bench player who could take that slot. "Locked in" means nobody could replace him.
- **Improve my team:**
  - weak spots, ranked by points at stake;
  - add/drop moves simulated against your real lineup;
  - this week's streaming adds (with your league's acquisition limit).
- **Schedule:** projected score ± 1 standard deviation for upcoming weeks, including the two-week playoff rounds.

**Players.** Free agents, rostered players, or everyone. Filter by position, search, and sort by projection, rest-of-season value, games next week and more. Click a player to see exactly how the projection was built:
1. the three sources and their weights;
2. how much this season's games count so far;
3. every adjustment applied (injuries, minutes budget, team change), with its cap;
4. what moved the ML model.

**Matchups.** Pick a week and see every scheduled matchup with a quick win estimate, or pair any two teams. The selected matchup is simulated 10,000 times, game by game, and shows:
- each team's chance to win, with its projected score and the range it lands in 80% of the time;
- the distribution of scores and of the winning margin;
- day-by-day expected points and how many games each team has left;
- every player's expected points, range and **swing** (how much a big week from him raises the team's chance to win).

Uncheck a player to see the odds if he sits, e.g. to decide on a questionable starter or whether to stream. Use *Reset lineups* to undo. During a live week, points already scored are included.

**Trades**
- **Analyze a trade:** pick both teams and tick players. For a three-team trade, switch to **3 teams**, tick what each team gives and pick which team gets each player in the **To** column. The result shows each side's gain per week and for the rest of the season, the fantasy-playoff weeks, and playoff and title odds before and after, then a numbered list of the math (for every team in a three-team trade). "Uneven" trades automatically drop the weakest player or pick up the best free agent, just like in real life.
- **Find trades:** searches packages with every team and lists the ones that help you without hurting the other side (so they might accept). By default it looks at **1-for-1 and 2-for-2** trades only. Under **Package** you can pick 1-for-1 only, 2-for-2 only, 3-for-3 only, give 2 get 1, give 1 get 2, or any size up to 2-for-2. If no 2-for-2 or 3-for-3 qualifies, it says how many it checked. Switch to **3 teams** for three-team trades: you send a player to one team, that team sends one on to a third, and the third sends one back to you. That finds deals a two-team trade can't, like when the team with the player you want doesn't want anyone of yours. Pick a team under **Including** to only see rotations with them.
- **Target a player:** tick up to 3 players on another team and click **Find offers**. Courtside tries every offer of 1, 2 or 3 of your players (pick the most under **Offer**), simulates the best ones for both teams, and lists the offers that team should accept, best for you first, grouped by how many players you send. If none works, it shows the offers that came closest. A player's card on another team has a **What to offer** button that opens this with them picked. **Analyze** opens any offer in the analyzer.
- **Shop a player:** for when someone wants one of your players but won't make an offer. Tick up to 3 of your players, pick the team that wants them (or leave **Any team**) and click **Find what to ask for**. Courtside tries every package of their players, simulates the best ones for both teams, and lists the ones that are fair to both sides, the most you can ask for first. By default it asks for as many players as you give; **Ask for** lets you ask for more (they then pick up a free agent for the open spot, which can make a deal look fairer on paper than a manager would think). Your own player's card has a **What to ask for** button that opens this with them picked and searches every team.
- **League board:** share a trade idea (with a note) so the whole league can see the numbers. They're re-scored after every refresh; ideas that went through on ESPN are marked **Happened**, and ones a roster move ruled out are marked **No longer possible**. Above the ideas, **Recent moves** lists the league's real trades, pickups and drops.

**League.** Standings, scoring rules, members and which team each manages. Owners also see invite links, refresh settings, ESPN cookies, and delete. In the desktop app everyone runs their own copy, so league mates who join with your code don't appear as members on your computer; the app shows the number of teams instead.

## Adjusting a projection

If you know something the data doesn't yet (a confirmed starting role, an absence), open the player and choose **Adjust projection for this league**. Overrides are capped at ±15% minutes, apply to the whole league, and are logged in the feed so everyone can see them.

## Desktop & phone

- **Desktop:** install *Courtside Setup.exe* (Windows) or the DMG (macOS). On the sign-in screen, set the server address to your league's Courtside server.
- **Phone:** open the website and use *Add to Home Screen*. It works like an app.

## FAQ

**Why is a player's FP/G different from ESPN's?** ESPN is one of three inputs. The blend weights come from a backtest in which the blend was more accurate than ESPN alone (Methodology page). As the season goes on, this season's games take over stat by stat.

**Why does a trade that adds value still say "neutral"?** The verdict is based on your *lineup-adjusted* output. Two good players who play the same days or positions may not both start. The threshold is ±1% of your rest-of-season points.

**How often does data update?** Every hour by default (the league owner can change it), and again whenever you open Courtside and the data is more than 30 minutes old. While games are on (the status bar says **Live**), every 3 minutes as long as Courtside is open. Tonight's games count toward projections the next morning, once they're final. Anyone can press **Refresh** in the top bar. Trades and waiver moves made on ESPN show up in the league feed and under **Trades > League board** after the next refresh.
