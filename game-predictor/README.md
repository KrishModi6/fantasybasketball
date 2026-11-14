# NBA game predictor

Predicts NBA games: each team's chance to win, a predicted score, the factors behind the prediction, 10,000
simulated outcomes, and how the odds change when you change who plays.

It is a separate program from Courtside (its own folder, Python environment and tests) and shares no code with
the fantasy engine.

```
CLE at NY, 2026-11-11
  NY 64%   CLE 36%
  Predicted score: NY 117, CLE 112   (8 games in 10: NY 102-132, CLE 97-127)
  10,000 simulated games: NY by 10+ 37%, CLE by 10+ 14%, within 5 points 29%, total 204-254
  What moves NY's chance (percentage points):
    +10.5  Season efficiency
     +6.9  Home court
     -3.7  Injuries and availability
     +3.0  Team strength (Elo)
     -2.9  Rest and schedule
     +0.4  Recent form
  NY out: Josh Hart (injury report)
  CLE out: Jaylon Tyson (injury report), Sam Merrill (injury report)
```

## Set up

```bash
cd game-predictor
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m gamepred fetch                      # about a minute, once: every box score since 2014-15
```

A trained model ships in `models/`, so `predict` works straight after `fetch`.

## Use

```bash
python -m gamepred predict                                   # today's games
python -m gamepred predict --date 2026-11-14                 # any day on the schedule
python -m gamepred predict --home BOS --away LAL             # any two teams
python -m gamepred predict --home CHI --away SAS --out "Victor Wembanyama"      # what if he sits?
python -m gamepred predict --home BOS --away LAL --back "Jayson Tatum"          # what if he's back?
python -m gamepred lineup SAS                                # a team's rotation and each player's value
python -m gamepred train                                     # backtest again and retrain on everything
python -m gamepred publish predictions.json                  # today's and tomorrow's predictions as one file
```

- `--out` and `--back` take full names, comma-separated. A surname alone works when only one player on the two
  teams has it. With either, the output shows how many points the lineup change moved the odds.
- ESPN's injury report is applied by default: players listed **Out** sit, day-to-day players are counted as
  playing and named. `--no-injuries` ignores it.
