"""Game data: every team's and every player's box score for each game, the day's schedule, and injuries.

History comes from the sportsdataverse project (the data behind hoopR and sportsdataverse-py), which publishes
ESPN's box scores as one small file per season: one for teams, one for players. Finished seasons are cached
for good; the season in progress is downloaded again once the cached copy is a few hours old. Its file can
run a day or two behind, so the latest games are topped up from ESPN directly, a few requests at most.
The schedule for a day and the injury report also come from ESPN.

Seasons are named by the year they start in: 2025 is 2025-26 (sportsdataverse and ESPN call that one 2026).
"""

from __future__ import annotations

import io
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RELEASES = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
TEAM_FILE = RELEASES + "/espn_nba_team_boxscores/team_box_{year}.parquet"
PLAYER_FILE = RELEASES + "/espn_nba_player_boxscores/player_box_{year}.parquet"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
HEADERS = {"User-Agent": "gamepred/0.1", "Accept": "application/json, */*"}
EASTERN = ZoneInfo("America/New_York")
FIRST_SEASON = 2014  # 2014-15: one season of run-in for ratings before the first season trained on
CURRENT_TTL = timedelta(hours=3)
REGULAR_SEASON = 2  # ESPN's season type
MAX_TOPUP_DAYS = 10  # how far behind the published file ESPN is asked to make up
BOX = ["FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TOV", "PF", "PTS"]
TEAM_COLUMNS = ["SEASON", "GAME_ID", "GAME_DATE", "TEAM_ID", "TEAM", "HOME", "WIN", *BOX]
PLAYER_COLUMNS = ["SEASON", "GAME_ID", "GAME_DATE", "TEAM_ID", "TEAM", "PLAYER_ID", "PLAYER", "MIN", *BOX, "DRESSED"]
_SHARED = {"field_goals_made": "FGM", "field_goals_attempted": "FGA", "three_point_field_goals_made": "FG3M",
           "three_point_field_goals_attempted": "FG3A", "free_throws_made": "FTM", "free_throws_attempted": "FTA",
           "offensive_rebounds": "OREB", "defensive_rebounds": "DREB", "assists": "AST", "steals": "STL", "blocks": "BLK",
           "fouls": "PF"}


def season_label(season: int) -> str:
    """2025 -> "2025-26"."""
    return f"{season}-{(season + 1) % 100:02d}"


def current_season(day: date | None = None) -> int:
    """The season in progress on ``day``: a new one starts in October, and the summer belongs to the last."""
    day = day or today()
    return day.year if day.month >= 10 else day.year - 1


def today() -> date:
    """Today on the NBA's calendar (US Eastern)."""
    return datetime.now(EASTERN).date()


def _get(url: str, params: dict | None = None, attempts: int = 3, timeout: float = 60.0) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            r = httpx.get(url, params=params, headers=HEADERS, timeout=timeout, follow_redirects=True)
            if r.status_code == 404:
                raise FileNotFoundError(url)
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {r.status_code}")
            else:
                r.raise_for_status()
                return r
        except httpx.HTTPError as exc:
            last = exc
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Couldn't reach {url.split('/')[2]} after {attempts} tries: {last}")


# ---- season files ------------------------------------------------------------------------------------


def tidy_team_box(raw: pd.DataFrame) -> pd.DataFrame:
    """sportsdataverse's team box scores as the columns the features use: regular-season NBA games only."""
    df = raw[(raw["season_type"] == REGULAR_SEASON) & raw["team_id"].between(1, 30)
             & raw["opponent_team_id"].between(1, 30)].copy()
    out = pd.DataFrame({
        "SEASON": df["season"].astype(int) - 1, "GAME_ID": df["game_id"].astype(str),
        "GAME_DATE": pd.to_datetime(df["game_date"]).dt.strftime("%Y-%m-%d"), "TEAM_ID": df["team_id"].astype(int),
        "TEAM": df["team_abbreviation"], "HOME": df["team_home_away"] == "home", "WIN": df["team_winner"].astype(bool),
    })
    for src, dst in _SHARED.items():
        out[dst] = pd.to_numeric(df[src], errors="coerce")
    out["REB"] = pd.to_numeric(df["total_rebounds"], errors="coerce")
    total = pd.to_numeric(df["total_turnovers"], errors="coerce") if "total_turnovers" in df else pd.Series(float("nan"), index=df.index)
    out["TOV"] = total.fillna(pd.to_numeric(df["turnovers"], errors="coerce"))  # team turnovers included when ESPN has them
    out["PTS"] = pd.to_numeric(df["team_score"], errors="coerce")
    return out[TEAM_COLUMNS].dropna(subset=["PTS", "FGA"]).reset_index(drop=True)


