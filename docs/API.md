# API reference

Base URL: your server, e.g. `http://localhost:8000`. Interactive docs (Swagger UI) are at `/docs` and the OpenAPI schema at `/openapi.json`.

**Auth.** `Authorization: Bearer <token>` using the token returned by `/api/auth/register` or `/api/auth/login` (JWT, HS256, 14-day expiry). Every league endpoint requires membership; a league you're not in returns `404`, the same as one that doesn't exist. Owner-only endpoints return `403` for members.

**Errors.** JSON `{"detail": "message"}`; validation errors use FastAPI's `detail` array.

**Rate limits.**
- Login and registration: 10 per minute per IP/email.
- Manual sync: once per 5 minutes for members; owners are exempt.

## Auth
| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/auth/register` | `{email, password (≥8), display_name}` | `{token, user}` |
| POST | `/api/auth/login` | `{email, password}` | `{token, user}` |
| GET | `/api/auth/me` | | `user` |

## Leagues
| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/api/leagues` | any | Your leagues, with `member_count` and `team_count` (teams in the latest snapshot; `null` before the first sync) |
| POST | `/api/leagues` | any | `{source: "espn"\|"demo"\|"flaim", name?, season, espn?: {league_id, espn_s2?, swid?}, flaim?: {flaim_token, platform, league_id}, team_count?}`. Runs the first sync inline; `400` with ESPN's error if the league can't be read |
| POST | `/api/leagues/import` | any | multipart `file` (.csv/.json, ≤2 MB), `name`, `season` |
| GET | `/api/leagues/{id}` | member | League, teams (with who claimed them), settings, sync status, your role/team |
| PATCH | `/api/leagues/{id}` | owner | `{name?, sync_interval_hours?, espn?}`. Blank cookie fields keep stored values |
| DELETE | `/api/leagues/{id}` | owner | |
| PUT | `/api/leagues/{id}/settings` | owner | `{kind, points, categories, roster_slots?}`. Demo/file leagues only |
| POST | `/api/leagues/{id}/sync` | member | Starts a background refresh (`202`) |

