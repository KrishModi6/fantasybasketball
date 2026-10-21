# Data connectors

Every source produces the same provider-neutral `LeagueSnapshot` (`server/fbb/models.py`), so the analytics never depend on where data came from. Connectors live in `server/fbb/connectors/`.

| Connector | Status | You supply |
|---|---|---|
| ESPN Fantasy | **Working, verified** against live 2026‑27 data | League ID; `espn_s2` + `SWID` cookies if the league is private |
| File import (CSV / JSON) | Working | A file |
| Demo | Working | Nothing |
| Flaim (MCP) | **Unverified adapter** | A Flaim OAuth access token, platform, league ID |

## ESPN

Uses ESPN's v3 fantasy API, the JSON the ESPN website itself loads. It isn't officially documented, so parsing is defensive and pinned by tests.

- **League:** `lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}/segments/0/leagues/{id}`, with views `mSettings`, `mTeam`, `mRoster`, `mMatchupScore`, `mStatus`. From these we read:
  - settings (scoring items, lineup slot counts, matchup periods, playoffs, acquisition limit, trade deadline);
  - teams, owners and records;
  - rosters and lineup slots;
  - the matchup schedule and scores.
- **Players:** `view=kona_player_info` with an `X-Fantasy-Filter` header: the top ~650 players by ownership, with season totals for this and last season, ESPN's projection, and the last 20 game logs.
- **NBA schedule:** `/seasons/{season}?view=proTeamSchedules_wl`, games per team per scoring period (day).
- **Injuries and news:** `site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries` and `/news`.
- **Player ages:** `site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{id}`, cached in `data/history/athletes.json`.
- **Live matchup data:** points use `totalPointsLive` when present (it updates during the matchup). For category leagues, totals accrued so far come from `cumulativeScore.scoreByStat` (the `mScoreboard` view). Both field usages and every id mapping (stats, lineup slots, NBA teams) were cross-checked against the open-source [cwendt94/espn-api](https://github.com/cwendt94/espn-api), whose constants match this project's apart from naming aliases (`PHL`/`PHI`, `PHO`/`PHX`, `3PT%`/`3P%`).
- **Reliability:**
  - requests are spaced ≥ 0.35 s apart, process-wide;
  - 429/5xx responses are retried with back-off, honouring `Retry-After`;
  - a stale-cookie HTML response becomes a clear error message;
  - injury and news failures never block a sync.

**Private leagues.** Sign in at fantasy.espn.com. Open DevTools → Application → Cookies → `https://fantasy.espn.com` and copy `espn_s2` (a long string) and `SWID` (`{…}`). Paste them when connecting; the owner can update them later under League hub → Data & sync. They're stored encrypted (Fernet, key derived from `FBB_SECRET_KEY`).

## File import

**CSV**: one row per rostered player.
```csv
team,owner,player,slot
Court Vision,Alex,Nikola Jokic,C
Court Vision,Alex,Anthony Edwards,
Glass Cleaners,Jordan,Victor Wembanyama,
```
Only `team` and `player` are required. An optional `player_id` column takes ESPN player IDs and skips name matching.

**JSON roster spec**
```json
{
  "name": "My League",
  "season": 2027,
  "scoring": {"kind": "points", "points": {"PTS": 1, "REB": 1, "AST": 2, "STL": 4, "BLK": 4, "TO": -2,
                                           "FGM": 2, "FGA": -1, "FTM": 1, "FTA": -1, "3PM": 1}},
  "roster_slots": {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UT": 3, "BE": 3, "IR": 1},
  "teams": [{"name": "Court Vision", "owner": "Alex", "players": ["Nikola Jokic", "Anthony Edwards"]}],
  "records": {"Court Vision": {"wins": 3, "losses": 1}}
}
```

**Full snapshot.** A `LeagueSnapshot` JSON (same schema the server stores) is loaded as-is, for offline use.

Names are matched to ESPN's player pool after accent, punctuation and suffix normalisation ("Luka Dončić" = "luka doncic", "Jaren Jackson Jr." = "jaren jackson"). Unmatched names are listed in the league feed. Stats, injuries and the schedule refresh from ESPN's public data on every sync. Rosters change only when you re-import or, for scoring, edit settings in the app.

## Flaim

[Flaim](https://flaim.app) ([GitHub](https://github.com/jdguggs10/flaim)) connects ESPN, Yahoo and Sleeper leagues to AI assistants through one MCP endpoint, `https://api.flaim.app/mcp`. It offers read-only tools including `get_league_info`, `get_roster`, `get_matchups`, `get_standings`, `get_free_agents`, `get_players` and `get_transactions`.

What's built (`fbb/connectors/flaim.py`):
- an MCP Streamable-HTTP JSON-RPC client: `initialize`, `tools/list`, `tools/call`, with session ID and SSE handling;
- defensive extraction of teams, rosters and records from tool output;
- matching players to ESPN's pool by name, the same path as file import.

Why it's marked **unverified**:
1. Flaim has no public REST API. Its MCP endpoint is protected by an OAuth sign-in designed for AI clients (Claude, ChatGPT), and there's no documented way for a third-party app to obtain a token.
2. The tools' output schema isn't published, so the mapping couldn't be tested against real responses.

To enable it, supply:
- a Flaim access token (from Flaim's OAuth flow);
- the platform (`espn`, `yahoo` or `sleeper`);
- the league ID as Flaim reports it, e.g. via its `get_user_session` tool.

If the tool arguments differ from `{platform, league_id, sport, season}`, adjust `FlaimConnector.fetch_snapshot`. Until then, ESPN users should use the direct connector, and Yahoo/Sleeper users the CSV import.

## Adding a connector

Subclass `LeagueConnector` (`fetch_snapshot()` returns a `LeagueSnapshot`; raise `ConnectorError` with a user-facing message). Then register it in `app/services/sync.py::build_connector` and `app/schemas.py::LeagueCreate`. Everything downstream works unchanged.
