# Courtside on AWS

Status (2026-10-31): **built**: the S3 bucket and uploader, the daily tracking feed behind xFP (S3 +
CloudFront, Courtside 0.5.0), a cost budget, and Lambda sims: the simulator backtest and its daily
check on this season (section 4). Bedrock was evaluated and not adopted. Everything else here is a
record of the decision.

The repo is public, so the project's account ID is written `<account-id>` here. The uploader looks
it up at runtime.

## Where things stand

- AWS project `<account-id>`, CLI profile `courtside`, selected Region `us-east-2`.
- Free plan: $100 in credits, plan ends 2027-04-30. On the free plan the project closes on its own when
  the credits run out or on that date, whichever comes first, and its data is deleted 90 days later
  unless you upgrade to the paid plan. 2027-04-30 is mid fantasy season, so nothing friends rely on
  during the season should depend on this project alone. The home machine and git stay the source of
  truth for everything below.

## 1. S3 bucket for tracking data and models (built)

One private bucket: `courtside-<account-id>-us-east-2-an`. It's in the account-regional namespace,
which reserves that name for this project and Region, so nobody else can take it.

Contents mirror `server/data/`:

| Local file | S3 key |
|---|---|
| `server/data/cache/nba/shots_<season>.json.gz` | `cache/nba/shots_<season>.json.gz` |
| `server/data/cache/nba/dash_<season>_<tag>.json` | `cache/nba/dash_<season>_<tag>.json` |
| `server/data/model/xfp_model.joblib`, `xfp_metrics.json` | `model/xfp_model.joblib`, `model/xfp_metrics.json` |

Left out: `live_<season>.json`, a six-hour cache the app rebuilds on every sync (mirroring it would
only pile up versions), and `history/nba_ids.json`, which is already in git.

Settings, in the order they're applied:

1. **Create** the bucket in `us-east-2` in the account-regional namespace.
2. **Block Public Access** stays on and **ACLs** stay disabled. Both are S3 defaults for new buckets;
   I check them and change nothing.
3. **Versioning** is on, so a bad fetch can't silently overwrite a good season file. Old versions can
   be restored.
4. **Lifecycle rule**: noncurrent versions are deleted after 30 days (the newest 3 are kept) and
   unfinished multipart uploads are aborted after 7 days. This caps what versioning can cost.
5. **Default encryption**: SSE-S3 with S3 Bucket Keys, with SSE-C blocked. There's no KMS key. The
   data is public NBA stats, and a customer-managed key would add $1/month plus key-policy work for no
   real gain.
6. **Bucket policy** denies every request that doesn't use HTTPS.
7. **ABAC** is on (tag-based access later; free). The bucket is tagged `project=courtside`.
8. **Access logging**: option A below with no KMS key, and no S3 Tables copy.

**Uploader**: `server/scripts/push_tracking_s3.py`, run at home right after `fetch_tracking.py`.
stats.nba.com blocks cloud IPs, so ingest stays on the home machine. Run by hand, the uploader uses
the `courtside` profile from `aws login`: short-lived credentials, with no access keys on disk (the
daily task in 1b has its own key, which can only write the public file). It
compares each file's SHA-256 with the copy in S3 and uploads only the files that changed, so new
versions appear only when the data really changed.

**Monthly cost**

| Item | Estimate |
|---|---|
| Storage: ~21 MB now, ~0.1 GB with 30 days of old versions in season, at $0.023/GB | $0.003 |
| Requests: ~35 HEADs plus a few PUTs per run, run daily | < $0.01 |
| Data transfer in | free |
| **Total** | **≈ $0.01** |

**Access logging options**

| Option | Cost here | Notes |
|---|---|---|
| A. Server access logs to CloudWatch Logs, 90-day retention (what the AWS S3 security skill recommends) | ≈ $0.00 | Searchable in Logs Insights. The skill also wants a KMS key on the log group, which adds $1.00/month. |
| B. Server access logs to a second S3 bucket | ≈ $0.00 | One more bucket to manage; raw log files. |
| C. CloudTrail data events | ≈ $0.00 | Needs a trail plus its own bucket. Best "who did what" detail. |
| None | $0 | CloudTrail event history still records bucket setting changes for 90 days at no cost. |