## Members & invites
| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/api/leagues/{id}/members` | member | |
| POST | `/api/leagues/{id}/members/me/team` | member | `{team_id \| null}`: claim/release your team (`409` if taken) |
| PATCH | `/api/leagues/{id}/members/{user_id}` | owner | `{team_id?, role?}` |
| DELETE | `/api/leagues/{id}/members/{user_id}` | owner, or self | Leave / remove |
| GET / POST | `/api/leagues/{id}/invites` | owner | POST `{expires_in_days?, max_uses?}` → `{code, url, …}` |
| DELETE | `/api/leagues/{id}/invites/{code}` | owner | Revoke |
| GET | `/api/invites/{code}` | **public** | Preview: league name, owner, teams and whether claimed |
| POST | `/api/invites/{code}/accept` | any | `{team_id?}` → `{league_id}` |

## Analytics
| Method | Path | Returns |
|---|---|---|
| GET | `/api/leagues/{id}/rankings` | `{teams: TeamStrength[], scoring, as_of}`. Rank, power score, pts/week, projected record, playoff/title odds, current matchup, explanation |
| GET | `/api/leagues/{id}/teams/{team_id}` | Roster with projections, expected starts and pts/week, upcoming weeks (mean ± sd), strength |
| GET | `/api/leagues/{id}/teams/{team_id}/report` | `{weaknesses, moves (add/drop), streaming}` |
| GET | `/api/leagues/{id}/teams/{team_id}/lineup?days=7` | Optimal daily lineups with a confidence per start |
| GET | `/api/leagues/{id}/players?status=all\|fa\|rostered&pos=&q=&team_id=&sort=&limit=` | Player table |
| GET | `/api/leagues/{id}/players/{player_id}` | Full projection breakdown: sources and weights, adjustments, SHAP factors, game log, seasons, news, daily trend, override |
| PUT / DELETE | `/api/leagues/{id}/players/{player_id}/override` | `{minutes_pct ∈ [−0.15, 0.15], out_games?, note}` (league-wide, logged) |
| GET | `/api/leagues/{id}/news` | ESPN headlines with linked players |
| GET | `/api/leagues/{id}/activity` | League feed |

A league with no data yet returns `409` from analytics endpoints, with the last sync error.

## Matchups
| Method | Path | Notes |
|---|---|---|
| GET | `/api/leagues/{id}/matchups?matchup_period=` | Remaining weeks plus that week's scheduled matchups with quick (Normal-approximation) projections and win probabilities. Defaults to the current week |
| POST | `/api/leagues/{id}/matchups/simulate` | `{team_a, team_b, matchup_period, exclude?: [player_id], sims?: 500–20000 (default 10000), seed?}` → win/tie probabilities, score and margin histograms, per-day and per-player results (`swing`, 80% range), category table for category leagues. Any two teams can be paired, not just scheduled opponents. `422` if the week is over or the teams are the same |

## Trades
| Method | Path | Notes |
|---|---|---|
| POST | `/api/leagues/{id}/trades/evaluate` | `{team_a, team_b, a_gives: [player_id], b_gives: [player_id]}` → `{teams, moves, sides[2], players, math, summary, fairness}`. Three-team trade: add `team_c`, `c_gives` and `send_to: {player_id: team_id}` (the team that gets each player); `sides` then has three entries. Each side lists `auto_drops` and `auto_adds` (forced by roster size), `open_spot_adds` (free agents its open spots get either way, part of the comparison) and `counted_as_pickups` (`[player, free agent]`: players traded away or dropped who project below a pickup, valued as that pickup) |
| GET | `/api/leagues/{id}/trades/suggestions?team_id=&sizes=1-1,2-2&partner_id=` | Ranked trade ideas for the listed package sizes (give-get, 1 to 3 each way, e.g. `sizes=3-3`). Without `sizes`: every size up to `max_give`-for-`max_get` (default 2, at most 3), or only that size with `exact=true` |
| GET | `/api/leagues/{id}/trades/offers?team_id=&target=&target=&give=1,2,3` | What to offer for 1 to 3 players (`target`, repeated) who are all on one other team. `give` lists how many of your players an offer may include. → `{partner_id, partner_name, get, get_names, offers, closest, checked}`: `offers` are the ones the partner's projection accepts (falls 0.4% at most), best for you first; `closest` (only when there are none) are the refusals that came nearest. `422` with a message saying what to fix if the targets are free agents, your own players or on two teams |
| GET | `/api/leagues/{id}/trades/asks?team_id=&player=&player=&partner_id=&get=1,2,3` | What to ask for 1 to 3 of your own players (`player`, repeated) from `partner_id`, or from every other team without it. `get` lists how many of their players a package may include. → `{partner_id, partner_name, give, give_names, offers, closest, checked}`: `offers` (each with its `partner_id`, `get` and `get_names`) are fair to both teams (neither falls more than 0.4%), the most for you first; `closest` (only when there are none) are the partner's refusals that came nearest. `422` with a message if the players aren't yours |
| GET | `/api/leagues/{id}/trades/three-team?team_id=&partner_id=` | Three-team rotations: you send one player to a team, it sends one to a third, which sends one back to you. Optional `partner_id` keeps rotations with that team. → `{suggestions, checked}`; each has `teams` (you first), `moves` (with `name`), `give`, `get`, your deltas, `partners` (`team_id`, `team_name`, `delta_ros`, `delta_pct`), `fairness`, `summary` |
| GET / POST | `/api/leagues/{id}/trades/board` | POST = evaluate + share `{…trade, note}`; re-scored after every refresh. GET rows include `teams` and `moves` (`{player_id, from_team, to_team}`) for two- and three-team trades |
| DELETE | `/api/leagues/{id}/trades/board/{post_id}` | Author or owner |

### `TradeSide`
```json
{"team_id": 1, "team_name": "…", "gives": ["…"], "receives": ["…"], "auto_drops": [], "auto_adds": ["…"],
 "before_ros": 28777, "after_ros": 30143, "delta_ros": 1366, "delta_pct": 0.047, "delta_per_week": 57.6,
 "delta_this_week": 12.0, "delta_playoffs": 183, "raw_value_delta": -451,
 "before_playoff_odds": 0.76, "after_playoff_odds": 0.94, "before_title_odds": 0.27, "after_title_odds": 0.49,
 "verdict": "advantageous"}
```

## Meta
| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | `{ok: true}` |
| GET | `/api/model` | Model card: backtest, blend weights, priors, data audit |
| GET | `/api/connectors` | Available connectors and what each needs |
| GET | `/api/import/example.csv` | Example import file |

## Realtime
`GET ws(s)://host/api/ws/leagues/{id}?token=<jwt>`. Members only; the server closes the socket with code `4401` otherwise. The server pushes JSON events; clients may send any text as a keep-alive.

```json
{"type": "sync_started" | "sync_completed" | "sync_failed" | "trade_shared" | "trade_removed" |
         "member_joined" | "member_updated" | "override_changed" | "settings_changed",
 "at": "2026-10-21T22:01:00Z", "...": "event-specific fields"}
```
