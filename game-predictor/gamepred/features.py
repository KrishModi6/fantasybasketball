"""What is known about a game before it tips off, as one row of numbers.

Games are walked through in date order. Each game's row is built from the two teams' state as it stood
before the game, and only then is the state updated with the game's result, so no row can see its own game
or anything after it (tests/test_features.py checks this by changing the future).

A team's state:

  strength      an Elo rating: 20 points a game at stake, more for bigger wins, home court worth HOME_ELO,
                carried over between seasons three-quarters of the way
  efficiency    points scored and allowed per 100 possessions this season, pace, and the four factors
                (shooting, turnovers, offensive rebounds, free throws) on both ends. Early in a season these
                start from last season's ratings, pulled 40% toward the league's, and give way as games come in
  form          net rating and wins over the last 10 games
  schedule      days of rest, back-to-backs, games in the last 7 days
  availability  each player has a value: his recent Game Score per game (John Hollinger's box-score summary),
                recent games counting most. A team's available value is the sum of its nine best players in
                uniform; its missing value is the sum for players in its recent rotation who are not

For a game that has been played, "in uniform" is everyone who played or sat by the coach's decision: who was
healthy and dressed, which is known before tip-off, not who got minutes. For one that hasn't, it is the team's recent
rotation less anyone listed as out, and a what-if can take players out or put them back (predict.py).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

HOME_ELO = 65.0  # home court, in Elo points (about 59% for two even teams)
ELO_K = 20.0
ELO_MEAN = 1505.0
ELO_CARRY = 0.75  # share of a team's rating above or below the mean kept into a new season
PRIOR_POSS = 1000.0  # last season's ratings count as this many possessions (about 10 games)
PRIOR_KEEP = 0.6  # share of last season's gap from the league average carried into the prior
FACTOR_PRIOR_GAMES = 5.0
LEAGUE = {"off": 110.0, "pace": 98.0, "efg": 0.535, "tov": 0.125, "orb": 0.245, "ftr": 0.255}  # before any game
RECENT_GAMES = 10
VALUE_HALF_LIFE = 25.0  # games for a player's old games to count half
VALUE_SHRINK = 4.0  # games of zero value mixed in, so a hot first week isn't a star
ROTATION_WINDOW = 10  # a player is in the rotation if he played in one of his team's last 10 games
ROTATION_SIZE = 9  # players whose value counts toward a team's available value
EARLY_GAMES = 5  # until a team has played this many, last season's rotation still counts
BUBBLE = (2019, date(2020, 7, 1))  # 2019-20 resumed on a neutral court

FEATURES = [
    "elo_diff", "neutral",
    "net_diff", "off_home", "def_home", "off_away", "def_away", "pace_home", "pace_away", "winpct_diff",
    "efg_off_diff", "tov_off_diff", "orb_off_diff", "ftr_off_diff", "efg_def_diff", "tov_def_diff", "orb_def_diff",
    "ftr_def_diff", "games_home", "games_away", "lg_off", "lg_pace",
    "l10_net_diff", "l10_win_diff",
    "rest_home", "rest_away", "b2b_home", "b2b_away", "g7_home", "g7_away",
    "avail_home", "avail_away", "avail_diff", "missing_home", "missing_away",
]
# What each feature is evidence of, for the factors shown with a prediction.
GROUPS = {
    "Team strength (Elo)": ["elo_diff"],
    "Season efficiency": ["net_diff", "off_home", "def_home", "off_away", "def_away", "pace_home", "pace_away",
                          "winpct_diff", "efg_off_diff", "tov_off_diff", "orb_off_diff", "ftr_off_diff", "efg_def_diff",
                          "tov_def_diff", "orb_def_diff", "ftr_def_diff", "games_home", "games_away", "lg_off", "lg_pace"],
    "Recent form": ["l10_net_diff", "l10_win_diff"],
    "Rest and schedule": ["rest_home", "rest_away", "b2b_home", "b2b_away", "g7_home", "g7_away"],
    "Injuries and availability": ["avail_home", "avail_away", "avail_diff", "missing_home", "missing_away"],
    "Neutral court": ["neutral"],
}
TARGETS = ["home_win", "margin", "total"]
# The data uses ESPN's team abbreviations; these are the NBA's and Basketball-Reference's for the same teams.
ALIASES = {"NYK": "NY", "SAS": "SA", "GSW": "GS", "NOP": "NO", "UTA": "UTAH", "WAS": "WSH", "PHO": "PHX", "BRK": "BKN",
           "CHO": "CHA"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_SUMS = ("fgm", "fg3m", "fga", "fta", "tov", "oreb", "dreb")


def name_key(name: str) -> str:
    """Accents, punctuation and suffixes dropped, so "Luka Dončić" and "Luka Doncic Jr." compare equal."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s.replace("-", " "))
    return " ".join(p for p in s.split() if p not in _SUFFIXES)


