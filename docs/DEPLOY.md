# Deploying Courtside for free

**Recommended:** Google Cloud Run (API + website in one container) with Neon (Postgres). Cloud Run gives a full vCPU while it handles a request and scales to zero when idle. Its free allowance (180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million requests a month, request-based billing) is far more than one league uses. Neon's free Postgres (0.5 GB, compute suspends after 5 idle minutes) holds roughly 30 leagues at ~15 MB each per season. Google needs a card on the billing account; set a budget alert and usage should stay at $0.

**Why not Cloudflare?** Python Workers can't load SciPy/LightGBM within the free 128 MB limit, and Cloudflare Containers require the $5/month Workers Paid plan. **Hugging Face Spaces** now require a paid plan to create Docker Spaces. **Vercel** (where FinLit-Pro is hosted) runs serverless functions with size limits too small for this stack and no WebSockets.

## 1. Database (Neon)
1. Create a free project at neon.tech and copy the connection string (`postgresql://…?sslmode=require`).
2. Courtside creates its tables on first start; no migration step.

## 2. App (Cloud Run, from GitHub, no CLI needed)
1. In the Google Cloud console create a project, enable billing, and add a **budget alert** (e.g. $1).
2. Cloud Run → **Deploy container → Service → Continuously deploy from a repository** → connect GitHub → `KrishModi6/fantasybasketball`, branch `main`, build type **Dockerfile** (`/Dockerfile`).
3. Settings: region `us-central1`; **Allow unauthenticated invocations**; billing **request-based**; min instances **0**, max instances **1**; memory **1 GiB**, CPU **1**; request timeout **300 s**.
4. Environment variables:

| Variable | Value |
|---|---|
| `FBB_DATABASE_URL` | Neon connection string |
| `FBB_SECRET_KEY` | a long random string (keep it stable) |
| `FBB_CRON_TOKEN` | another long random string |
| `FBB_PUBLIC_URL` | the service URL Cloud Run shows after the first deploy (used in invite links) |
| `FBB_SCHEDULER_ENABLED` | `false` |
| `FBB_INLINE_SYNC` | `true` |
| `FBB_REALTIME_ENABLED` | `false` (an open WebSocket keeps an instance billed; the app polls every 60 s instead) |

5. In Artifact Registry, add a cleanup policy that keeps only the 2 most recent images so stored images stay small.

## 3. Scheduled refreshes
Add two repository secrets in GitHub (Settings → Secrets and variables → Actions): `COURTSIDE_URL` (the service URL) and `COURTSIDE_CRON_TOKEN` (same as `FBB_CRON_TOKEN`). `.github/workflows/sync.yml` then calls `POST /api/cron/sync` every 3 hours (about 240 of the 2,000 free Actions minutes a month for a private repo). Cloud Scheduler can call the same endpoint instead.

## 4. Use it
- Website and phones: open the service URL; invite links point there.
- Desktop app: on the sign-in screen, choose **Use a different server** and enter the service URL.

## No-card alternative: Render + Neon
Click **New → Blueprint** on render.com and select this repo; `render.yaml` configures a free Docker web service (fill in `FBB_DATABASE_URL` and `FBB_PUBLIC_URL`, then copy the generated `FBB_CRON_TOKEN` into the GitHub secret). Trade-offs: the free instance is much slower (a fraction of a CPU), sleeps after 15 idle minutes (about a minute to wake), and its free Postgres expires after 30 days, which is why Neon is used for data.

Sources: [Render free tier](https://render.com/docs/free), [Koyeb pricing](https://www.koyeb.com/pricing), [Hugging Face Spaces](https://huggingface.co/docs/hub/spaces-overview), [Cloud Run pricing](https://cloud.google.com/run/pricing), [Neon pricing review](https://www.jetadmin.io/blog/neon-pricing/), [Cloudflare plans](https://www.cloudflare.com/plans.md).
