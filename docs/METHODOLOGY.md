# Methodology & model card

Courtside answers three questions: *how good is each player for the rest of the season*, *how strong is each fantasy team*, and *what does a roster move or trade change*. The guiding rule is **statistics first**. Box-score rates and published projections drive every number; machine learning is included only because a rolling backtest shows it lowers error; news enters only through small, capped, labelled adjustments.

The live version of this page (with the current model's numbers) is in the app under **Methodology**. The raw figures are in `server/data/model/metrics.json`.

---

## 1. Player projections

### 1.1 Statistical baseline: SPS

The baseline follows Basketball-Reference's *Simple Projection System* [1], itself modelled on Tango's Marcel [2].

- **Rates, not totals.** The model projects per-minute rates for 14 stats: REB, AST, STL, BLK, TO, FG made, FG missed, FT made, FT missed, 3PM, 3P missed, DD, TD and PF. Attempts are makes + misses, and points = 2·FGM + 3PM + FTM, so shooting efficiency and volume stay consistent.
- **Weighting.** The last two seasons are weighted 6 / 2: for 2026‑27, that's 2025‑26 and 2024‑25, and nothing older. SPS itself uses three seasons at 6 / 3 / 1; dropping the oldest was slightly more accurate in the backtest (season-total error 448 → 446, §2).
- **Regression to the mean.** The baseline adds 1,000 minutes of league-average production, at each season's league rate, weighted the same way. A rookie with 300 minutes is pulled strongly toward average; a veteran with 8,000 barely moves.
- **Aging.** Rates are multiplied by `1 + (28 − age) × 0.004` below 28 and `1 + (28 − age) × 0.002` above. The sign is reversed for misses, turnovers and fouls.
- **Minutes and availability.** SPS deliberately doesn't project these. Courtside uses the same weighting with priors: minutes shrink toward 18 MPG with 6 weighted games of prior weight; availability is games played ÷ team games, shrunk toward 76%.

### 1.2 Published projections: ESPN

ESPN's preseason projection for each player, converted to per-minute rates, minutes and availability. It appears in the data pull as `statSourceId = 1`. Other projection sources can be added the same way, as `ExpertProjection` records.

### 1.3 Machine learning: LightGBM residual model

One gradient-boosted model per target [3] (minutes, availability and each of the 14 rates) predicts the **residual from the SPS baseline**. With no signal it falls back to the statistics. Features, all knowable before the season:

- the SPS projection
- last season's and two seasons ago per-36 rates and minutes
- games-played fractions
- weighted minutes of history
- age and years in the league
- ESPN's projected rates, minutes and games

Training uses per-row weights (minutes or games played) and conservative hyperparameters: 15 leaves, learning rate 0.03, 260 rounds, L2 regularisation of 3.

**Draft position (ADP) is deliberately excluded.** It is market opinion, not statistics. ESPN also overwrites it once a season ends: every 2025‑26 player now shows the "undrafted" value of 140. A model that leaned on ADP failed badly in exactly that season.

### 1.4 Ensemble

For each target, the three sources (SPS, ESPN, ML) are combined with **convex weights**: non-negative and summing to 1. The weights are fitted to minimise weighted squared error on *out-of-fold* predictions, i.e. stacking [4] over forecast combination [5]. When a source is missing for a player (no ESPN projection), the remaining weights are renormalised.

Current weights:

| Target | SPS | ESPN | ML |
|---|---|---|---|
| Minutes | 7% | 30% | 64% |
| Games played (availability) | 10% | 4% | 86% |
| Rebounds | 10% | 60% | 29% |
| Assists | 7% | 54% | 39% |
| Steals | 38% | 20% | 42% |
| Blocks | 18% | 60% | 22% |
| 3PM | 47% | 24% | 29% |
| Turnovers | 22% | 29% | 49% |

These match intuition. Steals and threes are noisy year to year, so the heavily regressed baseline earns weight there. ESPN knows about roles, so it dominates rebounds, assists and blocks. ML is best at minutes and durability.

In the UI, each source also gets a single "effective weight": the per-target weights averaged by each target's share of fantasy points under your league's scoring.

### 1.5 In-season updating (Bayesian shrinkage)

During the season, each rate is updated as a Gamma–Poisson posterior [6]:

```
rate = (K · preseason_rate + Σ w_g · stat_g) / (K + Σ w_g · minutes_g)
```

- `w_g = 0.5^(games_ago / 25)` is the exponential recency weight.
- Early in a season the sum also includes the player's **last 10 games of last season** (ESPN returns each player's last 20 team games, which reach back across the summer), behind this season's games and at a quarter of the weight: `w_g = 0.25 · 0.5^(games_ago / 25)`. Those games are already part of last season's line in the preseason projection, hence the discount. They fade out as this season's games pile up.
- From a player's **15th game** this season, last season stops counting: `K` is 0 and last season's games drop out, so rates, minutes per game and game-to-game volatility come from this season's games alone (still recency-weighted). The preseason projection is built from last season's stats, so it drops out too. Availability (share of games played) still comes from the preseason projection. This is a product choice, not the most accurate setting: in the recency backtest below it raised the error at 15 games from 4.11 to 4.86 FP/G (2025‑26) and from 3.61 to 4.38 (2024‑25), and at 35 games by 3-4%.
- `K` is the **prior strength in minutes**, estimated per stat from the ensemble's own backtest errors by method of moments: `K = mean_rate / (Var(actual − projected) − φ · E[rate / minutes])`.
- φ is the measured overdispersion of game counts relative to Poisson (from 2025‑26 game logs).
- Established players, with more weighted history, get a stronger prior than young or low-minute players.

| Stat | K (young / low data) | K (established) |
|---|---|---|
| Rebounds | 150 min | 393 min |
| Assists | 150 | 232 |
| Steals | 599 | 1,140 |
| Blocks | 191 | 606 |
| 3PM | 164 | 423 |
| Turnovers | 296 | 564 |

Steals need about 3× more minutes than rebounds before this season's sample dominates. Minutes per game update faster: 8 games of prior weight with a 10-game half-life, because roles change. A player's minutes in his first games of a season, and in his first games back from an absence, count scaled back up by the opening and return dips (§1.5.2, the opening dip deeper for a rookie and for a second-year player in a small role), so a short first week doesn't lower his level.

How quickly old evidence fades is tested, not assumed. `scripts/backtest_recency.py` replays every game of 2024-25 and 2025-26: each player starts from the preseason projection the app would have made (ML refit on earlier seasons only), sees the closing games of the season before, takes in his first 0 to 35 games, and is scored on his rest-of-season FP/G. Fading faster (12- and 5-game half-lives) was about 0.4-0.5% less accurate, and no recency weighting was 0.9-2.3% less accurate. Adding last season's last 10 games at a quarter weight cut the error by 0.8% overall and 1.1% over a player's first 0-3 games (2025‑26: 4.51 → 4.45 FP/G; 2024‑25: 3.87 → 3.86). Half weight, or all 20 games, did better in 2025‑26 but worse in 2024‑25.