def game_score(r) -> float:
    """Hollinger's Game Score: one number for a box-score line."""
    return (r["PTS"] + 0.4 * r["FGM"] - 0.7 * r["FGA"] - 0.4 * (r["FTA"] - r["FTM"]) + 0.7 * r["OREB"] + 0.3 * r["DREB"]
            + r["STL"] + 0.7 * r["AST"] + 0.7 * r["BLK"] - 0.4 * r["PF"] - r["TOV"])


def possessions(box: dict, opp: dict) -> float:
    """Possessions in a game, the average of both teams' estimates."""
    est = lambda b: b["FGA"] + 0.44 * b["FTA"] - b["OREB"] + b["TOV"]  # noqa: E731
    return 0.5 * (est(box) + est(opp))


@dataclass
class Team:
    id: int
    abbrev: str = ""
    elo: float = 1500.0
    season: int = -1
    games: int = 0  # this season
    wins: int = 0
    pts: float = 0.0
    opp_pts: float = 0.0
    poss: float = 0.0
    own: dict = field(default_factory=lambda: dict.fromkeys(_SUMS, 0.0))
    opp: dict = field(default_factory=lambda: dict.fromkeys(_SUMS, 0.0))
    prior: dict | None = None  # last season's off, def and pace, pulled toward the league's
    recent: deque = field(default_factory=lambda: deque(maxlen=RECENT_GAMES))  # (net rating, won)
    days: deque = field(default_factory=lambda: deque(maxlen=8))  # ordinals of its latest games
    played: int = 0  # games ever, the clock a rotation is measured on
    roster: dict = field(default_factory=dict)  # player -> ``played`` when he last appeared
    this_season: set = field(default_factory=set)  # players who have appeared this season


@dataclass
class PlayerValue:
    num: float = 0.0
    den: float = 0.0
    team: int | None = None
    name: str = ""

    @property
    def value(self) -> float:
        return self.num / (self.den + VALUE_SHRINK)


