"""Calendar helpers: scoring periods (days) <-> matchup periods (fantasy weeks)."""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median

from .models import LeagueSnapshot, ProSchedule


LIGHT_WEEK_SHARE = 0.8  # a week with fewer team-games than this share of a typical week is "light"


def week_units(sched: ProSchedule, light_share: float = LIGHT_WEEK_SHARE) -> list[list[int]]:
    """Group scoring periods (days) into fantasy weeks the way ESPN builds its calendar.

    Week 1 runs from opening night to the first Sunday, then weeks run Monday to Sunday. Back-to-back
    weeks that are each light on games (fewer team-games than ``light_share`` of a typical week), such
    as the All-Star break or the NBA Cup knockout round, form a single week, and a short, light final
    week joins the one before it. A lone light week stays on its own. This reproduces ESPN's own
    matchup dates for 2018-19 through 2023-24 and its week count for 2026-27; ESPN's matchup periods
    are numbered lists of these weeks.
    """
    if not sched.period_dates:
        return []
    weeks: list[list[int]] = []
    current: list[int] = []
    for p in sorted(sched.period_dates):
        current.append(p)
        if sched.period_dates[p].weekday() == 6:  # Sunday closes a week
            weeks.append(current)
            current = []
    if current:
        weeks.append(current)
    # Drop trailing weeks without games (calendar padding after the season).
    while weeks and not any(sched.games.get(p) for p in weeks[-1]):
        weeks.pop()
    if not weeks:
        return []
    load = [sum(len(sched.games.get(p, ())) for p in w) for w in weeks]
    light = [n < light_share * median(load) for n in load]
    units: list[list[int]] = []
    i = 0
    while i < len(weeks):
        j = i
        while light[i] and j + 1 < len(weeks) and light[j + 1]:
            j += 1
        units.append([p for w in weeks[i:j + 1] for p in w])
        i = j + 1
    if len(units) > 1 and light[-1] and len(weeks[-1]) < 7 and units[-1] == weeks[-1]:
        last = units.pop()
        units[-1] = units[-1] + last
    return units


def espn_matchup_periods(sched: ProSchedule, espn_weeks: dict[int, list[int]]) -> dict[int, list[int]] | None:
    """Turn ESPN's matchup periods into days.

    ESPN lists each matchup period as the numbered weeks it covers ({3: [3], 20: [20, 21]}), not as
    days. Returns None if ESPN counts a different number of weeks than the NBA schedule gives, so the
    caller can build the calendar itself instead.
    """
    units = week_units(sched)
    needed = max((w for ws in espn_weeks.values() for w in ws), default=0)
    if not units or needed != len(units):
        return None
    return {mp: [p for w in sorted(ws) for p in units[w - 1]] for mp, ws in sorted(espn_weeks.items())}


def build_matchup_periods(sched: ProSchedule, regular_matchups: int, playoff_round_weeks: list[int]) -> dict[int, list[int]]:
    """A default ESPN-style calendar: one week per regular-season matchup, then each playoff round
    spans ``playoff_round_weeks[i]`` weeks (see week_units for how weeks are formed)."""
    units = week_units(sched)
    out: dict[int, list[int]] = {}
    for mp in range(1, min(regular_matchups, len(units)) + 1):
        out[mp] = units[mp - 1]
    idx = mp = len(out)
    for weeks_in_round in playoff_round_weeks:
        days = [p for u in units[idx: idx + weeks_in_round] for p in u]
        idx += weeks_in_round
        if days:
            mp += 1
            out[mp] = days
    return out


def period_to_matchup(snapshot: LeagueSnapshot) -> dict[int, int]:
    out = {}
    for mp, days in snapshot.settings.matchup_periods.items():
        for d in days:
            out[d] = mp
    return out


def regular_season_periods(snapshot: LeagueSnapshot) -> list[int]:
    reg = snapshot.settings.regular_season_matchups
    return sorted(mp for mp in snapshot.settings.matchup_periods if mp <= reg)


def playoff_periods(snapshot: LeagueSnapshot) -> list[int]:
    reg = snapshot.settings.regular_season_matchups
    return sorted(mp for mp in snapshot.settings.matchup_periods if mp > reg)


def remaining_days(snapshot: LeagueSnapshot, through_playoffs: bool = True) -> list[int]:
    """Scoring periods from today to the end of the fantasy season."""
    mps = snapshot.settings.matchup_periods
    reg = snapshot.settings.regular_season_matchups
    days = []
    for mp, ds in mps.items():
        if not through_playoffs and mp > reg:
            continue
        days.extend(d for d in ds if d >= snapshot.current_period)
    return sorted(set(days))


def date_of(snapshot: LeagueSnapshot, period: int) -> date | None:
    d = snapshot.schedule.period_dates.get(period)
    if d:
        return d
    if snapshot.schedule.period_dates:
        p0 = min(snapshot.schedule.period_dates)
        return snapshot.schedule.period_dates[p0] + timedelta(days=period - p0)
    return None


def period_for_date(sched: ProSchedule, day: date) -> int | None:
    for p, d in sched.period_dates.items():
        if d == day:
            return p
    if sched.period_dates:
        p0 = min(sched.period_dates)
        return p0 + (day - sched.period_dates[p0]).days
    return None
