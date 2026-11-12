"""A small made-up league, so every test runs offline in a few seconds.

Six teams of eight players play each other home and away twice a season. A team's strength is fixed, its
first player is a star worth about ten points a game, and each star misses about one game in five (listed
with an injury, so he is not in uniform). The final margin is the strength gap, home court, the stars who are
out, and noise: the things the real model is meant to pick up.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gamepred import data, features, model  # noqa: E402

TEAMS = {1: "AAA", 2: "BBB", 3: "CCC", 4: "DDD", 5: "EEE", 6: "FFF"}
STRENGTH = {1: 8.0, 2: 5.0, 3: 2.0, 4: -1.0, 5: -5.0, 6: -9.0}
STAR_WORTH = 10.0
HOME_EDGE = 3.0
SEASONS = range(2016, 2022)


def _team_row(season, gid, day, tid, home, pts, opp_pts, rng) -> dict:
    fga = 86 + rng.integers(-4, 5)
    fg3m = 11 + rng.integers(-3, 4)
    ftm = 16 + rng.integers(-4, 5)
    fgm = (pts - ftm - fg3m) / 2  # points = 2 x field goals + threes + free throws
    return {"SEASON": season, "GAME_ID": gid, "GAME_DATE": day.isoformat(), "TEAM_ID": tid, "TEAM": TEAMS[tid],
            "HOME": home, "WIN": pts > opp_pts, "FGM": fgm, "FGA": fga, "FG3M": fg3m, "FG3A": 32, "FTM": ftm,
            "FTA": ftm + 5, "OREB": 10 + rng.integers(-2, 3), "DREB": 33, "REB": 43, "AST": 25, "STL": 7, "BLK": 5,
            "TOV": 13 + rng.integers(-3, 4), "PF": 20, "PTS": pts}


def _player_rows(season, gid, day, tid, star_out, rng) -> list[dict]:
    rows = []
    for k in range(8):
        pid = tid * 100 + k
        base = {"SEASON": season, "GAME_ID": gid, "GAME_DATE": day.isoformat(), "TEAM_ID": tid, "TEAM": TEAMS[tid],
                "PLAYER_ID": pid, "PLAYER": f"{TEAMS[tid]} Player {k}"}
        zero = {c: 0.0 for c in data.BOX}
        if k == 0 and star_out:
            rows.append({**base, "MIN": 0.0, **zero, "DRESSED": False})  # listed with an injury
        elif k == 7 and rng.random() < 0.5:
            rows.append({**base, "MIN": 0.0, **zero, "DRESSED": True})  # healthy, not used: coach's decision
        else:
            pts = (27 if k == 0 else 15 - k) + rng.integers(-3, 4)
            rows.append({**base, "MIN": 34.0 if k == 0 else 26.0 - 2 * k, **zero, "FGM": pts / 2.5, "FGA": pts / 1.2,
                         "FTM": 3.0, "FTA": 4.0, "DREB": 4.0, "REB": 5.0, "AST": 6.0 if k == 0 else 2.0, "STL": 1.0,
                         "TOV": 2.0, "PF": 2.0, "PTS": float(pts), "DRESSED": True})
    return rows


def make_logs(seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    team_rows, player_rows = [], []
    gid = 0
    for season in SEASONS:
        day = date(season, 10, 20)
        pairs = [(h, a) for h in TEAMS for a in TEAMS if h != a] * 2
        order = rng.permutation(len(pairs))
        busy: dict[int, date] = {}
        for i in order:
            h, a = pairs[i]
            while busy.get(h) == day or busy.get(a) == day:  # one game a day for a team
                day += timedelta(days=1)
            busy[h] = busy[a] = day
            out_h, out_a = rng.random() < 0.2, rng.random() < 0.2
            margin = (STRENGTH[h] - STRENGTH[a] + HOME_EDGE - STAR_WORTH * out_h + STAR_WORTH * out_a + rng.normal(0, 9))
            margin = int(round(margin)) or 1
            total = int(round(222 + rng.normal(0, 14)))
            hp, ap = (total + margin + 1) // 2, (total - margin) // 2
            if hp == ap:
                hp += 1
            gid += 1
            g = f"{gid:010d}"
            team_rows += [_team_row(season, g, day, h, True, hp, ap, rng), _team_row(season, g, day, a, False, ap, hp, rng)]
            player_rows += _player_rows(season, g, day, h, out_h, rng) + _player_rows(season, g, day, a, out_a, rng)
            if rng.random() < 0.6:
                day += timedelta(days=1)
    return pd.DataFrame(team_rows)[data.TEAM_COLUMNS], pd.DataFrame(player_rows)[data.PLAYER_COLUMNS]


@pytest.fixture(scope="session")
def logs():
    return make_logs()


@pytest.fixture(scope="session")
def built(logs):
    return features.build(*logs)


@pytest.fixture(scope="session")
def bundle(built):
    games, _ = built
    bt = model.backtest(games, [2021])
    bt.pop("oof")
    return {"models": model.fit(games), "features": features.FEATURES, "sigma_margin": bt["sigma_margin"],
            "sigma_total": bt["sigma_total"], "rho": bt["rho"], "metrics": {"backtest": bt, "trained_through": "2022-03-01"}}