def tidy_player_box(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw[(raw["season_type"] == REGULAR_SEASON) & raw["team_id"].between(1, 30) & raw["athlete_id"].notna()].copy()
    out = pd.DataFrame({
        "SEASON": df["season"].astype(int) - 1, "GAME_ID": df["game_id"].astype(str),
        "GAME_DATE": pd.to_datetime(df["game_date"]).dt.strftime("%Y-%m-%d"), "TEAM_ID": df["team_id"].astype(int),
        "TEAM": df["team_abbreviation"], "PLAYER_ID": df["athlete_id"].astype(int), "PLAYER": df["athlete_display_name"],
        "MIN": pd.to_numeric(df["minutes"], errors="coerce").fillna(0.0),
    })
    for src, dst in _SHARED.items():
        out[dst] = pd.to_numeric(df[src], errors="coerce").fillna(0.0)
    out["REB"] = pd.to_numeric(df["rebounds"], errors="coerce").fillna(0.0)
    out["TOV"] = pd.to_numeric(df["turnovers"], errors="coerce").fillna(0.0)
    out["PTS"] = pd.to_numeric(df["points"], errors="coerce").fillna(0.0)
    sat = df["did_not_play"].fillna(False).astype(bool).to_numpy()
    out.loc[sat, "MIN"] = 0.0
    # In uniform: he played, or he was healthy and the coach didn't use him. A player listed with an injury,
    # illness, rest or suspension was not, and neither was anyone the box score leaves out.
    coach = df["reason"].fillna("").str.upper().str.contains("COACH").to_numpy()
    out["DRESSED"] = (out["MIN"].to_numpy() > 0) | (sat & coach)
    return out[PLAYER_COLUMNS].reset_index(drop=True)


def _season_file(kind: str, url: str, tidy, season: int, refresh: bool) -> pd.DataFrame:
    """A season's table from the cache; downloaded when missing, and when it is the season in progress and stale."""
    path = DATA_DIR / f"{kind}_{season}.csv.gz"
    finished = season < current_season()
    if path.exists():
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if finished or (not refresh and age < CURRENT_TTL):
            return pd.read_csv(path, dtype={"GAME_ID": str})
    try:
        df = tidy(pd.read_parquet(io.BytesIO(_get(url.format(year=season + 1)).content)))
    except FileNotFoundError:  # a season that hasn't started, or isn't published yet
        return pd.DataFrame(columns=TEAM_COLUMNS if kind == "teams" else PLAYER_COLUMNS)
    except RuntimeError:
        if path.exists():  # GitHub is unreachable: an older copy beats none
            return pd.read_csv(path, dtype={"GAME_ID": str})
        raise
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


# ---- ESPN: the latest games, the schedule, injuries -----------------------------------------------------


def scoreboard(day: date) -> list[dict]:
    """ESPN's games on ``day``: [{GAME_ID, GAME_DATE, HOME, AWAY, HOME_ID, AWAY_ID, STATUS, NEUTRAL, TIP}], regular
    season and NBA teams only. STATUS is "scheduled", "live" or "final"."""
    events = _get(f"{ESPN}/scoreboard", {"dates": day.strftime("%Y%m%d"), "limit": 100}).json().get("events", [])
    games = []
    for e in events:
        comp = (e.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if (e.get("season") or {}).get("type") != REGULAR_SEASON or set(sides) != {"home", "away"}:
            continue
        ids = {k: int(c["team"]["id"]) for k, c in sides.items()}
        if not all(1 <= i <= 30 for i in ids.values()):
            continue  # an All-Star game
        state = ((e.get("status") or {}).get("type") or {}).get("state")
        games.append({"GAME_ID": str(e["id"]), "GAME_DATE": day.isoformat(), "TIP": e.get("date"),
                      "HOME": sides["home"]["team"]["abbreviation"], "AWAY": sides["away"]["team"]["abbreviation"],
                      "HOME_ID": ids["home"], "AWAY_ID": ids["away"], "NEUTRAL": bool(comp.get("neutralSite")),
                      "STATUS": {"pre": "scheduled", "in": "live", "post": "final"}.get(state, "scheduled")})
    return games


def _pair(value: str) -> tuple[float, float]:
    made, _, tried = str(value).partition("-")
    return float(made or 0), float(tried or 0)


def parse_summary(summary: dict, game: dict, season: int) -> tuple[list[dict], list[dict]]:
    """Team and player rows (the tidy columns) from one finished game's ESPN summary."""
    box = summary["boxscore"]
    scores = {int(c["team"]["id"]): float(c["score"]) for c in summary["header"]["competitions"][0]["competitors"]}
    base = {"SEASON": season, "GAME_ID": game["GAME_ID"], "GAME_DATE": game["GAME_DATE"]}
    teams = []
    for t in box["teams"]:
        tid = int(t["team"]["id"])
        s = {x["name"]: x["displayValue"] for x in t["statistics"]}
        fgm, fga = _pair(s["fieldGoalsMade-fieldGoalsAttempted"])
        tpm, tpa = _pair(s["threePointFieldGoalsMade-threePointFieldGoalsAttempted"])
        ftm, fta = _pair(s["freeThrowsMade-freeThrowsAttempted"])
        teams.append({**base, "TEAM_ID": tid, "TEAM": t["team"]["abbreviation"], "HOME": tid == game["HOME_ID"],
                      "WIN": scores[tid] == max(scores.values()), "FGM": fgm, "FGA": fga, "FG3M": tpm, "FG3A": tpa,
                      "FTM": ftm, "FTA": fta, "OREB": float(s["offensiveRebounds"]), "DREB": float(s["defensiveRebounds"]),
                      "REB": float(s["totalRebounds"]), "AST": float(s["assists"]), "STL": float(s["steals"]),
                      "BLK": float(s["blocks"]), "TOV": float(s.get("totalTurnovers") or s["turnovers"]),
                      "PF": float(s["fouls"]), "PTS": scores[tid]})
    players = []
    for t in box.get("players", []):
        tid, abbrev = int(t["team"]["id"]), t["team"]["abbreviation"]
        for block in t.get("statistics", []):
            keys = block.get("keys") or []
            for a in block.get("athletes", []):
                row = dict(zip(keys, a.get("stats") or []))
                sat = bool(a.get("didNotPlay")) or not row
                if sat and "COACH" not in str(a.get("reason") or "").upper():
                    continue  # hurt, ill, resting or suspended: not in uniform
                if sat:
                    row = {}
                num = lambda k: float(row.get(k) or 0) if str(row.get(k) or 0).lstrip("-+").replace(".", "", 1).isdigit() else 0.0  # noqa: E731
                fgm, fga = _pair(row.get("fieldGoalsMade-fieldGoalsAttempted", "0-0"))
                tpm, tpa = _pair(row.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted", "0-0"))
                ftm, fta = _pair(row.get("freeThrowsMade-freeThrowsAttempted", "0-0"))
                players.append({**base, "TEAM_ID": tid, "TEAM": abbrev, "PLAYER_ID": int(a["athlete"]["id"]),
                                "PLAYER": a["athlete"]["displayName"], "MIN": num("minutes"), "FGM": fgm, "FGA": fga,
                                "FG3M": tpm, "FG3A": tpa, "FTM": ftm, "FTA": fta, "OREB": num("offensiveRebounds"),
                                "DREB": num("defensiveRebounds"), "REB": num("rebounds"), "AST": num("assists"),
                                "STL": num("steals"), "BLK": num("blocks"), "TOV": num("turnovers"), "PF": num("fouls"),
                                "PTS": num("points"), "DRESSED": True})
    return teams, players


def top_up(teams: pd.DataFrame, players: pd.DataFrame, progress=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add finished games the season file doesn't have yet, from ESPN (up to MAX_TOPUP_DAYS back). Each game is
    fetched once and kept. Never fails a prediction: with ESPN unreachable the file's games are used as they are."""
    season = current_season()
    have = set(teams.loc[teams["SEASON"] == season, "GAME_ID"])
    cache = DATA_DIR / f"topup_{season}.json"
    kept = json.loads(cache.read_text()) if cache.exists() else {}
    last = teams.loc[teams["SEASON"] == season, "GAME_DATE"].max() if have else None
    start = max(date.fromisoformat(last), today() - timedelta(days=MAX_TOPUP_DAYS)) if last else today() - timedelta(days=MAX_TOPUP_DAYS)
    try:
        day = start
        while day < today():
            for g in scoreboard(day):
                if g["STATUS"] != "final" or g["GAME_ID"] in have:
                    continue
                if g["GAME_ID"] not in kept:
                    summary = _get(f"{ESPN}/summary", {"event": g["GAME_ID"]}).json()
                    t_rows, p_rows = parse_summary(summary, g, season)
                    kept[g["GAME_ID"]] = {"teams": t_rows, "players": p_rows}
                    time.sleep(0.3)
            day += timedelta(days=1)
    except (RuntimeError, KeyError, ValueError, FileNotFoundError) as exc:
        if progress:
            progress(f"ESPN top-up stopped ({exc}); using the games on file")
    kept = {gid: v for gid, v in kept.items() if gid not in have}  # the file has caught up with these
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(kept))
    if kept:
        teams = pd.concat([teams, pd.DataFrame([r for v in kept.values() for r in v["teams"]])[TEAM_COLUMNS]], ignore_index=True)
        players = pd.concat([players, pd.DataFrame([r for v in kept.values() for r in v["players"]])[PLAYER_COLUMNS]],
                            ignore_index=True)
        if progress:
            progress(f"{len(kept)} recent game(s) added from ESPN")
    return teams, players


def load_logs(first: int = FIRST_SEASON, last: int | None = None, refresh: bool = False, recent: bool = True,
              progress=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Team and player box scores for seasons ``first`` to ``last`` (the season in progress by default)."""
    last = current_season() if last is None else last
    teams, players = [], []
    for season in range(first, last + 1):
        teams.append(_season_file("teams", TEAM_FILE, tidy_team_box, season, refresh))
        players.append(_season_file("players", PLAYER_FILE, tidy_player_box, season, refresh))
        if progress and len(teams[-1]):
            progress(f"{season_label(season)}: {len(teams[-1]) // 2:,} games, {len(players[-1]):,} player games")
    all_teams = pd.concat([t for t in teams if len(t)], ignore_index=True)
    all_players = pd.concat([p for p in players if len(p)], ignore_index=True)
    if recent and last == current_season():
        all_teams, all_players = top_up(all_teams, all_players, progress)
    return all_teams, all_players


def load_schedule(day: date) -> pd.DataFrame:
    """The games on ``day``, played or not."""
    return pd.DataFrame(scoreboard(day), columns=["GAME_ID", "GAME_DATE", "TIP", "HOME", "AWAY", "HOME_ID", "AWAY_ID",
                                                  "NEUTRAL", "STATUS"])


def load_injuries() -> dict[str, str]:
    """Player name -> status ("Out", "Day-To-Day", ...) from ESPN's league injury report. Empty if unreachable:
    the report only sharpens a prediction, so a prediction never fails for want of it."""
    try:
        data = _get(f"{ESPN}/injuries", attempts=2, timeout=20.0).json()
    except (RuntimeError, ValueError, FileNotFoundError):
        return {}
    out = {}
    for team in data.get("injuries", []):
        for inj in team.get("injuries", []):
            name = (inj.get("athlete") or {}).get("displayName")
            if name and inj.get("status"):
                out[name] = inj["status"]
    return out