Game-to-game volatility of fantasy points is the player's recent standard deviation, shrunk toward the population coefficient of variation (0.51, measured).

### 1.5.1 Expected fantasy points (xFP)

NFL Next Gen Stats, F1 Insights and the NBA's own Inside the Game stats (all built on AWS) follow the same pattern. Tracking data describes each opportunity, a model prices it, and the gap between actual and expected is treated as luck. Courtside applies this to the stats that drive fantasy points, using NBA.com's public tracking dashboards and every shot in its shot log (`fbb/xfp.py`):

| Stat | Opportunity (NBA.com) | Priced at |
|---|---|---|
| FGM, 3PM | Every shot: distance, court x/y, shot type (layup, step-back...), zone, period and clock | A LightGBM shot model for a league-average shooter, scaled to this season's league make rate, plus the player's own shot-making above the model |
| FTM | Free-throw attempts | His FT% |
| AST | Potential assists (passes that led to a shot) | His assists per potential assist |
| REB | Rebound chances (within 3.5 ft of the ball) | His rebounds per chance |
| STL | Deflections | His steals per deflection |
| BLK | Contested two-pointers | His blocks per contest |

"His" rate means his last two seasons (the older one at half weight), shrunk toward the league rate by a pseudo-count of opportunities (400 shots, 300 threes, 150 free throws, 200 potential assists, 300 rebound chances, 120 deflections, 300 contests), so a rookie converts at the league rate. Like the shot model, those rates are then scaled so the whole league's expected free throws, assists, rebounds, steals and blocks add up to what happened this season (`calibrate_lines`). NBA.com's counts drift between seasons: rebounds per rebound chance held near 0.58 for years, climbed to 0.70 during 2025‑26 and are back near 0.53, which, unscaled, made every player's rebounds look 27% unlucky and pushed rebound projections up. That drift is nobody's luck. On each season's unseen shots, the shot model beats league FG% by zone (log loss 0.636 vs 0.658).

During the season, before the update in 1.5, this season's makes, assists, rebounds, steals and blocks move part of the way from what happened toward expected. Attempts, minutes and turnovers stay as they happened. The weights are fitted by `scripts/backtest_xfp.py`: FG 0.70, 3P 1.00, FT 0.90, AST 0.50, REB 0.95, STL 0.75, BLK 0.25. A weight of 1 means only the opportunities count. Threes and rebounds are almost all opportunity; blocks are mostly skill.

The backtest replays 2023‑24, 2024‑25 and 2025‑26 at six points (7 to 91 days after opening night). Every player with 40+ games is projected the way the app would have done it at that date, and the projection is scored on his rest-of-season FP/G. Each season's weights are fitted on the other two seasons, and its shot model is trained on earlier seasons only:

| Season | Without xFP | With xFP | Change | At day 42 |
|---|---|---|---|---|
| 2023‑24 | 4.25 FP/G | 4.15 | −2.5% | −4.5% |
| 2024‑25 | 3.92 | 3.79 | −3.3% | −9.5% |
| 2025‑26 | 4.41 | 4.27 | −3.2% | −5.0% |

The gain is largest after a player's 15th game, when the preseason prior drops out and this season's sample stands alone. Across 5,033 player-dates, each FP/G a player ran above his xFP so far was followed by 0.83 FP/G less over the rest of the season. Of the players 3+ FP/G above xFP, 69% scored less the rest of the way (40% of all players did). Of those 3+ below, 79% scored more (60% of all players). The player list shows xFP/G and luck (FP/G minus xFP/G), and the AI assistant gets both. `scripts/xfp_report.py` prints this season's luck board.

stats.nba.com does not answer cloud hosts (AWS, Azure, GitHub Actions), so tracking is fetched during a league sync on the desktop app or a home server. Each sync downloads only days not already cached. Where NBA.com can't be reached, projections are what they were without xFP.

### 1.5.2 A player's first games of a season and back from an absence (the opening and return dips)

Opening-week projections used to run 6–11% above what players scored, per game, in 2023‑24 to 2025‑26. The preseason level was not the problem: it was 0.98–0.99 of the same players' FP/G over the whole season. Their first games were. Week 1 ran 8–11% below those players' rest-of-season FP/G (0.906, 0.891 and 0.924 of it in 2023‑24, 2024‑25 and 2025‑26). In 2022‑23 to 2025‑26 box scores, players who played their team's opener scored, against the preseason projection the app would have made: 0.91 in game 1, 0.93 in games 2 and 3, 0.97 in games 4 and 5, 0.975 in games 6 to 8, and about 1 after that. About three fifths of the gap is minutes, the rest is output per minute.

So in his *n*-th game of the season (1 = his first), a player is projected at a share of his level:

```
minutes × (1 − 0.07·e),  output per minute × (1 − 0.05·e),  e = exp(−(n − 1) / 2)
```

| Game of the season | 1 | 2 | 3 | 4 | 5 | 6 | 8 |
|---|---|---|---|---|---|---|---|
| Expected FP vs his level | 0.88 | 0.93 | 0.96 | 0.97 | 0.98 | 0.99 | 1.00 |

- **Weekly windows, the matchup simulator and the daily lineup** (§3–5) scale each day's expected points by his factor for that game. The count starts from the games he has played, after any games he's expected to miss.
- **The update (§1.5)** counts his first games' minutes scaled back up by the dip, so a slow start doesn't lower his level for the weeks after. Output per minute counts as it happened: scaling it up too raised projections once the preseason prior drops out (game 15) and made the 2025‑26 rest-of-season error worse (4.58 vs 4.57 FP/G).
- **Rest-of-season value** (§7) counts each game at its factor too: 0.3 games' worth of points over a season for most players, about 4 for a rookie on opening night (below).

This changes the level of the first games, not how much weight the preseason projection gets. Giving the preseason projection less weight had already been tried, and it made rest-of-season projections less accurate.

Checked on the weekly projections `scripts/backtest_sims.py` makes for its synthetic leagues (preseason ML refit on earlier seasons, each week projected from earlier games only), against what each player who played scored that week. Per game, projected / actual, 16,330 player-weeks:

