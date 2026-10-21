# Courtside

Fantasy basketball analytics for a whole league: player projections, team power rankings, lineup and add/drop advice, a trade analyzer and trade finder, and a shared league hub with invite links. It runs as a website (mobile-friendly, installable as a PWA) and as a desktop app for Windows and macOS built from the same code.

**Statistics come first.** Every number starts from box-score statistics and published projections. Machine learning is used only where a rolling backtest shows it reduces error. News, injuries and trades enter only as bounded, labelled adjustments to minutes and games played. See [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

---

## What it does

| Area | What you get |
|---|---|
| **Player projections** | Per-minute rates for 14 stats, plus minutes and availability, blended from a statistical baseline (Basketball-Reference SPS), ESPN's projection and a LightGBM model. Blend weights are learned out of sample, then updated in-season with Bayesian shrinkage. Every player shows its sources, weights, adjustments and SHAP factors. |
| **Team strength model** | A day-by-day optimal-lineup simulation for every team over the rest of the season gives weekly score distributions. Teams are ranked by all-play win rate, with projected records plus playoff and title odds from 5,000 simulated seasons of your actual schedule and bracket. Each rank comes with a "why": stat-by-stat edges, top contributors, depth, schedule and injury drag. |
| **Team improvement** | Weak spots ranked by points at stake. Add/drop moves are simulated against your lineup. Also: streaming pickups for the current week and daily lineups with a confidence score for every start. |
| **Matchup simulator** | Plays any week's head-to-head out 10,000 times, box score by box score: who suits up, each player's night, bench fill-ins when a starter sits, and points already scored. You get win probability, score and margin distributions, a day-by-day breakdown, each player's range and "swing", and what-if toggles to sit players. Works for points and category leagues. |
| **Trades** | The analyzer re-simulates both rosters and handles 2-for-1 roster spots. It shows the math step by step: raw value, lineup-adjusted change, playoff weeks, and playoff and title odds before and after. The verdict is advantageous, neutral or disadvantageous. The finder screens every 1–2 player package and fully simulates the best ones. |
| **League hub** | Accounts, invite links and codes, team claiming, standings, a shared trade board, a live activity feed (WebSockets), injury-change alerts and league-wide projection overrides. |
| **Data** | An ESPN connector (public, or private with cookies), CSV/JSON import for any platform, an adapter for Flaim (unverified, see below), scheduled refreshes, rate limiting and caching. |

## Quick start (local)

Prerequisites: Python 3.11+ and Node 20+.

```bash
cd server
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```
(macOS/Linux: `.venv/bin/python`.) The repo already includes the historical dataset and a trained model. To rebuild either, see *Retraining* below.

Start the API:
```bash
cd server
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

Start the web app (proxies `/api` to :8000):
```bash
cd web
npm install
npm run dev
```

Open http://localhost:5173, create an account, then **Try a demo league** or **Connect ESPN league**. For a ready-made local account with a demo league, run `python -m scripts.seed_dev` in `server/`; the credentials are at the top of that script.

### Desktop app
```bash
cd desktop
npm install
npm start
```
`npm start` builds the web app and opens it in Electron. Installers:
- **Windows:** `npm run dist:win` → `desktop/release/Courtside Setup 0.1.0.exe` (already built once on this machine).
- **macOS:** `npm run dist:mac` must run on a Mac. Signing and notarisation need an Apple Developer ID; without one the DMG works but Gatekeeper warns on first open.

The desktop app connects to a Courtside server; the address is set on the sign-in screen (default `http://localhost:8000`, or the `COURTSIDE_SERVER` environment variable).

### Mobile
The website is fully responsive (the tab strip scrolls and tables scroll inside their panels on phones), and it's installable as a PWA (Add to Home Screen) on iOS and Android. Everything syncs through the server in real time.

## Connecting your league

The ESPN connector talks to ESPN's fantasy API directly. Its field mappings were cross-checked against the open-source [cwendt94/espn-api](https://github.com/cwendt94/espn-api), and it borrows that library's handling of live matchup points and in-progress category totals. It isn't a dependency, because it reads the same endpoints this connector already does.

- **ESPN (recommended).** Paste your league URL or ID. Private leagues also need the `espn_s2` and `SWID` cookies from a logged-in browser (DevTools → Application → Cookies → fantasy.espn.com). They're encrypted at rest and only used to read the league. Your league's real scoring settings, lineup slots, schedule, matchups and acquisition limit are mirrored on every refresh.
- **File import.** CSV (`team, player[, owner, slot]`) or JSON. Rosters come from the file; stats, injuries and the NBA schedule still refresh from ESPN's public data. Details in [docs/CONNECTORS.md](docs/CONNECTORS.md).
- **Flaim.** Flaim exposes leagues via an MCP server for AI assistants, with no public REST API, and its output format is unpublished. The adapter speaks MCP over HTTP and maps rosters defensively, but it's **unverified**. To try it you need a Flaim OAuth access token and your league ID as Flaim knows it. For ESPN leagues, the direct connector is better.

## Backtest (how much the ML helps)

Rolling origin: each season is predicted using only earlier seasons. Error is in fantasy points per game under the reference league's scoring (ESPN default H2H points).

**Head-to-head with ESPN**, on players with 20+ games and a genuine ESPN preseason projection (2021‑22, 2023‑24, 2024‑25, 2025‑26):

| Method | FP/G MAE | FP/G RMSE | Season-total MAE | Rank ρ |
|---|---|---|---|---|
| Last season repeated | 4.78 | 6.06 | 515 | 0.667 |
| Statistical baseline (SPS) | 4.62 | 5.89 | 469 | 0.700 |
| ESPN preseason projection | 4.57 | 5.79 | 507 | **0.736** |
| LightGBM alone | 4.39 | 5.58 | 459 | 0.721 |
| **Courtside ensemble** | **4.34** | **5.49** | **448** | 0.732 |

The ensemble is 5% more accurate than ESPN per game and 12% more accurate on season totals, and it beat ESPN in every one of these seasons; ranking accuracy is a statistical tie. A data audit excluded ESPN's stored projections for 2019‑20 and 2022‑23, because they were refreshed in-season, and removed ADP as a feature because ESPN overwrites it for past seasons. Both choices are documented in the methodology.

## Project layout

```
server/            FastAPI backend + analytics engine
  fbb/             engine (no web deps): projections, lineup optimiser, strength, trades, valuation
    matchup_sim.py Monte Carlo matchup simulator
    connectors/    espn, file import, flaim (MCP), demo
    ml/            feature builder + LightGBM wrapper
  app/             API: auth, leagues, invites, analysis, trades, realtime, scheduler
  scripts/         build_history, fetch_game_logs, train_model, seed_dev
  data/history/    player-season dataset 2017-18 → today (from ESPN)
  data/model/      trained model + metrics.json (the backtest above)
  tests/           engine unit tests + end-to-end API test
web/               React + TypeScript + Vite client (also the desktop UI)
desktop/           Electron shell, installers in desktop/release
docs/              methodology, API, user guide, admin guide, connectors
```

## Tests

```bash
cd server && .venv/Scripts/python -m pytest      # 31 tests (the API test needs internet once; ESPN data is cached)
cd web && npm test && npm run typecheck
```

## Deploying for your league

Everyone in a league needs to reach one server. The simplest option is the included Dockerfile on any container host (Fly.io, Render, Railway, a VPS):
```bash
docker build -t courtside .
docker run -p 8000:8000 -v courtside-data:/app/server/data/db -e FBB_SECRET_KEY=... -e FBB_PUBLIC_URL=https://your.host courtside
```
Free hosting (Cloud Run + Neon, or Render): see [docs/DEPLOY.md](docs/DEPLOY.md). Environment variables, backups and scaling: [docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md).

## Retraining

```bash
cd server
.venv/Scripts/python -m scripts.build_history     # refresh player seasons + ages from ESPN
.venv/Scripts/python -m scripts.fetch_game_logs   # game logs for the overdispersion estimate
.venv/Scripts/python -m scripts.train_model       # backtest, learn blend weights, save model + metrics
```
The server hot-reloads the model when the file changes. Set `FBB_RETRAIN_ENABLED=true` to retrain weekly.

## Documentation

- [Methodology & model card](docs/METHODOLOGY.md)
- [User guide](docs/USER_GUIDE.md)
- [Admin guide](docs/ADMIN_GUIDE.md)
- [API reference](docs/API.md) (interactive docs at `/docs` on a running server)
- [Connectors](docs/CONNECTORS.md)