**Cost budget** (created 2026-10-30): `courtside-monthly`, $5 a month of real usage (credits and
refunds excluded, so it doesn't sit at $0), emailing the project owner when actual spend passes 50%
and 100% and when the month is forecast to pass 100%. Budgets that only alert are free. Created
through the API, so it may not have counted toward the "set up a budget" $20 credit activity.

**Running it**

```bash
cd server
pip install -r requirements-dev.txt          # once: boto3[crt]
aws login --profile courtside                # when the 12-hour session has lapsed
python -m scripts.fetch_tracking             # home connection only
python -m scripts.push_tracking_s3           # add --dry-run to preview
python -m scripts.push_tracking_s3 --live    # the daily feed (1b); the scheduled task runs this
```

First upload (2026-10-29): 34 files, 21.6 MB. A second run uploaded nothing.

## 1b. Daily tracking feed for xFP (built, Courtside 0.5.0)

xFP needs this season's NBA.com tracking (every shot, plus the passing, rebounding and hustle
dashboards). Before 0.5.0 every app fetched it from stats.nba.com on its own syncs, and hosts NBA.com
won't answer (the Render server, GitHub Actions) got no xFP. Now one machine fetches it once a day and
everyone reads that copy:

```
home PC, daily task ──► NBA.com ──► s3://<bucket>/public/live_<season>.json.gz ──► CloudFront ──► every app
```

- **Publisher**: `python -m scripts.push_tracking_s3 --live` brings `live_<season>.json` up to date
  from NBA.com (only the days it lacks) and uploads it gzipped to `public/` with
  `Cache-Control: max-age=600`. It refuses to publish anything older than yesterday (US Eastern).
  A scheduled task on the home PC runs it every morning (below).
- **CloudFront** distribution `https://d3n5zp4ghqw542.cloudfront.net` serves only the bucket's
  `public/` folder: an origin access control signs CloudFront's requests, and the bucket policy lets
  that one distribution (`AWS:SourceArn` plus `AWS:SourceAccount`) `GetObject` on `public/*`. Block
  Public Access stays on and the bucket isn't public; a direct S3 request or any path outside
  `public/` gets 403. HTTP redirects to HTTPS; the managed CachingOptimized and SecurityHeaders
  policies; price class 100 (North America and Europe edges).
- **Apps** (`fbb.xfp.refresh_live`, `FBB_TRACKING_URL` to change or `""` to turn it off) download the
  file before asking NBA.com. Its ETag makes the later checks 304s with no body. When the file covers
  yesterday NBA.com isn't asked at all; when it's a day behind, the app fetches only the missing day
  from NBA.com; when neither answers, it uses the newest copy it has.
- The file is public to anyone with the URL. It holds NBA.com box-score and tracking numbers only:
  no league, roster or personal data.