| Week | 2023‑24 before | after | 2024‑25 before | after | 2025‑26 before | after | All three before | after |
|---|---|---|---|---|---|---|---|---|
| 1 | 1.090 | **1.001** | 1.109 | **1.019** | 1.061 | **0.976** | 1.087 | **0.999** |
| 2 | 1.021 | 1.010 | 1.026 | 1.016 | 1.012 | 1.002 | 1.020 | 1.009 |
| 3 | 1.032 | 1.036 | 1.009 | 1.014 | 0.997 | 1.001 | 1.012 | 1.017 |
| 4 | 0.995 | 1.001 | 0.950 | 0.955 | 0.969 | 0.974 | 0.971 | 0.977 |
| 8 | 0.975 | 0.980 | 1.023 | 1.027 | 1.001 | 1.005 | 1.000 | 1.004 |

Over weeks 1–19 the average distance from 1 fell from 0.022 to 0.016 and per-game error from 7.08 to 7.05 FP (weeks 1–3: 7.45 to 7.31). The 150 most valuable players moved the same way (week 1: 1.083 to 0.995). In the recency backtest the dip lowered rest-of-season error in every season: 4.225 → 4.208 FP/G (2023‑24), 4.006 → 3.997 (2024‑25), 4.568 → 4.567 (2025‑26).

Tried and dropped:
- **The dip in the windows only**, with early games taken at face value in the update: the same week 1, but a slow start then pulled the level down for the rest of the season (rest-of-season error 4.228 / 4.004 / 4.569).
- **A longer, gentler dip** (10% fading over 3–8 games): fitted the game-by-game numbers worse and under-projected week 2 (0.975 with an 8-game fade).

**First games back from an absence (the return dip).** The offseason is one absence; every other absence does the same on a smaller scale. In the same weekly projections, a player's first game back mid-season, against his projection that week, after missing *L* straight team games:

| Team games missed | 1 | 2 | 3–4 | 5–6 | 7–9 | 10–14 | 15–19 | 20–29 |
|---|---|---|---|---|---|---|---|---|
| First game back vs projection | 0.96 | 0.91 | 0.89 | 0.89 | 0.85 | 0.83 | 0.83 | 0.75 |

Nearly all of it is minutes. After a long absence it also lasts longer: after 20–29 games his second and third games were 0.74 and 0.70. Players whose season starts late, mostly back from an injury, have both dips. In their first game they scored 0.67 of their projection, and over their first five games 0.86; the opening dip alone projected 0.88 and 0.94.

So in his *j*-th game back after missing *L* straight team games, a player is projected at

```
1 − 0.07 · ln(1 + L) · exp(−(j − 1) · 8 / L)
```

of his level, times the opening dip in his first games of the season. The dip fades over one game for every 8 he missed:

| Games missed \ game back | 1 | 2 | 3 | 5 | 8 |
|---|---|---|---|---|---|
| 1 | 0.95 | 1.00 | 1.00 | 1.00 | 1.00 |
| 3 | 0.90 | 0.99 | 1.00 | 1.00 | 1.00 |
| 10 | 0.83 | 0.92 | 0.97 | 0.99 | 1.00 |
| 25 | 0.77 | 0.83 | 0.88 | 0.94 | 0.98 |

Absences are counted in his team's games (the NBA schedule) between the games in his ESPN game log, which reaches back about 20 team games. The absence he is in now counts the games he has missed plus those the injury report says he'll miss, and the dip starts after them. It is used wherever the opening dip is: the windows, the simulator and the daily lineup scale his days by it, the update counts his first games back with their minutes scaled back up, and rest-of-season value counts them at their dip (§7).

Fitted on mid-season returns in the weekly projections (each first-to-tenth game back after each length of absence, weighted squared error 4,151 with no dip, 3,094 with it). Fitted on two seasons and scored on the third, 0.07 was the best depth every time, and it lowered the held-out error in all three. Against the opening dip alone, in the weekly projections (projected / actual per game; error per player-game):

| | Before | After |
|---|---|---|
| Late joiners, games 1–8 | 1.022 | **1.007** |
| Late joiners, error | 10.98 FP | **10.82** |
| Back mid-season, games 1–8 | 0.993 | **0.997** |
| Weeks 1–19, average distance from 1 | 0.018 | **0.016** |

In the recency backtest it lowered rest-of-season error in every season, and the bias moved toward 0: 4.208 → 4.198 FP/G (2023‑24), 3.997 → 3.984 (2024‑25), 4.567 → 4.565 (2025‑26).

**Rookies.** A player with no NBA minutes in the last two seasons (a rookie, mostly) has a deeper, longer dip of his own, on top of the one above:

```
minutes × (1 − 0.30·s·r),  output per minute × (1 − 0.20·r),  r = exp(−(n − 1) / 8)
```

| Game of the season | 1 | 2 | 3 | 5 | 8 | 10 | 15 | 20 | 30 |
|---|---|---|---|---|---|---|---|---|---|
| A rookie's expected FP vs his level, before his first game | 0.49 | 0.56 | 0.62 | 0.71 | 0.80 | 0.84 | 0.91 | 0.95 | 0.99 |

*s* is how much of the minutes dip is still to come. It is 1 before his first game. Once he has played, it goes by his minutes so far against his preseason projection: 1 if they fall short of it by 30% or more, 0 if he's already playing it, in between linearly. A rookie playing a full role from opening night has nothing left to climb in minutes. His output per minute still dips.

Rookies' preseason minutes aren't low. Over every rookie who played in 2023‑24 to 2025‑26 (287 of them), the preseason projection was 1.05 of the minutes they averaged over the season, and 1.09 of their FP/G. It looks low, 0.92, on the backtests' player pool, but that pool is the 350 most-rostered players at the end of the season, so it keeps the rookies who broke out. The 183 left out of it had been projected at 1.40 of their minutes.

Their first games are low, though, and they take weeks to climb, not days. Every rookie, against the projection with no games seen:

| Games of the season | 1 | 2–3 | 4–5 | 6–10 | 11–20 | 21–30 |
|---|---|---|---|---|---|---|
| Minutes | 0.52 | 0.66 | 0.73 | 0.82 | 0.89 | 0.98 |
| Fantasy points | 0.43 | 0.59 | 0.65 | 0.75 | 0.84 | 0.92 |

With only the general dip, the update read those games as the role he'd keep: after 5, 10 and 20 games, rookies' rest-of-season FP/G was projected at 0.92, 0.90 and 0.86 of what they then scored. The rookie dip works like the general one. The update counts every rookie's early minutes scaled back up by the full minutes dip (output per minute as it happened), and the weekly windows project his next games lower.

Checked on every rookie's games (ESPN game logs for the 1,000 most-rostered players, not just the season-end pool), from the projection the app would have made after *c* games of his:

| After *c* games | 0 | 2 | 4 | 8 | 12 |
|---|---|---|---|---|---|
| Next 4 games, projected / actual: all rookies, before | 1.65 | 1.36 | 1.24 | 1.09 | 1.05 |
| after | **1.03** | **1.07** | **1.08** | **1.05** | **1.06** |
| Rookies projected for 20+ minutes, before | 1.35 | 1.33 | 1.20 | 1.06 | 0.99 |
| after | 0.84 | **1.12** | **1.09** | **1.05** | **1.02** |

