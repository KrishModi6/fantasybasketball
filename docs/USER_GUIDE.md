# User guide

## Getting in

- **Invited by your commissioner?** Open the invite link (or enter the code under *Join with a code*). Create an account, pick your team, and you're in.
- **Starting a league hub?** Create an account, then choose one of:
  - **Connect ESPN league:** paste the league URL. Private leagues also need two browser cookies; see the help text in the form.
  - **Import a file:** CSV/JSON rosters, for Yahoo, Sleeper or anything else.
  - **Try a demo league:** explore with a realistic 10-team league.

Then open **League hub → Invite your league → New link** and share it.

## Pages

**Overview.** Your team at a glance:
- this week's projected score and win probability;
- power rank, projected record, and playoff and title odds;
- the best add/drop right now;
- the live league feed (trades shared, injuries, joins) and NBA news.

**Power rankings.** Every team ranked by *all-play win rate*: how often it would beat every other team, every remaining week. Expand a team to see **why**:
- fantasy points per week from each stat vs league average;
- who produces them;
- empty lineup slots, games stuck on the bench and schedule volume;
- injuries.

**My team** (any team via the switcher)
- **Roster:** projected FP/G, recent form, games in the next 7 days, expected starts and points per week, value over replacement.
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
- **Analyze a trade:** pick both teams and tick players. The result shows each side's gain per week and for the rest of the season, the fantasy-playoff weeks, and playoff and title odds before and after, then a numbered list of the math. "Uneven" trades automatically drop the weakest player or pick up the best free agent, just like in real life.
- **Find trades:** searches every 1- and 2-player package with every team and lists the ones that help you without hurting the other side (so they might accept).
- **League board:** share a trade idea (with a note) so the whole league can see the numbers. "Re-run with today's data" re-evaluates it.

**League hub.** Standings, scoring rules, members and which team each manages. Owners also see invite links, refresh settings, ESPN cookies, and delete.

## Adjusting a projection

If you know something the data doesn't yet (a confirmed starting role, an absence), open the player and choose **Adjust projection for this league**. Overrides are capped at ±15% minutes, apply to the whole league, and are logged in the feed so everyone can see them.

## Desktop & phone

- **Desktop:** install *Courtside Setup.exe* (Windows) or the DMG (macOS). On the sign-in screen, set the server address to your league's Courtside server.
- **Phone:** open the website and use *Add to Home Screen*. It works like an app.

## FAQ

**Why is a player's FP/G different from ESPN's?** ESPN is one of three inputs. The blend weights come from a backtest in which the blend was more accurate than ESPN alone (Methodology page). As the season goes on, this season's games take over stat by stat.

**Why does a trade that adds value still say "neutral"?** The verdict is based on your *lineup-adjusted* output. Two good players who play the same days or positions may not both start. The threshold is ±1% of your rest-of-season points.

**How often does data update?** Automatically every few hours (set by the league owner), and anyone can press the refresh pill at the top.
