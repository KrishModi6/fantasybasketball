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
- From a player's **15th game** this season, last season stops counting: `K` is 0 and last season's games drop out, so rates, minutes per game and game-to-game volatility come from this season's games alone (still recency-weighted). The preseason projection is built from last season's stats, so it drops out too. Availability (share of games played) still comes from the preseason projection. This is a product choice, not the most accurate setting: in the recency backtest below it raised the error at 15 games from 4.12 to 4.84 FP/G (2025‑26) and from 3.61 to 4.36 (2024‑25), and at 35 games by 3-4%.
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

Steals need about 3× more minutes than rebounds before this season's sample dominates. Minutes per game update faster: 8 games of prior weight with a 10-game half-life, because roles change.

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

"His" rate means his last two seasons (the older one at half weight), shrunk toward the league rate by a pseudo-count of opportunities (400 shots, 300 threes, 150 free throws, 200 potential assists, 300 rebound chances, 120 deflections, 300 contests), so a rookie converts at the league rate. On each season's unseen shots, the shot model beats league FG% by zone (log loss 0.636 vs 0.658).

During the season, before the update in 1.5, this season's makes, assists, rebounds, steals and blocks move part of the way from what happened toward expected. Attempts, minutes and turnovers stay as they happened. The weights are fitted by `scripts/backtest_xfp.py`: FG 0.70, 3P 1.00, FT 0.90, AST 0.50, REB 0.95, STL 0.75, BLK 0.25. A weight of 1 means only the opportunities count. Threes and rebounds are almost all opportunity; blocks are mostly skill.

The backtest replays 2023‑24, 2024‑25 and 2025‑26 at six points (7 to 91 days after opening night). Every player with 40+ games is projected the way the app would have done it at that date, and the projection is scored on his rest-of-season FP/G. Each season's weights are fitted on the other two seasons, and its shot model is trained on earlier seasons only:

| Season | Without xFP | With xFP | Change | At day 42 |
|---|---|---|---|---|
| 2023‑24 | 4.25 FP/G | 4.15 | −2.5% | −4.5% |
| 2024‑25 | 3.92 | 3.79 | −3.3% | −9.5% |
| 2025‑26 | 4.41 | 4.27 | −3.2% | −5.0% |

The gain is largest after a player's 15th game, when the preseason prior drops out and this season's sample stands alone. Across 5,033 player-dates, each FP/G a player ran above his xFP so far was followed by 0.83 FP/G less over the rest of the season. Of the players 3+ FP/G above xFP, 69% scored less the rest of the way (40% of all players did). Of those 3+ below, 79% scored more (60% of all players). The player list shows xFP/G and luck (FP/G minus xFP/G), and the AI assistant gets both. `scripts/xfp_report.py` prints this season's luck board.

stats.nba.com does not answer cloud hosts (AWS, Azure, GitHub Actions), so tracking is fetched during a league sync on the desktop app or a home server. Each sync downloads only days not already cached. Where NBA.com can't be reached, projections are what they were without xFP.

### 1.6 Bounded adjustments (the "news" layer)

Only structured signals change numbers, and each has a cap:

| Signal | Effect | Bound |
|---|---|---|
| ESPN injury status / return date | Games missed only (rates untouched). Defaults: OUT 4 games, IR 12, suspension 3, day-to-day 0.5 | Never more than the team's games left |
| Return-time phrase in the official injury note ("3-to-4 months", "season-ending") | Games missed only, used if longer than the default | Same |
| Team minutes budget (healthy players on each NBA team scaled toward 236 min/game) | Minutes, moved halfway toward the budget. This is how injuries and trades reallocate opportunity | ±8% |
| New team since last season | Game-to-game uncertainty ×1.15; rates unchanged | — |
| League override by a member (logged to the feed) | Minutes, optional games out | ±15% minutes |
| **All minutes adjustments combined** | | **−20% / +15%** |

Free-text news headlines are shown for context only and are never parsed into numbers.

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
2. **Weights.** Each player's lineup weight is expected fantasy points × probability of playing that day (availability, or 0 during an injury window).
3. **Depth.** When a starter sits, the best playing bench player fills the expected vacancy, so depth has value.
4. **Weekly distribution.** Mean = sum of expected starts × FP/G. Variance = sum of `q·σ² + q(1−q)·μ²` over starts (game volatility plus play/no-play). A sum of ~35 player-games is close to Normal.
5. **Matchups.** `P(A beats B) = Φ((μA − μB) / √(σA² + σB²))`. The current week adds points already scored. Category leagues use Normal per-category totals (percentages via the delta method) and a Poisson-binomial count of categories won.
6. **Power score.** `played/season × record win% + (1 − played/season) × projected all-play win%`, where *played* is games played so far, *season* the regular-season length, ties count as half a win, and the projected all-play win rate is every team against every other team in every remaining regular-season week. It is each team's projected final win rate with an average opponent in every remaining week: week 1 is all projection, halfway is half each, and the last week is almost all record. The future part removes schedule luck; the record part is what actually happened, luck included.
7. **Season simulation.** 5,000 runs of the real remaining schedule, then seeding (wins, tiebreak by strength) and your bracket, including two-week rounds and byes. The outputs are projected record, playoff odds, title odds and average seed.
8. **Explanation.** Each rank is explained by:
   - fantasy points per week from each scoring stat vs the league average;
   - each player's expected contribution;
   - empty starting slots and bench-stuck games;
   - schedule volume;
   - points lost to injuries.