Per-game error over those games fell from 9.12 to 8.25 FP (rookies projected for 20+ minutes: 11.39 to 10.64). By how many minutes a rookie played in his first 5 games, projected / actual over his next 10, with the per-game error:

| Minutes in games 1–5 | under 15 | 15–22 | 22–28 | 28+ |
|---|---|---|---|---|
| Rookies | 149 | 39 | 19 | 9 |
| Before | 1.20 (5.17) | 1.08 (5.12) | 1.02 (5.97) | 0.90 (5.61) |
| After | **1.09 (4.83)** | 1.08 (5.17) | 1.08 (5.94) | **0.94 (5.25)** |
| The same dip for every rookie (*s* = 1) | 1.08 (4.84) | 1.01 (4.99) | 0.98 (5.88) | 0.85 (6.25) |

Rest-of-season FP/G error, averaged over 0 to 20 games seen, fell in every season: 5.24 → 5.00 (2023‑24), 4.31 → 4.01 (2024‑25), 4.62 → 4.39 (2025‑26), and for rookies projected for 20+ minutes 5.42 → 5.24, 4.54 → 4.37, 5.19 → 4.69. After 5, 10 and 20 games, rest-of-season projections now come to 0.97, 0.96 and 0.94 of what rookies scored.

In the app's rest-of-season backtest (`scripts/backtest_recency.py`, every player with 40+ games in the season-end pool), error fell in every season: 4.565 → 4.536 FP/G (2025‑26), 3.984 → 3.968 (2024‑25), 4.198 → 4.184 (2023‑24). In the weekly check above (`scripts/backtest_sims.py`'s projections, per game), rookies' per-game error fell from 7.18 to 7.06 FP (weeks 1–6: 7.99 to 7.62), and their average distance from 1 over weeks 1–19 from 0.069 to 0.058. Veterans' projections don't change, and all players' per-game error fell from 7.051 to 7.039. Week 1 is the exception. The pool's rookies, the ones still rostered at the end of the season, went from 1.30 to 0.78 of what they scored, so all players' week 1 moved from 0.997 to 0.973. Veterans alone stay at 0.984. Before a rookie's first game, nothing yet says which kind of rookie he is.

Tried and dropped:
- **The same dip for every rookie** (*s* = 1 throughout). Its next-four-game projections were the closest to 1 over all rookies (1.00–1.03 after 2 to 12 games), with the same rest-of-season error (4.48 vs 4.47 FP/G, averaged over the three seasons). But it projected rookies already playing big minutes well below what they went on to score (0.85 for 28+ minutes in games 1–5), and those are the rookies fantasy teams roster and start. On a live 2026‑27 league after 5 or 6 games, it cut the next week's projections for the top rostered rookies by 9–26%, to well under what they had been scoring.
- **Scaling the whole rookie dip by *s*, in the update too.** The near-term numbers held up, but rest-of-season error came back up to 4.56, because rookies whose early minutes look normal still climb later.
- **A deeper minutes dip (0.45).** It had the lowest rest-of-season error over all rookies when every rookie got it (4.47), and it was the pick in every season when each was scored with the setting chosen on the other two. But it projected rookies' first four games at 0.86 of what they scored, 0.70 for rookies projected for 20+ minutes, and dragged week‑1 projections for all players to 0.967 of what they scored, per game. 0.30 is the setting that does best on all rookies' and the 20+ minute rookies' rest-of-season error together.
- **Letting rookies' minutes move faster** (a minutes prior of 4 or 2 games instead of 8): the update followed the low early minutes sooner, and rest-of-season projections after 5 games fell further below what rookies scored (0.89 and 0.86).

**Second-year players.** A player with NBA minutes last season but none the season before (a second-year player, mostly) has a minutes dip of his own, on top of the general one, by how small his role is:

```
minutes × (1 − 0.40·s·r),  r = exp(−(n − 1) / 5)
```

*s* is 1 for a preseason projection of 15 minutes or less, 0 from 28 minutes, in between linearly. Output per minute has only the general dip.

| Game of the season | 1 | 2 | 3 | 5 | 8 | 10 | 15 | 20 |
|---|---|---|---|---|---|---|---|---|
| Minutes vs his level, projected for 15 or less | 0.56 | 0.64 | 0.71 | 0.81 | 0.90 | 0.93 | 0.98 | 0.99 |
| Projected for about 21.5 (*s* = ½) | 0.74 | 0.80 | 0.84 | 0.90 | 0.95 | 0.97 | 0.99 | 1.00 |

