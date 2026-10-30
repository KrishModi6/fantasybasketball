# Courtside

Fantasy basketball analytics for a whole league: player projections, team power rankings, lineup and add/drop advice, a trade analyzer and trade finder, an AI assistant that explains it all, and a shared league hub with invite links. It runs as a website (mobile-friendly, installable as a PWA) and as a desktop app for Windows and macOS built from the same code.

**[Download the desktop app](https://github.com/KrishModi6/fantasybasketball/releases/latest)**: Windows installer or Mac app, with everything bundled. No server, no Python, no account.

**Statistics come first.** Every number starts from box-score statistics and published projections. Machine learning is used only where a rolling backtest shows it reduces error. News, injuries and trades enter only as bounded, labelled adjustments to minutes and games played. See [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

---

## What it does

| Area | What you get |
|---|---|
| **Player projections** | Per-minute rates for 14 stats, plus minutes and availability, blended from a statistical baseline (Basketball-Reference SPS), ESPN's projection and a LightGBM model. Blend weights are learned out of sample, then updated in-season with Bayesian shrinkage. Every player shows its sources, weights, adjustments and SHAP factors. |
| **Expected fantasy points (xFP)** | The Next Gen Stats idea applied to fantasy. NBA.com tracking prices each player's chances this season: every shot (by a shot model), potential assists, rebound chances, deflections and contests. Projections lean on those chances rather than how many went in, which cut rest-of-season error about 3% in a three-season backtest. Each player shows xFP/G and luck (FP/G minus xFP/G), so the buy-lows and sell-highs stand out. See [METHODOLOGY 1.5.1](docs/METHODOLOGY.md#151-expected-fantasy-points-xfp). |
| **Team strength model** | A day-by-day optimal-lineup simulation for every team over the rest of the season gives weekly score distributions. Teams are ranked by a power score that counts this season's record for the share of the season played and the projected all-play win rate for the rest, with projected records plus playoff and title odds from 5,000 simulated seasons of your actual schedule and bracket. A week-by-week grid shows every team's projected points (or win odds against that week's opponent) for the rest of the season. Each rank comes with a "why": stat-by-stat edges, top contributors, depth, schedule and injury drag. |
| **Team improvement** | Weak spots ranked by points at stake. Add/drop moves are simulated against your lineup. Also: streaming pickups for the current week and daily lineups with a confidence score for every start. |
| **Matchup simulator** | Plays any week's head-to-head out 10,000 times, box score by box score: who suits up, each player's night, bench fill-ins when a starter sits, and points already scored. You get win probability, score and margin distributions, a day-by-day breakdown, each player's range and "swing", and what-if toggles to sit players. Works for points and category leagues. |
| **Trades** | The analyzer re-simulates both rosters and handles 2-for-1 roster spots. It shows the math step by step: raw value, lineup-adjusted change, playoff weeks, and playoff and title odds before and after. The verdict is advantageous, neutral or disadvantageous. The finder screens 1-for-1, 2-for-2 or give-up-to-2-get-1 packages and fully simulates the best ones. |
| **AI assistant** | Ask in plain English: best waiver pickups, a game plan for this week's matchup, trades to pitch, injury impact, season outlook, power-rankings trash talk. The model calls Courtside's own engine (waiver simulation, trade evaluator, matchup simulator, rankings) and may only state numbers those tools return. **Ask AI** buttons on players, trades and matchups. See [docs/AI.md](docs/AI.md). |
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
`npm start` builds the web app and opens it in Electron. Release builds bundle the engine (`server/engine.py`, frozen with PyInstaller) and start it on `127.0.0.1` with the database in the user's app-data folder, so the app works with nothing else installed and signs its one local user in automatically.

- **Windows:** `npm run dist:win` → `desktop/release/Courtside-Setup-<version>.exe` (builds and self-tests the engine first).
- **macOS:** `npm run dist:mac` on a Mac → `Courtside-<version>-mac-arm64.zip`, ad-hoc signed. Without an Apple Developer ID, friends approve it once: the release notes walk through moving it to Applications and clearing the download flag (`xattr -cr /Applications/Courtside.app`) or using System Settings > Privacy & Security > Open Anyway.
- **Release notes:** `desktop/release-notes.md` becomes the release page. To fix the notes of a published release without rebuilding, run the **Update release notes** workflow from the Actions tab.
- **Releases:** bump the version in `desktop/package.json`, then push a matching tag like `v0.2.0`. `.github/workflows/release.yml` builds both, self-tests a real Windows install, and publishes a GitHub release with [these notes](desktop/release-notes.md) and the update feed (`latest.yml`, `latest-mac.yml`, `.blockmap` files).
- **Auto-update:** installed apps check the newest GitHub release shortly after launch, every 15 minutes, and when the computer wakes up or Courtside comes back to the front after 15+ minutes, so a laptop that mostly sleeps still checks (`electron-updater`). The update downloads in the background; people get an "Update now" prompt, or it installs the next time they close the app. On Windows electron-updater installs it. The Mac app is only ad-hoc signed, and electron-updater can only install Mac updates signed with an Apple Developer ID, so `desktop/mac-update.js` does it there: it downloads the zip itself (macOS only flags browser downloads, so no `xattr` step), checks it against the SHA-512 in `latest-mac.yml`, unzips it with `ditto`, and swaps the new app in after Courtside quits. If macOS refuses the swap, the old copy stays and shows the downloaded one in Finder to drag over. The release workflow runs that whole path on the macOS build (`desktop/scripts/test-mac-update.mjs`). The top bar shows the installed version and whether it's current; clicking it, or Help > Check for updates…, checks on demand. Logs: `updates.log` next to the engine log.

Without a bundled engine (plain `npm start` before `npm run build:engine`), the app connects to a Courtside server instead: `COURTSIDE_SERVER`, or `http://localhost:8000`.

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
| Last season repeated | 4.80 | 6.08 | 514 | 0.668 |
| Statistical baseline (SPS) | 4.56 | 5.83 | 469 | 0.704 |
| ESPN preseason projection | 4.57 | 5.79 | 507 | 0.736 |
| LightGBM alone | 4.41 | 5.60 | 456 | 0.729 |
| **Courtside ensemble** | **4.33** | **5.49** | **446** | **0.737** |

The ensemble is 5% more accurate than ESPN per game and 12% more accurate on season totals, and it beat ESPN in every one of these seasons; ranking accuracy is a statistical tie. A data audit excluded ESPN's stored projections for 2019‑20 and 2022‑23, because they were refreshed in-season, and removed ADP as a feature because ESPN overwrites it for past seasons. Both choices are documented in the methodology.

## Project layout

```
server/            FastAPI backend + analytics engine
  fbb/             engine (no web deps): projections, lineup optimiser, strength, trades, valuation
    matchup_sim.py Monte Carlo matchup simulator
    connectors/    espn, file import, flaim (MCP), demo
    ml/            feature builder + LightGBM wrapper
  app/             API: auth, leagues, invites, analysis, trades, realtime, scheduler, AI assistant
  engine.py        desktop engine entry point (PyInstaller: courtside-engine.spec)
  scripts/         build_history, fetch_game_logs, train_model, seed_dev
  data/history/    player-season dataset 2017-18 → today (from ESPN)
  data/model/      trained model + metrics.json (the backtest above)
  tests/           engine unit tests + end-to-end API test
web/               React + TypeScript + Vite client (also the desktop UI)
desktop/           Electron shell; starts the bundled engine; installers in desktop/release
cloudflare/ai-proxy/  Worker + D1 that holds the OpenAI key and meters AI calls for the desktop app
docs/              methodology, API, user guide, admin guide, connectors
```

## Tests

```bash
cd server && .venv/Scripts/python -m pytest      # 51 tests (the API test needs internet once; ESPN data is cached)
cd web && npm test && npm run typecheck           # 11 tests
cd cloudflare/ai-proxy && npm test                # 7 tests: the AI proxy against SQLite and a stubbed OpenAI
cd server && .venv/Scripts/python engine.py --self-test   # the desktop engine end to end
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
.venv/Scripts/python -m scripts.fetch_tracking    # NBA.com shots + tracking (home connection; NBA.com blocks cloud hosts)
.venv/Scripts/python -m scripts.backtest_xfp      # shot model, xFP weights and established rates -> xfp_model.joblib
.venv/Scripts/python -m scripts.xfp_report        # this season's luck board: who is running hot or cold
```
The server hot-reloads the model when the file changes. Set `FBB_RETRAIN_ENABLED=true` to retrain weekly.

## Documentation

- [Methodology & model card](docs/METHODOLOGY.md)
- [User guide](docs/USER_GUIDE.md)
- [Admin guide](docs/ADMIN_GUIDE.md)
- [API reference](docs/API.md) (interactive docs at `/docs` on a running server)
- [Connectors](docs/CONNECTORS.md)
- [AI assistant and the Cloudflare proxy](docs/AI.md)