**Cost**: CloudFront's always-free tier is 1 TB and 10M requests a month. The file is about 0.2 MB now
and maybe 3 MB by April; a dozen apps checking every few hours is well under 1% of the free tier.
Access logs go to CloudWatch Logs in us-east-1 (CloudFront is global; its logs can't go to us-east-2),
kept 90 days, a few cents at most.

**Not added**: an AWS WAF web ACL with a rate limit. It costs about $6 a month (more than the whole
budget) to protect a cached static file; the budget email is the tripwire instead. CloudFront's
flat-rate Free plan bundles WAF at $0 if you want it later (console only).

**The daily task** (installed 2026-10-30 on the home PC), all in `C:\Users\Krish\.courtside-publisher`:

| Piece | Where |
|---|---|
| Scheduled task | `Courtside tracking publisher`, daily 08:00, runs late if the PC was off (StartWhenAvailable), only while you're signed in to Windows. It starts `publish-tracking.pyw` with the main checkout's `server\.venv\Scripts\pythonw.exe`, so no console window opens |
| Wrapper | `publish-tracking.cmd`: updates the checkout to `origin/main`, then runs the publisher with the `courtside-publisher` profile (below). Run it by hand to publish now |
| Checkout | `publisher\`, a detached `git worktree` of this repo pinned to `origin/main` |
| Python | the main checkout's `server\.venv` (has `requirements-dev.txt`) |
| Cache | `publisher-cache\` (`FBB_CACHE_DIR`) |
| Log | `publish.log` |
| Key rotation reminder | Scheduled task `Courtside publisher key rotation reminder`: once, 2027-01-29 09:00 (late if the PC was off), deletes itself after 2027-04-30 |

Not under `%LOCALAPPDATA%`: the Claude desktop app is a packaged app, so files its sessions create
there are redirected into its private storage, where Task Scheduler can't see them.

**Credentials** (since 2026-10-31). The task first used the `aws login` session, but AWS caps that at
12 hours, so every run more than 12 hours after a sign-in failed (first on 2026-10-31). The wrapper
now sets `AWS_PROFILE=courtside-publisher`, the access key of an IAM user that exists only for this
task:

- IAM user `courtside-publisher`, tagged `project=courtside`, with no console password. Its one inline
  policy, `publish-live-tracking`, allows `s3:PutObject` on
  `arn:aws:s3:::courtside-<account-id>-us-east-2-an/public/live_*.json.gz` and nothing else, so it
  can't list or read the bucket or write anywhere else. `sts:GetCallerIdentity`, which finds the
  bucket name, needs no permission.
- The key is in `%USERPROFILE%\.aws\credentials` under `[courtside-publisher]`, and its region is in
  `.aws\config`. Anything running as you can read it. If it leaked, the worst case is a wrong
  `live_<season>.json.gz` until the next daily run replaces it, plus a few cents of storage.
- Not available on this free-plan project without activating advanced features: IAM Identity Center
  (unsupported in the new AWS experience) and IAM Roles Anywhere (short-lived credentials from a
  certificate, AWS's preferred pattern for machines outside AWS; the free plan's SCP doesn't allow
  `rolesanywhere`).
- **Rotate the key every 90 days.** Next: 2027-01-29, when the one-time task `Courtside publisher key
  rotation reminder` pops up a message. Signed in as `courtside`, run `aws iam create-access-key
  --user-name courtside-publisher --profile courtside`, put the new ID and secret in the credentials
  file, run `publish-tracking.cmd` and check that `publish.log` ends in `exit 0`, then
  `aws iam delete-access-key --user-name courtside-publisher --access-key-id <old ID> --profile
  courtside`. A user can hold two keys, so the old one keeps working until the new one is tested.

If the log says the profile has no working access key, the key was deleted, deactivated or mistyped:
make a new one as above. If it stops, apps notice nothing for a day and then fetch from NBA.com
themselves, as before 0.5.0. To stop it: `schtasks /Delete /TN "Courtside tracking publisher" /F`
and `git worktree remove C:\Users\Krish\.courtside-publisher\publisher`; then retire the key with
`aws iam delete-access-key`, `aws iam delete-user-policy --policy-name publish-live-tracking` and
`aws iam delete-user` (each with `--user-name courtside-publisher --profile courtside`), and delete
the reminder task.

**What exists in AWS** (tagged `project=courtside`; Regional resources in `us-east-2`)

| Resource | Name |
|---|---|
| S3 bucket | `courtside-<account-id>-us-east-2-an` |
| CloudWatch log group | `/aws/vendedlogs/s3/courtside-<account-id>-us-east-2-an/S3_SERVER_ACCESS_LOGS` (90-day retention) |
| Log delivery source / destination / delivery | `s3-sal-courtside` → `sal-dest-courtside` |
| CloudFront distribution (global) | `EA87DV0N45QSR`, `d3n5zp4ghqw542.cloudfront.net` |
| CloudFront origin access control | `courtside-public` |
| Bucket policy statement | `AllowCloudFrontReadPublic` (next to `DenyInsecureTransport`) |
| CloudWatch log group (us-east-1) | `/aws/vendedlogs/cloudfront/courtside-public` (90-day retention) |
| Log delivery source / destination / delivery (us-east-1) | `cf-courtside-public` → `cf-dest-courtside` |
| Budget (global) | `courtside-monthly` |
| IAM user (global) | `courtside-publisher`: one access key, inline policy `publish-live-tracking` (1b) |

To remove it all: delete the scheduled tasks and the `courtside-publisher` user (1b) first. Then CloudFront: delete the `cf-` delivery,
destination, source and log group in us-east-1, disable the distribution, wait for it to deploy,
delete it, then delete the origin access control and the `AllowCloudFrontReadPublic` statement. Then
the S3 logging: delete the delivery, then the delivery destination, the delivery source and the log
group. Then empty the bucket, including every object version (versioning keeps them), and delete it.
The budget can go any time. Apps on 0.5.0 keep working without the feed; they fall back to NBA.com.

## 2. Amazon Bedrock for the AI analyst (evaluated; staying on OpenAI)

**Today**: desktop app → Cloudflare Worker `courtside-ai` → OpenAI Responses API with GPT-6 Luna, low
reasoning effort, at most 3,000 output tokens, and `previous_response_id` for follow-up questions.
The Worker's D1 counters show no calls so far today, so the figures below use an assumed usage level.

**Tokens per question (estimate)**: every model call carries about 2,300 tokens of instructions and
13 tool schemas. A typical question makes 3 calls, and each later call carries the earlier tool
results. That comes to about 20,000 input and 1,200 output tokens per question. Prompt caching would
cut the input cost, but these figures leave it out to stay conservative.

| Model and route | $/M input | $/M output | Per question | 720 questions/month* | Worst day at the Worker's 6,000-call cap |
|---|---|---|---|---|---|
| GPT-6 Luna, OpenAI direct (**today**) | 0.10 | 0.50 | $0.0026 | $1.90 | ~$5 |
| GPT-6 Luna on Bedrock (`us.` profile) | 0.11 | 0.55 | $0.0029 | $2.10 | ~$6 |
| Claude Haiku 5.5 on Bedrock (`us.` profile) | 0.11 | 0.55 | $0.0029 | $2.10 | ~$6 |
| Claude Sonnet 5.5 on Bedrock (`us.` profile) | 2.20 | 11.00 | $0.057 | $41 | ~$115 |

\* 12 people asking 2 questions a day for 30 days. Bedrock prices come from the AWS Pricing API for
`us-east-2` on 2026-10-29. OpenAI's come from third-party price listings, because OpenAI's own page
couldn't be read. Claude counts tokens differently, so its per-question figures could be about 20%
off either way.

**Region note**: in `us-east-2`, Bedrock serves these models only through cross-Region inference
profiles. The project's managed SCPs allow the `us.` profiles (us-east-1, us-east-2, us-west-2); a
`global.` profile would be denied.

**Latency**: not measured. A question is 2 to 4 model calls in a row, so the wait comes mostly from
how fast the model writes, times the number of calls; the network matters much less. GPT-6 Luna on
Bedrock is the same model, so expect about the same speed. A five-question benchmark of each route
would cost about a cent if you want real numbers.

**What a switch would take**

- **A. Same model on Bedrock.** In `cloudflare/ai-proxy/src/worker.mjs`, point the upstream at
  `https://bedrock-runtime.us-east-2.amazonaws.com/openai/v1/responses`, change the model to
  `us.openai.gpt-6-luna`, and authenticate with a long-term Bedrock API key (an IAM user credential)
  stored as a Worker secret. Request bodies, tools and `previous_response_id` all stay as they are.
  About 1 to 2 hours, tests included.
- **B. Claude Haiku 5.5.** Use the Converse or Messages API. Neither has `previous_response_id`, so
  the Worker or server would have to keep each conversation's history. Tool schemas and response
  parsing would be rewritten in `server/app/services/ai.py` and `worker.mjs`, and answers would need
  a quality check against Luna. About a day.

**Decision (2026-10-29): keep OpenAI for now.** It costs the same or less, and it keeps long-lived AWS keys
out of Cloudflare. It also means the project closing (credits used up, or 2027-04-30) can't take the
analyst down in the middle of the season. For scale, one heavy day at the Worker's cap on Sonnet 5.5
would use the whole $100. If you upgrade to the paid plan, route A becomes an afternoon's work. Before
trying Claude, run about 20 saved questions through both models and compare the answers.

## 3. Overkill: skip these

| Idea | Why skip it | Rough cost (us-east-2 list price) |
|---|---|---|
| SageMaker training job for xFP | `backtest_xfp.py` trains locally in minutes. A job needs a container, an IAM role and S3 staging, so it's only worth doing to learn SageMaker. | cents per run (ml.m5.large ≈ $0.12/h) |
| SageMaker endpoint for xFP | The model is a 1 MB joblib file loaded inside the server. | always on, ≈ $40+/month even at the smallest size |
| RDS / Aurora | The app uses SQLite at home and Neon when hosted. | db.t4g.micro ≈ $14/month if left running; delete it right after your $20 activity |
| NAT gateway / VPC | Nothing here runs in a VPC. | ≈ $33/month plus data |
| Lambda or EC2 to fetch NBA data | stats.nba.com doesn't answer cloud IPs. | — |
| KMS customer-managed key on the bucket | The data is public stats, and SSE-S3 is enough. | $1/month plus requests |
| Athena / S3 Tables over the shot files | Seven gzipped files that pandas reads in seconds. | per query |
| AWS WAF on the CloudFront distribution | It would rate-limit one cached static file; the budget alert covers runaway use. | ≈ $6/month |

## 4. Lambda sims: the simulator backtest and the daily check (built 2026-10-30/31)

Results: docs/METHODOLOGY.md, "Calibration against real seasons".

`server/scripts/backtest_sims.py` checks the matchup simulator and the season simulation against real
seasons: synthetic 10-team ESPN points leagues on 2023-24 to 2025-26 game logs, with the app's own code
forecasting every week and the real box scores deciding it. One league-season with 17 settings takes
about 6 CPU-minutes, and the study ran 192 of them, so it fans out to Lambda.

**Why Lambda.** Each league is an independent, CPU-only job under 15 minutes that needs only numpy and
pydantic. It runs from a zip with no container, cluster or queue to manage, and you pay only while it
runs. The free plan allows 3,008 MB per function (about 1.7 vCPUs, so two processes per invocation) and
10 at once: about 17 vCPUs, roughly this PC. EC2 on the free plan is limited to 2-vCPU instances,
and AWS Batch or Fargate would add a container image for no gain.

**How it runs.**
1. At home: `python -m scripts.backtest_sims prepare` builds each season's projections and real
   outcomes (`data/cache/sims/inputs_<season>.json.gz`, about 1 MB each; NBA data can't be fetched
   from the cloud).
2. Upload the inputs to `sims/inputs/` and the package from `python -m scripts.sims_lambda <zip>` to
   `sims/code/`, then point the function at it (`aws lambda update-function-code`).
3. Send one asynchronous invocation per league, `{"run": "r1", "tasks": [[season, league, 0],
   [season, league, 1]]}`. Lambda queues what it can't run at once. Each writes
   `sims/results/<run>/<season>_<league>_g<group>.json.gz` and skips leagues already there, so
   sending again is safe.
4. `{"run": "r1", "action": "merge"}` writes `sims/merged/<run>.json.gz`. Download it and run
   `python -m scripts.backtest_sims report <file>`.

**The daily check** (`server/scripts/daily_sims.py`). Every morning the scheduled task in 1b runs it
right after the tracking feed. It checks the app as shipped against this season's finished matchup weeks:
1. At home it rebuilds this season's projections week by week from ESPN's games so far, the same way
   as for past seasons, and uploads them to `sims/inputs/inputs_<season>.json.gz`.
2. It sends 16 invocations of 2 leagues each (32 synthetic leagues, settings `"app"`) and waits for
   their results under `sims/results/daily-<season>-<day>/`.
3. `{"action": "report"}` scores them and writes `sims/daily/<season>/<day>.json` and `latest.json`:
   Brier score, log loss and calibration of the weekly win odds, how often the 80% ranges held, and
   the bias of projected scores, overall and week by week.
4. `publish.log` gets one line with the headline numbers.

Before the first week ends there is nothing to score, so it stops after step 1. The numbers change
only when a week ends; on the other days it repeats the same weeks. Playoff odds aren't checked
until the season is over. The task runs it only once `scripts/daily_sims.py` is on `main`.

It signs in as the IAM user `courtside-publisher` (1b). Besides `publish-live-tracking`, that user has a
second inline policy, `run-daily-sims`, added 2026-10-31. It allows three things: upload
`sims/inputs/inputs_*.json.gz`, list `sims/results/daily-*`, and invoke `courtside-sims`. So the
worst case of a leaked key in 1b is no longer a few cents: anyone holding it could run the function
on your bill, at most 10 at once (about $1.40 an hour, $34 a day). The $5 budget email would show it,
and deleting the key stops it. Retiring the user (1b) needs this policy deleted first.

**What exists in AWS** (tagged `project=courtside`, `us-east-2`)

| Resource | Name |
|---|---|
| Lambda function | `courtside-sims` (Python 3.12, arm64, 3,008 MB, 15-minute timeout, async retries off) |
| IAM role | `courtside-sims-lambda`: Lambda may assume it for this function only; inline policy `sims-s3-and-logs` reads `sims/inputs/*`, writes `sims/results/*`, `sims/merged/*` and `sims/daily/*`, writes its own logs |
| IAM user policy | `run-daily-sims` on `courtside-publisher` (above) |
| CloudWatch log group | `/aws/lambda/courtside-sims` (14-day retention) |
| S3 objects | `sims/` in the bucket above: inputs, code, results, merged, daily (about 40 MB, plus about 1 MB a day in season) |

**Cost.** Lambda bills $0.0000133334 per GB-second on arm64. A study league (both setting groups,
about 218 s at 3,008 MB) costs about $0.0085. Everything through 2026-10-31 took 212 invocations and
129,000 GB-seconds: $1.72 at list price. That covers the canary, the pilots, the 192-league study run
`r1`, its merge and the first daily run. Lambda's always-free allowance is 400,000 GB-seconds a month,
so the bill may show less, if this plan gets that allowance. The daily check runs one setting with no
playoff odds: the first one (week 1, 32 leagues) took about 40 s end to end. Expect under $1 a month by
the end of the season. S3 storage and requests add well under a cent.

To remove it all: first take the daily-sims lines out of `publish-tracking.cmd` and delete the user
policy (`aws iam delete-user-policy --user-name courtside-publisher --policy-name run-daily-sims`).
Then `aws lambda delete-function --function-name courtside-sims`, then
`aws logs delete-log-group --log-group-name /aws/lambda/courtside-sims`, then delete the role's
inline policy and the role (`aws iam delete-role-policy --role-name courtside-sims-lambda
--policy-name sims-s3-and-logs`, `aws iam delete-role --role-name courtside-sims-lambda`), then
`aws s3 rm s3://courtside-<account-id>-us-east-2-an/sims/ --recursive`. Nothing else uses them.

## Hosted Courtside

The Render-hosted server can't reach stats.nba.com, so before 0.5.0 hosted Courtside had no xFP. It
now reads the CloudFront file like the desktop app does, with no AWS credential involved.