Measured on every second-year player's games in ESPN's game logs for the 1,000 most-rostered players, 2023‑24 to 2025‑26 (193 player-seasons), not just the season-end pool. Their preseason minutes aren't low: 0.98 of what they averaged over the season. Their rest-of-season projection before the first game was 0.98 of what they scored. But their first games fall short of their own level, the more the smaller the role. Each player's minutes against his minutes in the middle of his season (30–70% of his team's games):

| Games of the season | 1 | 2–3 | 4–5 | 6–8 | 9–12 |
|---|---|---|---|---|---|
| Veterans | 0.94 | 0.97 | 0.99 | 0.99 | 1.01 |
| Second-year players | 0.76 | 0.86 | 0.91 | 0.94 | 0.99 |
| projected for under 15 minutes | 0.45 | 0.71 | 0.79 | 0.93 | 0.89 |
| 15–22 | 0.74 | 0.84 | 0.91 | 0.89 | 1.04 |
| 22–28 | 0.95 | 0.92 | 0.93 | 0.98 | 0.98 |
| 28+ | 0.93 | 1.01 | 1.04 | 1.01 | 1.03 |

With only the general dip the update read those games as the role he'd keep. After 3, 5, 10 and 20 games, second-year players' rest-of-season FP/G was projected at 0.93, 0.92, 0.91 and 0.92 of what they then scored (veterans 1.00–1.02). The second-year dip works like the rookie one. The update counts his early minutes scaled back up by it, and the weekly windows project his next games lower. Unlike rookies, second-year players' preseason minutes already match their season, dip included. So the level the update starts from is his preseason minutes divided by his dip's average over the games he's projected to play. Before his first game, his rest-of-season projection stays what it was.

From the projection the app would have made after *c* of his games, against what he then scored:

| After *c* games | 0 | 3 | 5 | 10 | 20 |
|---|---|---|---|---|---|
| Rest of season, projected / actual: before | 0.98 | 0.93 | 0.92 | 0.91 | 0.92 |
| after | 0.97 | **0.98** | **0.97** | **0.96** | **0.96** |
| Next 10 games: before | 1.16 | 1.04 | 1.02 | 0.97 | 0.95 |
| after | **1.06** | 1.05 | 1.05 | **1.02** | **0.99** |

Averaged over those five points, second-year players' rest-of-season error fell from 4.28 to 4.16 FP/G and next-10-game error from 4.72 to 4.63, in every season (rest of season 3.73 → 3.69 in 2023‑24, 4.03 → 3.91 in 2024‑25, 4.89 → 4.71 in 2025‑26). A paired bootstrap over player-seasons puts the rest-of-season change at −0.13 FP/G (95% interval −0.23 to −0.02) and next-10 at −0.10 (−0.15 to −0.04). By preseason role (rest of season / next 10, error in brackets):

| Preseason minutes | under 15 | 15–22 | 22–28 | 28+ |
|---|---|---|---|---|
| Second-year players | 76 | 72 | 25 | 20 |
| Before | 0.80 (4.57) / 0.97 (4.52) | 0.96 (4.20) / 1.08 (4.79) | 0.96 (4.57) / 1.03 (4.68) | 0.98 (3.59) / 1.02 (5.29) |
| After | **0.86 (4.25)** / 0.97 **(4.42)** | **1.02 (4.14)** / 1.08 **(4.67)** | **0.98 (4.48)** / 1.03 **(4.64)** | unchanged |

Rookies and third-year players don't change. Veterans hardly do (3.829 → 3.832 FP/G): the few with a season missed two years ago get the dip too. All 1,573 player-seasons together: 4.052 → 4.040 (−0.013, 95% interval −0.025 to −0.001).

In the app's rest-of-season backtest (`scripts/backtest_recency.py`), error fell in every season: 4.536 → 4.514 FP/G (2025‑26), 3.968 → 3.962 (2024‑25), 4.184 → 4.174 (2023‑24). In the weekly check above (`scripts/backtest_sims.py`'s projections, per game), it is about even: second-year players' per-game error 7.022 → 7.032 FP (weeks 1–3: 7.700 → 7.662, 4–6: 7.483 → 7.430; the change is within noise, 95% interval −0.026 to +0.045), all players' 7.038 → 7.039, and for the 150 most valuable players 7.413 → 7.409. Week 1 is the cost, as with rookies: the pool's second-year players, the ones still rostered at the end of the season, went from 0.98 to 0.88 of what they scored in week 1, and all players' week 1 from 0.973 to 0.964. From week 4 on they're closer to 1 than before (week 6: 0.95 → 0.98, week 8: 0.95 → 0.98).

Tried and dropped:
- **The same dip for every second-year player.** Established second-year starters have no dip to climb out of: projected 28+ minutes, it put their next 10 games at 0.88 of what they scored after no games seen. Scaling by role keeps them where they were.
- **Leaving the starting level at the preseason projection.** The dip then lowered every small-role second-year player's rest-of-season projection before the first game, to 0.93 of what they scored.
- **Fading over 8 games instead of 5** (or 3), and a 0.30 depth. 8 games gained about as much on the wide sample (4.16 / 4.61), but missed more in the weekly windows (second-year per-game error 7.051, weeks 7 and 10 worse); 3 games and 0.30 gave up more of the gain (4.18–4.19 / 4.63–4.66).
- **A smaller dip for third-year players too** (0.20). It lowered all players' wide-sample error further (4.040 → 4.029), but the app keeps only two seasons of history, so a third-year player looks like a veteran to it. That needs his first season from the bundled history first.

### 1.6 Bounded adjustments (the "news" layer)

Only structured signals change numbers, and each has a cap:

| Signal | Effect | Bound |
|---|---|---|
| ESPN injury status / return date | Games missed only (rates untouched). Defaults: OUT 4 games, IR 12, suspension 3, day-to-day 0.5 | Never more than the team's games left |
| Return-time phrase in the official injury note ("3-to-4 months", "season-ending") | Games missed only, used if longer than the default | Same |
| New team since last season | Game-to-game uncertainty ×1.15; rates unchanged | — |
| League override by a member (logged to the feed) | Minutes, optional games out | ±15% minutes |
| **All minutes adjustments combined** | | **−20% / +15%** |

Free-text news headlines are shown for context only and are never parsed into numbers.

There is no team minutes budget. An earlier version scaled each NBA team's healthy players toward 236 minutes a game (±8%) so that injuries would hand out playing time. On 28,526 player-games in 2023‑24 to 2025‑26, projections were unbiased before that step (real/projected 0.99–1.01) and about 7% too high after it for the players it raised the most (real/projected 0.93 at the cap). Teammates' recent games already show who absorbs an absence, so the step counted it twice (`scripts/backtest_sims.py`).

## 2. Backtest

Rolling origin: the model for season *T* is trained only on seasons before *T*. The ensemble weights used to score *T* are fitted only on folds before *T*. Error is in fantasy points under ESPN default H2H points, the reference league's settings.

**Head-to-head with ESPN.** Players with 20+ games and a genuine ESPN preseason projection; test seasons 2021‑22, 2023‑24, 2024‑25 and 2025‑26.

| Method | FP/G MAE | FP/G RMSE | Season-total MAE | Rank ρ (season FP) |
|---|---|---|---|---|
| Last season repeated | 4.80 | 6.08 | 514 | 0.668 |
| SPS baseline | 4.56 | 5.83 | 469 | 0.704 |
| ESPN preseason | 4.57 | 5.79 | 507 | 0.736 |
| LightGBM alone | 4.41 | 5.60 | 456 | 0.729 |
| **Ensemble** | **4.33** | **5.49** | **446** | **0.737** |

**All players with 20+ games, all five test seasons** (ESPN not required):

| Method | FP/G MAE | FP/G RMSE | Season-total MAE | Rank ρ |
|---|---|---|---|---|
| Last season | 5.18 | 6.60 | 506 | 0.657 |
| SPS | 4.97 | 6.33 | 470 | 0.685 |
| LightGBM | **4.55** | **5.73** | 437 | 0.739 |
| **Ensemble** | 4.56 | **5.73** | **428** | **0.742** |

By season, the ensemble's FP/G MAE vs ESPN's: 4.30 vs 4.46 (2021‑22), 4.20 vs 4.48 (2023‑24), 4.10 vs 4.48 (2024‑25), 4.72 vs 4.84 (2025‑26). It beat ESPN in every season.

**Interpretation.** ML measurably helps on per-game accuracy and on season totals. It does not improve the *ranking* of players over ESPN alone; that difference is within noise. This is why ML is one input to a blend rather than the projection.

### Data audit

ESPN keeps a single "projection" per past season. For **2019‑20** and **2022‑23** it was evidently refreshed in-season:
- In 2022‑23, projected games average 37.5, a rest-of-season count.
- In both seasons, errors are about 40% below every other year.