- `--json` prints everything: both models' probabilities, the full simulation, the feature values.
- `--neutral` for a neutral court; `--sims` for more or fewer simulated games; `--refresh` to fetch the latest
  results first (otherwise this season's file is fetched again once it is three hours old).
- Team abbreviations are ESPN's (`NY`, `SA`, `GS`, `NO`, `UTAH`, `WSH`); the NBA's (`NYK`, `SAS`, `GSW`, ...) work too.

## In Courtside

Courtside's Overview page has an **NBA games** panel with these predictions. The desktop app can't run this
program (it ships without pandas, scikit-learn and XGBoost), so `.github/workflows/game-predictions.yml` runs
`publish` four times a day and puts the file on the `game-predictions` branch, where every copy of the app reads it.

`publish` predicts a game again on every run until it tips off. After that the game keeps the prediction from the
file written last time (the output file itself, or `--previous other.json`) and only its score changes, so a
finished game is never shown with a prediction made after it. What-ifs (`--out`, `--back`) are only here, on the
command line.

## How it works

**Data** (`gamepred/data.py`). Every team's and player's box score for each regular-season game since 2014-15,
from the [sportsdataverse](https://github.com/sportsdataverse/sportsdataverse-data) season files (the data behind
hoopR and sportsdataverse-py; one small file per season). The newest few days' games, the day's schedule and the
injury report come from ESPN. NBA.com is not used: it refuses many connections.

**Features** (`gamepred/features.py`). Games are walked through in date order. Each game's row is built from the
two teams as they stood *before* the game, and only then is the game added, so nothing can see its own result
or the future (a test changes the future and checks that earlier rows don't move).

| Factor | What goes in |
|---|---|
| Team strength | Elo rating: 20 points a game, more for bigger wins, home court worth 65, carried 75% between seasons |
| Season efficiency | Points scored and allowed per 100 possessions, pace, win rate, and the four factors (shooting, turnovers, offensive rebounds, free throws) on both ends. Early in a season these start from last season's ratings and give way as games come in |
| Recent form | Net rating and wins over the last 10 games |
| Rest and schedule | Days of rest, back-to-backs, games in the last 7 days |
| Injuries and availability | Each player's value is his recent Game Score per game. A team's *available* value is the sum for its nine best players in uniform; its *missing* value is the sum for rotation players who aren't |
| Home court | The models' starting point, about 7 points of win probability |

"In uniform" for a past game means everyone who played or sat by the coach's decision: who was healthy and
dressed, which is known before tip-off. It deliberately isn't "who got minutes", which depends on how the game
went.

**Models** (`gamepred/model.py`). The win probability is the average of a logistic regression and XGBoost. Two
more XGBoost models predict the margin and the total points.

**Simulation** (`gamepred/simulate.py`). A real game lands about 14 points from its predicted margin and 19 from
its predicted total (measured in the backtest). Each simulated game draws a margin and a total from that spread
and turns them into a final score; ties go to overtime. The margin's centre is set so the share of simulated
games the home team wins equals the model's win probability, so the two never disagree.

**Factors.** Both models add up evidence on the log-odds scale (the regression's terms; XGBoost's TreeSHAP
contributions). A factor's number is the win probability with it, minus the probability with its evidence taken
out of both models. They are a reading of the models, not exact pieces that sum to the probability.

## How good is it

Rolling-origin backtest: each season is predicted by models trained only on earlier seasons. 7,393 games,
2020-21 to the first three weeks of 2026-27.

| Method | Accuracy | Log loss | Brier |
|---|---|---|---|
| Home team always | 55.1% | 0.6888 | 0.2478 |
| Elo ratings alone | 65.2% | 0.6246 | 0.2175 |
| Random forest | 66.3% | 0.6099 | 0.2110 |
| XGBoost | 66.8% | 0.6094 | 0.2107 |
| Margin model read as odds | 66.9% | 0.6075 | 0.2100 |
| Logistic regression | 66.9% | **0.6041** | **0.2086** |
| **Logistic + XGBoost (used)** | **67.0%** | 0.6053 | 0.2091 |

- The blend beat Elo on log loss in every finished season. On accuracy it beat Elo in five of six (2025-26:
  68.8% against Elo's 69.0%).
- The logistic regression alone is as good as the blend (slightly better on log loss, slightly worse on
  accuracy). XGBoost adds little here; the blend is kept because the two models disagree usefully on single games.
- Player availability is worth about 2 points of accuracy: without those features the blend scores 65.1% and
  0.6228 on the six finished seasons.
- The odds mean what they say. When the model gave the home team 70-80%, it won 76%; at 30-40%, 37%; above 80%, 84%.
- Scores: the predicted margin is off by 10.7 points on average (12.4 for always guessing the usual home edge),
  the total by 15.1 (16.0 for last season's average).

For scale: nobody predicts NBA games much above two in three before tip-off. This model has not been compared
with betting lines, which are usually the best public forecast, and it isn't meant for betting.

## Limits

- A what-if moves only the availability features. The team's Elo and efficiency ratings were earned with the
  player, so sitting a star moves the odds less than it should (Wembanyama out moved Chicago's chance against San
  Antonio from 19% to 25%).
- Player value is a box-score number. It misses defence and anything else the box score misses.
- A rotation is whoever played in the team's last 10 games, so a player back from a long absence isn't in the
  lineup until he plays or you add him with `--back`. A traded player counts for his new team from his first game.
- Trained on regular-season games only; playoff games are not predicted well by regular-season habits.
- 2019-20's restart is treated as neutral-court; 2020-21's empty arenas are not marked.

## Tests

```bash
.venv/Scripts/python -m pytest tests -q
```

They run offline on a made-up league (`tests/conftest.py`), plus one check of the shipped model's backtest.