class FeatureState:
    """Every team and player as of the last game fed in."""

    def __init__(self) -> None:
        self.teams: dict[int, Team] = {}
        self.players: dict[int, PlayerValue] = {}
        self.league: dict[int, list[float]] = {}  # season -> [points, possessions, team games]
        self.by_abbrev: dict[str, int] = {}
        self.last_day: date | None = None

    # ---- lookups -----------------------------------------------------------------------------------

    def team(self, team_id: int, abbrev: str = "") -> Team:
        t = self.teams.get(team_id)
        if t is None:
            t = self.teams[team_id] = Team(team_id, abbrev)
        if abbrev:
            t.abbrev = abbrev
            self.by_abbrev[abbrev] = team_id
        return t

    def team_id(self, abbrev: str) -> int:
        key = abbrev.strip().upper()
        key = key if key in self.by_abbrev else ALIASES.get(key, key)
        if key not in self.by_abbrev:
            raise KeyError(f"No team {abbrev!r}. Teams: {', '.join(sorted(self.by_abbrev))}")
        return self.by_abbrev[key]

    def league_now(self, season: int) -> tuple[float, float]:
        """The league's points per 100 possessions and pace: this season once it has 100 team games, else last."""
        for s in (season, season - 1):
            pts, poss, games = self.league.get(s, (0.0, 0.0, 0.0))
            if games >= (100 if s == season else 1):
                return 100 * pts / poss, poss / games
        return LEAGUE["off"], LEAGUE["pace"]

    def rotation(self, team_id: int) -> list[int]:
        """Players expected in uniform: in one of the team's last ROTATION_WINDOW games, this season once it
        is EARLY_GAMES old. Best first."""
        t = self.teams[team_id]
        ids = [p for p, last in t.roster.items() if last > t.played - ROTATION_WINDOW
               and (t.games < EARLY_GAMES or p in t.this_season)]
        return sorted(ids, key=lambda p: -self.players[p].value)

    def find_player(self, name: str, team_id: int | None = None) -> int | None:
        """A player by his full name (accents, punctuation and "Jr." aside), on ``team_id`` if given, the whole
        league otherwise. None if nobody matches."""
        key = name_key(name)
        pool = list(self.teams[team_id].roster) if team_id is not None else list(self.players)
        exact = [p for p in pool if name_key(self.players[p].name) == key]
        return max(exact, key=lambda p: self.players[p].den) if exact else None

    # ---- a team before a game ------------------------------------------------------------------------

    def _roll(self, t: Team, season: int) -> None:
        """Start a new season for a team: its ratings become the prior, its counts start again."""
        if t.season == season:
            return
        if t.season >= 0 and t.poss > 0:
            lg_off, lg_pace = self.league_now(t.season)
            pull = lambda x, lg: lg + PRIOR_KEEP * (x - lg)  # noqa: E731
            t.prior = {"off": pull(100 * t.pts / t.poss, lg_off), "def": pull(100 * t.opp_pts / t.poss, lg_off),
                       "pace": pull(t.poss / t.games, lg_pace)}
            t.elo = ELO_MEAN + ELO_CARRY * (t.elo - ELO_MEAN)
        t.season, t.games, t.wins, t.pts, t.opp_pts, t.poss = season, 0, 0, 0.0, 0.0, 0.0
        t.own, t.opp = dict.fromkeys(_SUMS, 0.0), dict.fromkeys(_SUMS, 0.0)
        t.this_season = set()
        t.recent.clear()  # last spring's form says little about a new roster

    def _team_row(self, t: Team, day: date, season: int, playing: list[int] | None) -> dict[str, float]:
        lg_off, lg_pace = self.league_now(season)
        prior = t.prior or {"off": lg_off, "def": lg_off, "pace": lg_pace}
        # Last season's ratings are in last season's scoring: move them by how far the league has moved since.
        drift = lg_off - self.league_now(season - 1)[0] if t.prior and season - 1 in self.league else 0.0
        off = 100 * (t.pts + (prior["off"] + drift) / 100 * PRIOR_POSS) / (t.poss + PRIOR_POSS)
        dfn = 100 * (t.opp_pts + (prior["def"] + drift) / 100 * PRIOR_POSS) / (t.poss + PRIOR_POSS)
        k = FACTOR_PRIOR_GAMES

        def factors(a: dict, b: dict) -> dict[str, float]:
            """Shooting, turnovers, offensive rebounds and free throws for the side whose sums are ``a``."""
            g = max(t.games, 1)
            fga, fta, tov = a["fga"] / g, a["fta"] / g, a["tov"] / g
            w = t.games / (t.games + k)
            mix = lambda x, lg: w * x + (1 - w) * lg  # noqa: E731
            if t.games == 0:
                return {f: LEAGUE[f] for f in ("efg", "tov", "orb", "ftr")}
            return {"efg": mix((a["fgm"] + 0.5 * a["fg3m"]) / max(a["fga"], 1), LEAGUE["efg"]),
                    "tov": mix(tov / max(fga + 0.44 * fta + tov, 1), LEAGUE["tov"]),
                    "orb": mix(a["oreb"] / max(a["oreb"] + b["dreb"], 1), LEAGUE["orb"]),
                    "ftr": mix(a["fta"] / max(a["fga"], 1), LEAGUE["ftr"])}

        mine, theirs = factors(t.own, t.opp), factors(t.opp, t.own)
        today = day.toordinal()
        rest = min(today - t.days[-1], 7) if t.days else 7
        rotation = self.rotation(t.id)
        if playing is None:
            playing = rotation
        values = sorted((max(self.players[p].value, 0.0) for p in playing if p in self.players), reverse=True)
        there = set(playing)
        return {
            "elo": t.elo, "off": off, "def": dfn, "net": off - dfn,
            "pace": (t.poss + prior["pace"] * 10) / (t.games + 10),
            "winpct": (t.wins + 2.5) / (t.games + 5), "games": float(t.games),
            "l10_net": float(np.mean([r[0] for r in t.recent])) if t.recent else 0.0,
            "l10_win": float(np.mean([r[1] for r in t.recent])) if t.recent else 0.5,
            "rest": float(rest), "b2b": float(rest <= 1), "g7": float(sum(1 for d in t.days if today - d <= 7)),
            "avail": float(sum(values[:ROTATION_SIZE])),
            "missing": float(sum(max(self.players[p].value, 0.0) for p in rotation if p not in there)),
            **{f"{f}_off": v for f, v in mine.items()}, **{f"{f}_def": v for f, v in theirs.items()},
        }

    def matchup(self, home: int, away: int, day: date, season: int, neutral: bool = False,
                home_playing: list[int] | None = None, away_playing: list[int] | None = None) -> dict[str, float]:
        """The feature row for ``home`` hosting ``away`` on ``day``. ``*_playing`` are the players in uniform
        (the team's rotation when not given)."""
        th, ta = self.team(home), self.team(away)
        self._roll(th, season)
        self._roll(ta, season)
        h, a = self._team_row(th, day, season, home_playing), self._team_row(ta, day, season, away_playing)
        lg_off, lg_pace = self.league_now(season)
        row = {"elo_diff": h["elo"] - a["elo"], "neutral": float(neutral), "lg_off": lg_off, "lg_pace": lg_pace,
               "avail_diff": h["avail"] - a["avail"]}
        for k in ("net", "winpct", "l10_net", "l10_win", "efg_off", "tov_off", "orb_off", "ftr_off", "efg_def", "tov_def",
                  "orb_def", "ftr_def"):
            row[f"{k}_diff"] = h[k] - a[k]
        for k in ("off", "def", "pace", "games", "rest", "b2b", "g7", "avail", "missing"):
            row[f"{k}_home"], row[f"{k}_away"] = h[k], a[k]
        return {f: row[f] for f in FEATURES}

    # ---- after a game --------------------------------------------------------------------------------

    def update(self, season: int, day: date, home: int, away: int, home_box: dict, away_box: dict, neutral: bool,
               home_players: list[tuple], away_players: list[tuple]) -> None:
        """Take in a finished game. ``*_players`` are (player id, name, Game Score) for everyone who played."""
        th, ta = self.team(home), self.team(away)
        self._roll(th, season)
        self._roll(ta, season)
        poss = possessions(home_box, away_box)
        margin = home_box["PTS"] - away_box["PTS"]
        # Elo: the winner takes points from the loser, more for a bigger win and for an upset.
        gap = th.elo - ta.elo + (0.0 if neutral else HOME_ELO)
        expected = 1.0 / (1.0 + 10 ** (-gap / 400))
        won = 1.0 if margin > 0 else 0.0
        mult = (abs(margin) + 3) ** 0.8 / (7.5 + 0.006 * (gap if margin > 0 else -gap))
        shift = ELO_K * mult * (won - expected)
        th.elo += shift
        ta.elo -= shift
        lg = self.league.setdefault(season, [0.0, 0.0, 0.0])
        for t, box, opp, players in ((th, home_box, away_box, home_players), (ta, away_box, home_box, away_players)):
            t.games += 1
            t.played += 1
            t.wins += box["PTS"] > opp["PTS"]
            t.pts += box["PTS"]
            t.opp_pts += opp["PTS"]
            t.poss += poss
            for k, col in zip(_SUMS, ("FGM", "FG3M", "FGA", "FTA", "TOV", "OREB", "DREB")):
                t.own[k] += box[col]
                t.opp[k] += opp[col]
            t.recent.append((100 * (box["PTS"] - opp["PTS"]) / poss, float(box["PTS"] > opp["PTS"])))
            t.days.append(day.toordinal())
            lg[0] += box["PTS"]
            lg[1] += poss
            lg[2] += 1
            decay = 0.5 ** (1 / VALUE_HALF_LIFE)
            for pid, name, score in players:
                p = self.players.get(pid)
                if p is None:
                    p = self.players[pid] = PlayerValue()
                if p.team is not None and p.team != t.id and p.team in self.teams:
                    self.teams[p.team].roster.pop(pid, None)  # he has moved
                p.num, p.den, p.team, p.name = p.num * decay + score, p.den * decay + 1.0, t.id, name
                t.roster[pid] = t.played
                t.this_season.add(pid)
        self.last_day = day