Using them would leak in-season information into training and flatter ESPN, so the training script detects and drops them automatically. The rule: mean projected games < 65% of a season, or error < 75% of the median season. Earlier versions of this model that did not do this reported a misleadingly large ML advantage.

## 3. Team strength

1. **Daily lineups.** For every remaining day, the optimal legal lineup is solved exactly. A player's value doesn't depend on which slot he fills, so the startable sets form a transversal matroid. Greedy-by-value with Kuhn augmenting paths [7] is optimal, equivalent to the Hungarian method on the assignment problem but faster.
2. **Weights.** Each player's lineup weight is expected fantasy points × probability of playing that day: 0 during an injury window, otherwise his availability. A healthy player's next games are more likely than that: the injuries that would keep him out are mostly on the report already. His chance of missing his next game is 30% of his season miss rate, and it climbs back to the season rate over about 9 team games (`exp(−games ahead / 9)`). In 2023‑24 to 2025‑26 game logs, healthy rotation players missed 8% of their next two games and 24% over a season. Players back from an injury keep the season rate. At the start of a season, with the injury report fresh from training camp, healthy players miss even less: 0.76–0.85 as often as the ramp says in their team's 2nd to 6th games, and as often from about the 7th. So in his team's *n*-th game of the season (0 = the opener) a healthy player's chance of missing is also scaled by `1 − 0.6 · exp(−n / 2)`: 0.40 at the opener, 0.78 in game 2, 0.92 in game 4. In 2026‑27 all 90 rostered players ESPN listed as active before their team's opener played it. The ramp alone expected 6.9 of them to sit; with the season-start factor, 2.8. Today, a game in progress counts only the minutes he hasn't played yet, and a finished game counts nothing: what he scored is already in the matchup total. Each NBA team's own tip-off time tells which games are over; older data with only the day's first and last tip-off can tell only once every game is.
3. **Depth.** When a starter sits, the best playing bench player fills the expected vacancy, so depth has value.
4. **Roster limits and pickups.** A team holds its active slots plus one IR slot per injured player it can put there (OUT or IR, or already in its IR slot), and only while he's out. The day an IR player is back, a full team is a player over the limit, and its lowest-value player from then on is dropped. Without this, the returning player and everyone else played the rest of the season: a phantom extra player worth 35–50 points a week. A pickup plays from the day his waiver claim clears (ESPN's processing date, else the league's waiver period) and never in a game that has already tipped off. The player dropped for a waiver claim plays until it clears.
5. **Weekly distribution.** Mean = sum of expected starts × FP/G, lower in a player's first games of the season and his first games back from an absence (§1.5.2). Variance = sum of `q·σ² + q(1−q)·μ²` over starts (game volatility plus play/no-play), plus two swings that last a whole week: each player's level (10% of his expected points that week, squared) and the whole roster's (6% of the team's expected total, squared). The roster's swing grows in over the season's first weeks, as `6% × (1 − exp(−g / 8))` with *g* the league's games per team so far: 0 in week 1, about 2% in week 2, 4% in week 4 and nearly the full 6% by week 8. Early in a season rosters don't run hot or cold together (§4, calibration). Games aren't independent within a week: role, form and the projection's own error carry over from one game to the next. Points leagues only. A sum of ~35 player-games is close to Normal.
6. **Matchups.** `P(A beats B) = Φ((μA − μB) / √(σA² + σB²))`. The current week adds points already scored. Category leagues use Normal per-category totals (percentages via the delta method) and a Poisson-binomial count of categories won.
7. **Power score.** `played/season × record win% + (1 − played/season) × projected all-play win%`, where *played* is games played so far, *season* the regular-season length, ties count as half a win, and the projected all-play win rate is every team against every other team in every remaining regular-season week. It is each team's projected final win rate with an average opponent in every remaining week: week 1 is all projection, halfway is half each, and the last week is almost all record. The future part removes schedule luck; the record part is what actually happened, luck included.
8. **Season simulation.** 5,000 runs of the real remaining schedule, then seeding (win percentage, tiebreak by strength) and your bracket, including two-week rounds and byes. Seeding goes by win percentage because with an odd number of teams some play a game fewer: counting wins gave each extra game about 8 points of playoff odds. In points leagues each run also draws every team a level for the rest of the season (mean 1, SD 10%) that scales its projected points every week, for injuries, trades and form that last. Without it the weekly swings average out over a season and the odds come out far too sure. The outputs are projected record, playoff odds, title odds and average seed.
9. **Explanation.** Each rank is explained by:
   - fantasy points per week from each scoring stat vs the league average;
   - each player's expected contribution;
   - empty starting slots and bench-stuck games;
   - schedule volume;
   - points lost to injuries.

## 4. Matchup simulator

The season-long model uses the Normal approximation above. The **Matchups** page instead plays a single week out 10,000 times (`fbb/matchup_sim.py`).

