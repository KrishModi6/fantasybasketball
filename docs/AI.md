# AI assistant

The **Assistant** tab answers plain-English questions about a league: who to pick up, how to win this week's matchup, which trades to pitch, who's hurt and what it costs. It is an OpenAI Responses-API agent (`gpt-6-luna`, low reasoning effort) that can only state numbers it got from Courtside's own engine.

## What the model can call

The agent runs the same code as the rest of the app (`server/app/services/ai_tools.py`). Each tool returns compact JSON.

| Tool | What it runs |
|---|---|
| `league_overview` | Power rankings, records, projected points per week, playoff and title odds (5,000 simulated seasons) |
| `team_roster` | A roster with projections, weak spots ranked by points at stake, and upcoming games |
| `free_agents` | Free agents sorted by rest-of-season value, games in the next 7 days, or points per game |
| `waiver_recommendations` | Add/drop moves simulated against the team's real daily lineups, plus this week's streams |
| `player_details` | One player's projection sources and weights, adjustments, ML factors, recent games and news |
| `evaluate_trade` | The trade analyzer: both rosters re-simulated, playoff odds before and after, fairness |
| `trade_ideas` | The trade finder for a team, optionally against one partner |
| `simulate_matchup` | The Monte Carlo matchup simulator (6,000 runs), with players benched if asked |
| `league_news` | Recent headlines and injury changes (context only, never a reason to override the numbers) |

Quick questions on the page (waivers, matchup plan, trade ideas, injury check, season outlook, power-rankings blurbs) are fixed prompts. The player drawer, the trade analyzer and the matchup page each have an **Ask AI** button that opens the assistant with a question about what's on screen. Follow-ups continue the conversation through `previous_response_id`.

Only the question and the tool results the model asks for are sent to OpenAI. ESPN cookies, accounts and the rest of the database never leave the server.

## Where the OpenAI key lives

The server picks a transport from its settings (`server/app/services/ai.py`):

| Mode | Settings | Used by |
|---|---|---|
| `direct` | The user's own key, saved under **Assistant > Your OpenAI key** | Anyone who wants to use their own OpenAI account; it's checked against OpenAI, stored encrypted, and used only for that user's questions |
| `direct` | `FBB_OPENAI_API_KEY` | A hosted server that holds its own key |
| `proxy` | `FBB_AI_PROXY_URL` + `FBB_AI_PROXY_TOKEN` | The desktop app: the key stays in Cloudflare |
| `off` | neither | The Assistant tab explains how to turn it on |

The desktop releases use the proxy, so friends who install the app never see or need a key.

### The Cloudflare proxy (`cloudflare/ai-proxy`)

A Worker plus a D1 database, built like the quizpilot-cache service:

- `POST /v1/responses` checks the app's client token (`Authorization: Bearer`, compared in constant time), refuses browser requests (any `Origin` header), validates the body down to the exact shapes the agent sends, reserves a slot, then forwards to OpenAI with the server-side key.
- Admission and the counters run in one D1 batch, so they hold across Worker instances: concurrent calls, calls per minute, calls per day for everyone, and calls per day per device. Over-limit requests get a 429 with a plain message and `Retry-After`.
- `GET /health` reports whether the key and token are configured. An hourly cron clears expired counters.

The limits count model calls, not questions; one question usually takes 2 to 4 calls (at most 7). Defaults are in `wrangler.toml`:

| Variable | Default | Meaning |
|---|---|---|
| `DEVICE_DAILY_LIMIT` | 450 | Calls per computer per UTC day (about 110-220 questions) |
| `DAILY_LIMIT` | 6000 | Calls per UTC day across everyone |
| `PER_MINUTE_LIMIT` | 120 | Calls per minute across everyone |
| `MAX_CONCURRENCY` | 12 | Calls in flight at once |
| `MAX_OUTPUT_TOKENS` | 3000 | Cap per model call |

The client token ships inside every release, so treat it as an app identifier rather than a secret: anyone who unpacks the app can read it. The limits above are what bound your OpenAI spend. To cut off old builds, change `CLIENT_TOKEN` and publish a new release.

## Set up

The short way, from the repository root (needs `gh` signed in):

```bash
node cloudflare/ai-proxy/setup.mjs
```

It signs Wrangler in, deploys the Worker, creates a client token and stores it in both the Worker and the GitHub secrets below, and offers to pass your OpenAI key straight to Wrangler's prompt. Then publish a build that includes the assistant with `gh workflow run release.yml -f publish=true`. The steps it automates:

### 1. Deploy the Worker (once)

```bash
cd cloudflare/ai-proxy
npx wrangler@4 login                       # approve in the browser
npx wrangler@4 d1 execute courtside-ai --remote --file schema.sql   # already applied to the live database
npx wrangler@4 deploy
node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"   # a new client token
npx wrangler@4 secret put CLIENT_TOKEN     # paste the token
```

### 2. Add your OpenAI key

Cloudflare dashboard > **Workers & Pages** > **courtside-ai** > **Settings** > **Variables and Secrets** > **Add** > type **Secret**, name `OPENAI_API_KEY`, paste the key, **Deploy**. Or `npx wrangler@4 secret put OPENAI_API_KEY`.

Check it: `https://courtside-ai.<your-subdomain>.workers.dev/health` should say `"ready": true`.

### 3. Let release builds use it

Add two repository secrets (GitHub > Settings > Secrets and variables > Actions), or with the CLI:

```bash
gh secret set COURTSIDE_AI_URL --body "https://courtside-ai.<your-subdomain>.workers.dev"
gh secret set COURTSIDE_AI_TOKEN          # paste the same client token
```

The release workflow writes them into `desktop/ai-config.json` (never committed) and the app passes them to its engine.

### Hosted server instead

Set `FBB_OPENAI_API_KEY` on the server, or point `FBB_AI_PROXY_URL` and `FBB_AI_PROXY_TOKEN` at the Worker. `FBB_AI_USER_HOURLY_LIMIT` (default 100) caps questions per user per hour on top of the proxy's limits (users with their own OpenAI key aren't capped); `FBB_AI_MAX_ROUNDS` (default 6) caps tool rounds per question.

## Tests

```bash
cd server && .venv/Scripts/python -m pytest tests/test_ai.py   # tools on a real analysis, agent loop with a scripted model
cd cloudflare/ai-proxy && npm test                              # Worker against node:sqlite and a stubbed OpenAI
cd web && npm test                                              # the markdown renderer never emits HTML
```
