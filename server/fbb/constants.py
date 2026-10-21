"""Canonical stat keys, lineup slots and ESPN id mappings.

Everything inside the engine uses the canonical string keys below. Connectors
translate provider-specific ids (ESPN stat 17 -> "3PM") at the boundary.
"""

from __future__ import annotations

# Counting stats the projection system models directly. Shooting is modelled as
# makes + misses (attempts are derived), following Basketball-Reference's SPS.
COUNTING_STATS = [
    "PTS", "REB", "AST", "STL", "BLK", "TO",
    "FGM", "FGA", "FTM", "FTA", "3PM", "3PA",
    "OREB", "DREB", "DD", "TD", "PF",
]

# ESPN fantasy stat ids (basketball). Source: ESPN v3 fantasy API payloads,
# cross-checked against the open-source cwendt94/espn-api constants.
ESPN_STAT_IDS: dict[int, str] = {
    0: "PTS", 1: "BLK", 2: "STL", 3: "AST", 4: "OREB", 5: "DREB", 6: "REB",
    7: "EJ", 8: "FF", 9: "PF", 10: "TF", 11: "TO", 12: "DQ",
    13: "FGM", 14: "FGA", 15: "FTM", 16: "FTA", 17: "3PM", 18: "3PA",
    19: "FG%", 20: "FT%", 21: "3P%", 22: "EFG%", 23: "FGMI", 24: "FTMI", 25: "3PMI",
    37: "DD", 38: "TD", 39: "QD", 40: "MIN", 41: "GS", 42: "GP",
}
STAT_KEY_TO_ESPN = {v: k for k, v in ESPN_STAT_IDS.items()}

# Derived stats that can appear in scoring settings and how to compute them
# from the counting stats above.
DERIVED_STATS = {
    "FGMI": lambda s: s.get("FGA", 0.0) - s.get("FGM", 0.0),
    "FTMI": lambda s: s.get("FTA", 0.0) - s.get("FTM", 0.0),
    "3PMI": lambda s: s.get("3PA", 0.0) - s.get("3PM", 0.0),
}

# Ratio categories for category leagues: (numerator, denominator).
RATIO_STATS = {
    "FG%": ("FGM", "FGA"),
    "FT%": ("FTM", "FTA"),
    "3P%": ("3PM", "3PA"),
    "EFG%": ("EFGM", "FGA"),  # EFGM = FGM + 0.5 * 3PM, computed on the fly
}

# Stats where a lower total is better (category leagues).
LOWER_IS_BETTER = {"TO", "PF", "EJ", "FF", "TF", "DQ", "FGMI", "FTMI", "3PMI"}

BASE_POSITIONS = ["PG", "SG", "SF", "PF", "C"]

# ESPN lineup slot ids -> slot code.
ESPN_SLOT_IDS: dict[int, str] = {
    0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F",
    7: "SG/SF", 8: "G/F", 9: "PF/C", 10: "F/C", 11: "UT", 12: "BE", 13: "IR",
}
SLOT_CODE_TO_ESPN = {v: k for k, v in ESPN_SLOT_IDS.items()}

# Which base positions may fill each slot (used when a provider does not give
# per-player slot eligibility, e.g. CSV imports).
SLOT_ELIGIBILITY: dict[str, set[str]] = {
    "PG": {"PG"}, "SG": {"SG"}, "SF": {"SF"}, "PF": {"PF"}, "C": {"C"},
    "G": {"PG", "SG"}, "F": {"SF", "PF"},
    "SG/SF": {"SG", "SF"}, "G/F": {"PG", "SG", "SF", "PF"},
    "PF/C": {"PF", "C"}, "F/C": {"SF", "PF", "C"},
    "UT": set(BASE_POSITIONS), "BE": set(BASE_POSITIONS), "IR": set(BASE_POSITIONS),
}
NON_STARTING_SLOTS = {"BE", "IR"}

# ESPN defaultPositionId -> position.
ESPN_DEFAULT_POSITION = {1: "PG", 2: "SG", 3: "SF", 4: "PF", 5: "C", 6: "G", 7: "F", 8: "UT"}

ESPN_PRO_TEAMS: dict[int, str] = {
    0: "FA", 1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GSW", 10: "HOU", 11: "IND", 12: "LAC", 13: "LAL", 14: "MIA",
    15: "MIL", 16: "MIN", 17: "BKN", 18: "NYK", 19: "ORL", 20: "PHI", 21: "PHX",
    22: "POR", 23: "SAC", 24: "SAS", 25: "OKC", 26: "UTA", 27: "WAS", 28: "TOR",
    29: "MEM", 30: "CHA",
}

# ESPN's default H2H points scoring (also the settings in the reference league).
DEFAULT_POINTS_SCORING: dict[str, float] = {
    "FGM": 2, "FGA": -1, "FTM": 1, "FTA": -1, "3PM": 1,
    "REB": 1, "AST": 2, "STL": 4, "BLK": 4, "TO": -2, "PTS": 1,
}

# ESPN's default roster: 10 starters, 3 bench, 1 IR.
DEFAULT_ROSTER_SLOTS: dict[str, int] = {
    "PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UT": 3, "BE": 3, "IR": 1,
}

DEFAULT_CATEGORIES = ["FG%", "FT%", "3PM", "REB", "AST", "STL", "BLK", "TO", "PTS"]

# Regular-season length used to turn games played into availability rates.
SEASON_GAMES = {2020: 72, 2021: 72}  # shortened seasons; everything else is 82


def season_games(season: int) -> int:
    return SEASON_GAMES.get(season, 82)


def season_label(season: int) -> str:
    """ESPN seasonId 2027 -> "2026-27"."""
    return f"{season - 1}-{str(season)[2:]}"