def pair_games(team_logs: pd.DataFrame) -> pd.DataFrame:
    """One row per game from the two teams' rows, in the order they were played."""
    home = team_logs[team_logs["HOME"]].set_index("GAME_ID")
    away = team_logs[~team_logs["HOME"]].set_index("GAME_ID")
    games = home.join(away, lsuffix="_H", rsuffix="_A", how="inner").reset_index()
    games["DAY"] = pd.to_datetime(games["GAME_DATE_H"]).dt.date
    return games.sort_values(["DAY", "GAME_ID"]).reset_index(drop=True)


def build(team_logs: pd.DataFrame, player_logs: pd.DataFrame) -> tuple[pd.DataFrame, FeatureState]:
    """A feature row (and what happened) for every game played, and the state after the last of them."""
    games = pair_games(team_logs)
    # Who was in uniform (for the game's own row) and who played (for what is learned from it afterwards).
    dressed = player_logs[player_logs["DRESSED"].astype(bool) | (player_logs["MIN"] > 0)]
    scores = game_score(dressed).to_numpy()
    who: dict[tuple[str, int], list[tuple]] = {}
    uniform: dict[tuple[str, int], list[int]] = {}
    for gid, tid, pid, name, score, minutes in zip(dressed["GAME_ID"].to_numpy(), dressed["TEAM_ID"].to_numpy(),
                                                   dressed["PLAYER_ID"].to_numpy(), dressed["PLAYER"].to_numpy(), scores,
                                                   dressed["MIN"].to_numpy()):
        uniform.setdefault((gid, int(tid)), []).append(int(pid))
        if minutes > 0:
            who.setdefault((gid, int(tid)), []).append((int(pid), name, float(score)))
    box_cols = ["PTS", "FGM", "FG3M", "FGA", "FTA", "TOV", "OREB", "DREB"]
    state = FeatureState()
    rows = []
    for g in games.to_dict("records"):
        season, day, home, away = int(g["SEASON_H"]), g["DAY"], int(g["TEAM_ID_H"]), int(g["TEAM_ID_A"])
        state.team(home, g["TEAM_H"])
        state.team(away, g["TEAM_A"])
        neutral = season == BUBBLE[0] and day >= BUBBLE[1]
        hp, ap = who.get((g["GAME_ID"], home), []), who.get((g["GAME_ID"], away), [])
        row = state.matchup(home, away, day, season, neutral, uniform.get((g["GAME_ID"], home)),
                            uniform.get((g["GAME_ID"], away)))
        hb, ab = {c: float(g[f"{c}_H"]) for c in box_cols}, {c: float(g[f"{c}_A"]) for c in box_cols}
        rows.append({"game_id": g["GAME_ID"], "season": season, "date": day, "home": g["TEAM_H"], "away": g["TEAM_A"],
                     **row, "home_win": int(hb["PTS"] > ab["PTS"]), "margin": hb["PTS"] - ab["PTS"],
                     "total": hb["PTS"] + ab["PTS"], "home_pts": hb["PTS"], "away_pts": ab["PTS"]})
        state.update(season, day, home, away, hb, ab, neutral, hp, ap)
    return pd.DataFrame(rows), state


def elo_probability(elo_diff: float | np.ndarray, neutral: float | np.ndarray = 0.0) -> float | np.ndarray:
    """Home win probability from the Elo ratings alone: the simplest sensible forecast, and the one to beat."""
    return 1.0 / (1.0 + 10 ** (-(elo_diff + HOME_ELO * (1 - neutral)) / 400))


def logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))
