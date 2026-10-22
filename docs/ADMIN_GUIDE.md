# Admin guide

## Architecture

```
  Browser / PWA ─┐                   ┌─ ESPN fantasy API (league, players, schedule)
  Electron app ──┼─ HTTPS + WSS ─► FastAPI ─┼─ ESPN site API (injuries, news, bios)
                 │                   │      └─ Flaim MCP (optional)
                 │                   ├─ SQLite / Postgres (users, leagues, snapshots, history)
                 │                   ├─ Analysis cache (per league + snapshot + overrides)
                 │                   └─ Scheduler (refresh every N hours, optional weekly retrain)
```

- **One code base, three clients.** The React app in `web/` is the website, the PWA and, wrapped by Electron in `desktop/`, the desktop app.
- **Snapshots.** Every sync stores a compressed `LeagueSnapshot` (the last 20 are kept) plus a daily per-player projection row (`projection_history`). Together they form the time series used for trend lines and injury-change alerts.
- **Analysis.** Projections and rest-of-season lineup simulations take 1–2 s per league. They're cached in memory and rebuilt only when a new snapshot arrives or an override changes.

## Configuration

Environment variables (prefix `FBB_`), or a `server/.env` file. See `server/.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `FBB_DATABASE_URL` | `sqlite:///server/data/app.db` | Any SQLAlchemy URL, e.g. `postgresql+psycopg://user:pw@host/db` |
| `FBB_SECRET_KEY` | auto-generated into `data/secret.key` | Signs sessions and encrypts stored ESPN cookies. **Set it in production and keep it stable**; changing it signs everyone out and makes stored cookies unreadable |
| `FBB_PUBLIC_URL` | `http://localhost:5173` | Used to build invite links |
| `FBB_CORS_ORIGINS` | localhost + `app://fbb` | Comma-separated allowed browser origins. Keep `app://fbb` for the desktop app |
| `FBB_SYNC_INTERVAL_HOURS` | `1` | Default refresh cadence for new leagues; owners can change it per league. Open apps also refresh data older than 30 minutes |
| `FBB_SCHEDULER_ENABLED` | `true` | Background refresh loop |
| `FBB_MIN_MANUAL_SYNC_MINUTES` | `5` | Members' manual refresh cooldown |
| `FBB_RETRAIN_ENABLED` / `FBB_RETRAIN_INTERVAL_DAYS` | `false` / `7` | Periodic history refresh + retrain (hot-reloaded) |
| `FBB_FLAIM_MCP_URL` | `https://api.flaim.app/mcp` | Flaim endpoint |
| `FBB_WEB_DIST` | `../web/dist` | If present, the API also serves the built website (single origin) |

## Deploying

**Single container (recommended).** The Dockerfile builds the web app and serves it from the API on port 8000:
```bash
docker build -t courtside .
docker run -d -p 8000:8000 \
  -e FBB_SECRET_KEY="$(openssl rand -base64 48)" \
  -e FBB_PUBLIC_URL=https://courtside.example.com \
  -e FBB_CORS_ORIGINS=https://courtside.example.com,app://fbb \
  -v courtside-db:/app/server/data/db \
  courtside
```
Put it behind HTTPS (Caddy, nginx, or your host's TLS). WebSockets must be proxied (`Upgrade` headers).

**Postgres.** Set `FBB_DATABASE_URL` and add `psycopg[binary]` to `server/requirements.txt`. Tables are created on startup. For schema changes beyond additive ones, introduce Alembic.

**Desktop builds.**
- Windows: `npm run dist:win` (in `desktop/`).
  - On a machine without Developer Mode, electron-builder may fail to unpack its signing tools (a symlink privilege error). Enable Developer Mode, or pre-extract `winCodeSign` into `%LOCALAPPDATA%\electron-builder\Cache\winCodeSign\winCodeSign-2.6.0`.
  - Code signing needs a certificate (`CSC_LINK`, `CSC_KEY_PASSWORD`).
- macOS: `npm run dist:mac` on a Mac. Notarisation needs an Apple Developer ID (`APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`).
- Point users' desktop apps at your server: the sign-in screen has a server address field, or set `COURTSIDE_SERVER` at build time.

## Operations

- **Health:** `GET /api/health`.
- **Sync failures:**
  - visible to owners under League → Data and sync, and in the league feed;
  - expired ESPN cookies show as "ESPN refused access" or "non-JSON response": the owner pastes fresh cookies;
  - a crash mid-sync is reset to an error state on restart.
- **ESPN rate limits:** requests are spaced ≥0.35 s with retries; the scheduler runs at most 2 league syncs in parallel and checks every 10 minutes for due leagues.
- **Backups:** back up the database (SQLite: copy `app.db` while the app is idle, or use `sqlite3 .backup`) and `FBB_SECRET_KEY`. The model and history in `server/data/` can be regenerated from the scripts.
- **Retraining by hand:** `python -m scripts.build_history && python -m scripts.fetch_game_logs && python -m scripts.train_model`. Check the printed backtest; the new model is picked up automatically.

## Scaling

- The API is stateless except for the in-memory analysis cache and the WebSocket hub.
- To run several instances:
  1. Use Postgres.
  2. Replace `realtime.Hub.publish` with Redis pub/sub so every instance relays events to its own sockets.
  3. Run the scheduler in exactly one instance (`FBB_SCHEDULER_ENABLED=false` elsewhere).
- Each league's analysis takes ~50–100 MB in memory. `CACHE_SIZE` in `app/services/analysis.py` bounds it.

## Security notes

- Passwords: bcrypt (12 rounds) over a SHA-256 pre-hash, so long passphrases aren't truncated.
- Sessions: HS256 JWTs.
- ESPN cookies and Flaim tokens are Fernet-encrypted at rest and never returned by the API.
- League data is visible only to members. Non-members get `404`, so league IDs can't be probed.
- Invite codes are 8 characters from a 32-symbol alphabet (~10¹² combinations), expire (30 days by default), and can be revoked or use-limited.
- Uploads are capped at 2 MB and parsed as data only.
- The desktop app runs with context isolation, sandboxing and no Node integration. It serves only its bundled files and opens external links in the system browser.