1. **Each player-game.** The player plays with his availability probability (0 inside an injury window, or if you toggle him to sit). He then gets a role shock `S ~ Gamma(mean 1)` that scales every stat together (minutes, usage, foul trouble). In his first games of the season or back from an absence its mean is his opening and return dip instead of 1 (§1.5.2). Finally, independent Poisson counts are drawn for 2s made/missed, 3s made/missed, FTs made/missed, rebounds, assists, steals, blocks, turnovers, fouls, double- and triple-doubles, with means `rate × S`. Poisson thinning makes makes and misses independent given `S`, so shooting percentages emerge from simulated attempts.
2. **Calibration.** For fantasy points (linear in those counts), `Var(FP) = Var_Poisson + μ²·Var(S)` exactly. `Var(S)` is solved per player so the simulated spread equals his projected game-to-game SD. Checked: Jokić 65.1 ± 24.1 projected vs 65.1 ± 24.2 simulated. In points leagues each player's shocks are also multiplied by a level for the week (`Gamma`, mean 1, SD 10%) and the whole roster's by another (SD 6%, smaller in a season's first weeks), the week-level swings of §3.
3. **Lineups.** Each day's lineup is set from expected values, as a manager would set it. Because lineups lock at tip-off, a starter who doesn't play is replaced by the best *eligible* bench player who does.
4. **Scoring.** Points leagues sum fantasy points and add points already scored this week (ESPN's live total). Category leagues compare every category, with percentages from simulated makes/attempts and accrued category totals added where ESPN provides them. Ties count half.
5. **Outputs.** Win, loss and tie probabilities; score and margin distributions with 80% ranges; per-day expected points and player-games; each player's expected contribution, 80% range and *swing* = P(win | his week in the top quarter) − P(win | bottom quarter).
6. **What-ifs.** A fixed random seed is used per request, so sitting a player is a clean before/after comparison.

The simulator's team means land within ~1% of the analytic weekly projection. The small gap is because the simulator respects slot eligibility for fill-ins and the analytic shortcut doesn't. Win probabilities agree within ~1 point.

### Calibration against real seasons

`scripts/backtest_sims.py` asks the app's own code the questions it answers in the app and scores the answers against real games. It builds synthetic 10-team leagues on 2023‑24 to 2025‑26 box scores: ESPN default points scoring and roster, daily lineups, 19 matchup weeks and 4-team playoffs. Managers draft and make weekly pickups. Projections at each week's start see only earlier games. The study ran 192 league-seasons (18,240 weekly matchups) on AWS Lambda; settings that tie went to the simpler one.

| Weekly matchups | Before (0.5.3) | Now |
|---|---|---|
| Log loss of win odds (lower is better) | 0.578 | **0.561** (−0.017, 95% CI −0.021 to −0.012) |
| Brier score | 0.197 | **0.191** |
| Calibration slope (1 = calibrated, below 1 = too sure) | 0.90 | **0.97** |
| Scores inside the 80% range | 69% | **80%** |
| Real score vs projected | +6.4% | −2.1% |
| Player-games started per team-week (real: 39.2) | 34.3 | 39.9 |

| Playoff odds, all checkpoints | Before | Now |
|---|---|---|
| Brier score | 0.157 | **0.142** |
| Calibration slope | 0.49 | **0.93** |
| Title-odds Brier score | 0.094 | **0.085** |

Each change, tested alone and in combination:
- **Healthy players' next games (§3).** Without the ramp, teams started 5 fewer player-games a week than they really did. The ramp alone, with the team minutes budget still on, overshot: −9% bias.
- **Team minutes budget removed (§1.6).** Taken together with the ramp, the bias is −2%.
- **Week-level swings.** Without them the odds were too sure (slope 0.75) and 80% ranges held 70% of the time. Team swings of 4% were too small; 6% and 8% tied. Adding the 10% player swing made the calibration best. Scaling every game's role shock by 1.4 instead helped less.
- **Next-game miss rate.** 45% of the season rate instead of 30% was worse (log loss +0.002).
- **Season level for playoff odds.** 0.10 and 0.15 tied on Brier score; 0.10 is better calibrated (slope 0.93 vs 1.17). Odds before week 4 were still too sure (slope 0.5 at week 1; 0.64 on the local run below, 0.95 after the week-1 fixes there).
- **Opening dip (§1.5.2)**, on a separate local run of 96 league-seasons. Week-1 team scores had been projected 12.4% above real (9.6%, 15.3% and 12.1% by season), and 80% ranges held only 65% of week-1 scores. With the dip: 3.3% above (0.7%, 6.0%, 3.2%), and 80%. Points per player-game started in week 1 went from 1.081 to 0.993 of real. What was left was starts: simulated teams started 4% more player-games in week 1 than real teams did (next bullet). Over the season: log loss 0.5662 → 0.5658, Brier unchanged (0.1925), playoff-odds Brier 0.142 → 0.134 (slope 0.90 → 1.06), title Brier unchanged (0.086).
- **Week-1 starts, season start and return dip (§1.5.2, §3)**, on the same 96 league-seasons. The 4% was the study's, not the app's. Mid-season, the study stands in for the injury report with how long absences like each one lasted, but before week 1 nobody has missed a game, so it had no injury report at all. Players who would miss their team's opener were simulated as healthy: 2,740 week-1 games, where they played 263. Now a player who missed his team's opener stands in for one the report lists, out for as long as season-opening absences lasted in the other seasons (about 15 games on average). The app gets this from ESPN. That alone took week-1 starts to 0.966 of real. The rest was healthy players: they miss less at the start of a season than the ramp says (§3 step 2).

| 96 league-seasons | Before | Injury report in week 1 | + season start | + return dip (now) |
|---|---|---|---|---|
| Week-1 player-games started vs real | 1.040 | 0.966 | 0.996 | **0.996** |
| Week-1 team score, projected vs real | +3.3% | −4.7% | −1.6% | **−1.7%** |
| Week-1 Brier | 0.225 | 0.162 | 0.160 | **0.160** |
| Log loss, all weeks | 0.5658 | 0.5579 | 0.5576 | **0.5567** |
| Brier, all weeks | 0.1925 | 0.1892 | 0.1891 | **0.1881** |
| Playoff-odds Brier | 0.134 | 0.133 | 0.133 | 0.136 |
| Title-odds Brier | 0.086 | 0.086 | 0.086 | **0.084** |
| Week-1 playoff odds, calibration slope | 0.64 | 0.77 | 0.78 | **0.95** |

  The return dip changes projections, and so every draft and pickup, so its leagues aren't the same leagues: on 96 league-seasons its differences are within noise (log loss −0.0009, 95% interval −0.010 to +0.008; playoff Brier +0.002, −0.005 to +0.010). What it does to players is measured directly in §1.5.2.
- **The roster-wide swing in a season's first weeks (§3 step 5)**, on the same 96 league-seasons. With the week-1 bias gone, the early ranges were too wide: 80% ranges held 90% of real week-1 scores and 86% of week 2's, and win odds in weeks 1–3 were too timid (calibration slope 1.33). Week 1's errors were about what the players' own swings add up to, with nothing left for a roster-wide one. With the swing growing in over the league's first 8 games per team:

| | Week 1 | Week 2 | Week 3 | Weeks 4–6 | Weeks 7–19 |
|---|---|---|---|---|---|
| In the 80% range, full swing | 0.90 | 0.86 | 0.83 | 0.83 | 0.79 |
| In the 80% range, growing in | **0.83** | **0.79** | **0.78** | **0.80** | 0.79 |

  The win-odds calibration slope went from 1.33 to 1.15 in weeks 1–3 and from 1.03 to 0.97 in weeks 4–6, and stayed 0.89 after. Weekly log loss fell 0.5567 → 0.5558 (paired −0.0009, 95% interval −0.0016 to −0.0003), Brier 0.1881 → 0.1879, and week-1 Brier 0.160 → 0.158. Playoff and title odds didn't move. A 4-game ramp did less (−0.0005), and a 12-game one was worse than 8 (+0.0006, 95% +0.0001 to +0.0012), with weeks 4–19 too sure. Week 1 is still a little wide (83%): the projected game-to-game spread runs high early in a season (§8).
- **The rookie dip (§1.5.2) on top**, on the same 96 league-seasons. Like the return dip it changes projections, and so drafts and pickups. 80% ranges held 0.82 of real week-1 scores, 0.78 of week 2's and week 3's, and 0.79 after (0.83, 0.79, 0.78 and 0.79 without it). The win-odds calibration slope went from 1.14 to 1.06 in weeks 1–3, 0.97 to 0.91 in weeks 4–6 and 0.88 to 0.92 after. Weekly log loss 0.5558 → 0.5523 (−0.0035, 95% interval −0.013 to +0.007 by league-season), playoff Brier 0.1356 → 0.1378 (+0.002, −0.007 to +0.011): within noise.

The same check runs every morning on this season's finished weeks (`scripts/daily_sims.py`, docs/AWS.md §4).

## 5. Improvement suggestions

- **Add/drop.** The top 30 free agents (by rest-of-season value, healthy) are each simulated in place of each of your 4 lowest-value players. The player in your IR slot is never one of them: dropping him frees only the IR slot, which a healthy pickup can't take. Only lineup-adjusted gains are shown, as per week, this week, and rest of season.
- **Streaming.** For the current matchup's remaining days, the expected gain from each healthy free agent, with the rest-of-season cost of the drop shown. A player on waivers counts only the games after his claim clears.
- **Weak spots**, ranked by points at stake:
  - unfilled starting slots vs league average
  - bench overflow, which suggests consolidation trades
  - sub-replacement players
  - injuries (including IR housekeeping)
  - weakest categories in category leagues
- **Lineup confidence.** `P(starter outscores best eligible bench alternative)` from both players' means and standard deviations.

## 6. Trades

- **Evaluation.**
  - Both rosters are re-simulated over the rest of the season with the trade applied.
  - Roster size is enforced: a team over the limit drops its lowest-value player (never one it just received), and an open spot is filled by the best healthy free agent. IR slots hold OUT players, plus whoever is already in a team's IR slot: ESPN leaves a player there after he's upgraded to day-to-day, but the team that gets him in a trade can't put him on IR. An injured player a team gets goes in an empty IR slot instead of costing a drop, but never opens a spot for a pickup, and holds the slot only until he's back: then the team drops its lowest-value player from that day (step 4 of Team strength), and the calculation names him and the date. A team already over its limit isn't charged a drop for that by a trade.
  - Outputs per team: change in rest-of-season points, per week, this week and in playoff weeks, plus playoff and title odds before and after (3,000-run simulation).
  - Verdict: **advantageous** at ≥ +1% of rest-of-season output, **disadvantageous** at ≤ −1%, otherwise **neutral**.
  - Fairness label: **win-win** if every team gains, **lose-lose** if every team loses, **balanced** if every verdict is neutral, otherwise **favors** the team(s) that gain. Partner acceptance likelihood is a logistic curve in their % change.
- **Finder.**
  - Stage 1 scores every 1–2 player package pair against every opponent by raw rest-of-season value, with roster-spot replacement.
  - It keeps plausible trades: incoming value 0.7–1.6× outgoing, and neither side too far negative.
  - Stage 2 fully simulates the top ~90 for both teams.
  - Shown: trades the partner's projection doesn't lose more than 0.4% on, ranked by your gain, with at most 3 per partner and 3 per outgoing player.

## 7. Valuation measures

- **Points leagues.** Rest-of-season value = FP/G × expected games, with a player's first games of the season and first games back from an absence each counted at its opening and return dips (§1.5.2). VORP uses the replacement level: the median FP/G of the five best healthy free agents in *your* league.
- **Category leagues.** G-scores [8]: `(x − μ) / √(σ² + τ²/G)`, where σ is the spread of per-game means across rosterable players and τ² is the per-game variance (overdispersed Poisson; binomial for percentages). G ≈ 3.5 games per week. Percentages use volume-weighted impact: makes − attempts × league %.

## 8. Limitations

- Lineups assume an attentive manager who sets the optimal lineup daily.
- A player's projected game-to-game spread includes his level drifting from week to week, and early in a season his last season's closing games, so it overstates the spread within a week: in the weekly projections, real within-week variance was 0.68 of the projected one in week 1, 0.76 in weeks 2–3 and 0.87 after. His level, meanwhile, moves more than the 10% week swing: about 21% in week 1 and 15% later. The week-level swings were fitted to the team totals, which balance the two.
- The rookie dip (§1.5.2) is measured on three rookie classes, and only 9 rookies played 28+ minutes in their first 5 games. Before his first game every rookie gets the full dip, which projects rookies who go on to big roles low in week 1. Rookies who outplayed a small projection early (22–28 minutes) are still projected about 8% above their next games. The second-year dip is measured on 193 player-seasons, and it costs the season-end pool's second-year players in week 1 (0.88 of what they scored). Second-year players projected under 15 minutes are still projected at 0.86 of their rest of season, and young players' late-season climb (second- and third-year players' minutes ran 1.06–1.20 of their mid-season level in the last fifth of the season, veterans 1.02) isn't modeled. Neither are third-year players: the app keeps two seasons of history, so it can't tell them from veterans.
- The weekly distribution treats player games as independent. In reality there's some correlation, e.g. blowouts and pace.
- Category-league support is complete but less battle-tested than points.
- Accrued category totals for percentage categories are only added when ESPN reports the underlying makes and attempts.
- Simulated player-games are independent apart from each player's own role shock: no shared pace or blowout effects.
- Seeding tiebreaks approximate H2H record with team strength. Seeding by win percentage in leagues with byes is assumed, not confirmed against ESPN.
- When ESPN doesn't give a player's waiver processing date, his claim is assumed to clear after the league's waiver period (48 hours unless the league says otherwise).
- Trade acceptance is a model of the partner's *projected* interest, not of human psychology.

## References

1. Basketball-Reference, "Simple Projection System." https://www.basketball-reference.com/about/projections.html
2. T. Tango, "Marcel the Monkey Forecasting System." https://www.tangotiger.net/archives/stud0346.shtml
3. G. Ke et al. (2017). "LightGBM: A Highly Efficient Gradient Boosting Decision Tree." *NeurIPS*.
4. D. H. Wolpert (1992). "Stacked Generalization." *Neural Networks* 5(2).
5. J. M. Bates & C. W. J. Granger (1969). "The Combination of Forecasts." *Operational Research Quarterly* 20(4).
6. B. Efron & C. Morris (1975). "Data Analysis Using Stein's Estimator and Its Generalizations." *JASA* 70(350).
7. H. W. Kuhn (1955). "The Hungarian Method for the Assignment Problem." *Naval Research Logistics Quarterly* 2; J. Edmonds (1971). "Matroids and the greedy algorithm." *Mathematical Programming* 1.
8. Z. Rosenof (2023). "Static quantification of player value for fantasy basketball." arXiv:2307.02188 (and the follow-up on dynamic valuation, arXiv:2409.09884).
9. S. Lundberg et al. (2020). "From local explanations to global understanding with explainable AI for trees." *Nature Machine Intelligence* 2 (TreeSHAP, used for per-player explanations).
10. J. Kubatko, D. Oliver, K. Pelton & D. Rosenbaum (2007). "A Starting Point for Analyzing Basketball Statistics." *JQAS* 3(3).
