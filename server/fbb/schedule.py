"""Calendar helpers: scoring periods (days) <-> matchup periods (fantasy weeks)."""

from __future__ import annotations

from datetime import date, timedelta

from .models import LeagueSnapshot, ProSchedule


def build_matchup_periods(sched: ProSchedule, regular_matchups: int, playoff_round_weeks: list[int],
                          min_game_days: int = 4) -> dict[int, list[int]]:
    """Group scoring periods into Monday-Sunday fantasy weeks, ESPN style.

    Week 1 runs from opening night to the first Sunday. A week with fewer than
    ``min_game_days`` game days (the All-Star break) is merged into the next
    week, which is how ESPN builds its default calendar. Each playoff round
    spans ``playoff_round_weeks[i]`` weeks.
    """
    if not sched.period_dates:
        return {}
    periods = sorted(sched.period_dates)
    weeks: list[list[int]] = []
    current: list[int] = []
    for p in periods:
        current.append(p)
        if sched.period_dates[p].weekday() == 6:  # Sunday closes a week
            weeks.append(current)
            current = []
    if current:
        weeks.append(current)

    merged: list[list[int]] = []
    carry: list[int] = []
    for i, wk in enumerate(weeks):
        wk = carry + wk
        carry = []
        game_days = sum(1 for p in wk if sched.games.get(p))
        if i > 0 and game_days < min_game_days and i < len(weeks) - 1:
            carry = wk
            continue
        merged.append(wk)
    # Drop trailing weeks without games (calendar padding after the season).
    while merged and not any(sched.games.get(p) for p in merged[-1]):
        merged.pop()

    out: dict[int, list[int]] = {}
    idx = 0
    for mp in range(1, regular_matchups + 1):
        if idx >= len(merged):
            break
        out[mp] = merged[idx]
        idx += 1
    mp = regular_matchups
    for weeks_in_round in playoff_round_weeks:
        days: list[int] = []
        for _ in range(weeks_in_round):
            if idx < len(merged):
                days += merged[idx]
                idx += 1
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