## 4. Matchup simulator

The season-long model uses the Normal approximation above. The **Matchups** page instead plays a single week out 10,000 times (`fbb/matchup_sim.py`).

1. **Each player-game.** The player plays with his availability probability (0 inside an injury window, or if you toggle him to sit). He then gets a role shock `S ~ Gamma(mean 1)` that scales every stat together (minutes, usage, foul trouble). Finally, independent Poisson counts are drawn for 2s made/missed, 3s made/missed, FTs made/missed, rebounds, assists, steals, blocks, turnovers, fouls, double- and triple-doubles, with means `rate × S`. Poisson thinning makes makes and misses independent given `S`, so shooting percentages emerge from simulated attempts.
2. **Calibration.** For fantasy points (linear in those counts), `Var(FP) = Var_Poisson + μ²·Var(S)` exactly. `Var(S)` is solved per player so the simulated spread equals his projected game-to-game SD. Checked: Jokić 65.1 ± 24.1 projected vs 65.1 ± 24.2 simulated.
3. **Lineups.** Each day's lineup is set from expected values, as a manager would set it. Because lineups lock at tip-off, a starter who doesn't play is replaced by the best *eligible* bench player who does.
4. **Scoring.** Points leagues sum fantasy points and add points already scored this week (ESPN's live total). Category leagues compare every category, with percentages from simulated makes/attempts and accrued category totals added where ESPN provides them. Ties count half.
5. **Outputs.** Win, loss and tie probabilities; score and margin distributions with 80% ranges; per-day expected points and player-games; each player's expected contribution, 80% range and *swing* = P(win | his week in the top quarter) − P(win | bottom quarter).
6. **What-ifs.** A fixed random seed is used per request, so sitting a player is a clean before/after comparison.

The simulator's team means land within ~1% of the analytic weekly projection. The small gap is because the simulator respects slot eligibility for fill-ins and the analytic shortcut doesn't. Win probabilities agree within ~1 point.

## 5. Improvement suggestions

- **Add/drop.** The top 30 free agents (by rest-of-season value, healthy) are each simulated in place of each of your 4 lowest-value players. Only lineup-adjusted gains are shown, as per week, this week, and rest of season.
- **Streaming.** For the current matchup's remaining days, the expected gain from each healthy free agent, with the rest-of-season cost of the drop shown.
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
  - Roster size is enforced: a team over the limit drops its lowest-value player (never one it just received), and an open spot is filled by the best healthy free agent. IR slots hold OUT players, plus whoever is already in a team's IR slot: ESPN leaves a player there after he's upgraded to day-to-day, but the team that gets him in a trade can't put him on IR. An injured player a team gets goes in an empty IR slot instead of costing a drop, but never opens a spot for a pickup. A team already over its limit isn't charged a drop for that by a trade.
  - Outputs per team: change in rest-of-season points, per week, this week and in playoff weeks, plus playoff and title odds before and after (3,000-run simulation).
  - Verdict: **advantageous** at ≥ +1% of rest-of-season output, **disadvantageous** at ≤ −1%, otherwise **neutral**.
  - Fairness label: **win-win** if every team gains, **lose-lose** if every team loses, **balanced** if every verdict is neutral, otherwise **favors** the team(s) that gain. Partner acceptance likelihood is a logistic curve in their % change.
- **Finder.**
  - Stage 1 scores every 1–2 player package pair against every opponent by raw rest-of-season value, with roster-spot replacement.
  - It keeps plausible trades: incoming value 0.7–1.6× outgoing, and neither side too far negative.
  - Stage 2 fully simulates the top ~90 for both teams.
  - Shown: trades the partner's projection doesn't lose more than 0.4% on, ranked by your gain, with at most 3 per partner and 3 per outgoing player.

## 7. Valuation measures

- **Points leagues.** Rest-of-season value = FP/G × expected games. VORP uses the replacement level: the median FP/G of the five best healthy free agents in *your* league.
- **Category leagues.** G-scores [8]: `(x − μ) / √(σ² + τ²/G)`, where σ is the spread of per-game means across rosterable players and τ² is the per-game variance (overdispersed Poisson; binomial for percentages). G ≈ 3.5 games per week. Percentages use volume-weighted impact: makes − attempts × league %.

## 8. Limitations

- Lineups assume an attentive manager who sets the optimal lineup daily.
- The weekly distribution treats player games as independent. In reality there's some correlation, e.g. blowouts and pace.
- Category-league support is complete but less battle-tested than points.
- Accrued category totals for percentage categories are only added when ESPN reports the underlying makes and attempts.
- Simulated player-games are independent apart from each player's own role shock: no shared pace or blowout effects.
- Seeding tiebreaks approximate H2H record with team strength.
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
