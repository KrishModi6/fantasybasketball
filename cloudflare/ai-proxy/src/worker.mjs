// Courtside AI proxy: holds the OpenAI key and meters model calls for the desktop app.
//
// The desktop app runs the projection engine and the assistant's tools locally; only the
// model call comes here. The client token is baked into release builds, so it identifies
// the app, not a person. The per-device, per-minute, per-day and concurrency limits in D1
// are what actually cap spend.
//
// It also carries a league admin's trade rulings between desktop apps, since each app has its own database.
// Any copy can read a league's rulings. Writing takes the key only the admin's copy holds: the first copy to
// publish for a league claims it with that key's hash, and every later write has to match.

const OPENAI_URL = 'https://api.openai.com/v1/responses';
const JOB_TTL_MS = 120_000;
const UPSTREAM_TIMEOUT_MS = 90_000;
const MAX_BODY_BYTES = 400_000;
const DEVICE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const RESPONSE_ID = /^resp_[A-Za-z0-9_-]{1,200}$/;
const TOOL_NAME = /^[a-zA-Z0-9_-]{1,64}$/;
const RULINGS_ROUTE = /^\/v1\/leagues\/([0-9a-f]{32,64})\/rulings(?:\/([0-9a-f]{32}))?$/;
const MAX_RULING_BYTES = 16_000;
const MAX_LEAGUE_RULINGS = 500;
const securityHeaders = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' };

export class HttpError extends Error {
  constructor(status, message, retryAfter) { super(message); this.status = status; this.retryAfter = retryAfter; }
}

const limit = (value, fallback) => /^\d+$/.test(String(value)) && Number(value) > 0 ? Number(value) : fallback;
export function limits(env) {
  return {
    concurrency: limit(env.MAX_CONCURRENCY, 12),
    perMinute: limit(env.PER_MINUTE_LIMIT, 120),
    perDay: limit(env.DAILY_LIMIT, 6000),
    perDeviceDay: limit(env.DEVICE_DAILY_LIMIT, 450),
    maxOutputTokens: limit(env.MAX_OUTPUT_TOKENS, 3000),
  };
}

// The budgets roll over at midnight UTC, but people read the limit messages in Eastern.
export const inEastern = ms =>
  `${new Date(ms).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' })} Eastern`;

async function matchesToken(header, expected) {
  if (!header?.startsWith('Bearer ') || header.length > 1024) return false;
  const hash = value => crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  const [a, b] = await Promise.all([hash(header.slice(7)), hash(expected)]);
  const left = new Uint8Array(a), right = new Uint8Array(b);
  let difference = 0;
  for (let i = 0; i < left.length; i++) difference |= left[i] ^ right[i];
  return difference === 0;
}

async function readJSON(request, maxBytes) {
  if (!request.headers.get('content-type')?.startsWith('application/json')) throw new HttpError(415, 'JSON required.');
  if (Number(request.headers.get('content-length')) > maxBytes) throw new HttpError(413, 'Request too large.');
  const text = await request.text();
  if (new TextEncoder().encode(text).length > maxBytes) throw new HttpError(413, 'Request too large.');
  try { return JSON.parse(text); } catch { throw new HttpError(400, 'Invalid JSON.'); }
}

const isString = (v, max) => typeof v === 'string' && v.length <= max;

// Only the shapes the Courtside agent sends: a user prompt, or tool results for the previous turn.
export function validateRequest(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new HttpError(400, 'Invalid request.');
  const { input, instructions, tools, previous_response_id: previous } = body;
  if (typeof input === 'string') {
    if (!input.trim() || input.length > 8000) throw new HttpError(400, 'The question must be 1 to 8,000 characters.');
  } else if (Array.isArray(input)) {
    if (!input.length || input.length > 24) throw new HttpError(400, 'Invalid input items.');
    for (const item of input) {
      const ok = item?.type === 'function_call_output'
        ? isString(item.call_id, 200) && isString(item.output, 20000)
        : item?.role === 'user' && isString(item.content, 8000);
      if (!ok) throw new HttpError(400, 'Invalid input items.');
    }
  } else throw new HttpError(400, 'Invalid input.');
  if (!isString(instructions, 16000)) throw new HttpError(400, 'Invalid instructions.');
  if (!Array.isArray(tools) || tools.length > 20) throw new HttpError(400, 'Invalid tools.');
  for (const tool of tools) {
    if (tool?.type !== 'function' || !TOOL_NAME.test(tool.name ?? '') || !isString(tool.description ?? '', 2000) ||
      typeof tool.parameters !== 'object' || JSON.stringify(tool.parameters).length > 6000) throw new HttpError(400, 'Invalid tools.');
  }
  if (previous !== undefined && previous !== null && !RESPONSE_ID.test(previous)) throw new HttpError(400, 'Invalid previous_response_id.');
  return {
    input, instructions,
    tools: tools.map(({ type, name, description, parameters }) => ({ type, name, description, parameters })),
    ...(previous ? { previous_response_id: previous } : {}),
  };
}

// Admission and counters share one D1 transaction, so limits hold across Worker instances.
export async function reserveModel(db, env, deviceId, now = Date.now()) {
  const l = limits(env);
  const id = crypto.randomUUID();
  const dayNumber = Math.floor(now / 86_400_000);
  const day = `day:${dayNumber}`, minute = `minute:${Math.floor(now / 60_000)}`, device = `device:${deviceId}:${dayNumber}`;
  const dayEnd = (dayNumber + 1) * 86_400_000, minuteEnd = (Math.floor(now / 60_000) + 1) * 60_000;
  const sql = (query, ...args) => db.prepare(query).bind(...args);
  const result = await db.batch([
    sql('DELETE FROM model_jobs WHERE expires_at <= ?', now),
    sql('DELETE FROM model_budget WHERE expires_at <= ?', now),
    sql(`INSERT INTO model_jobs(id, expires_at) SELECT ?, ? WHERE
      (SELECT COUNT(*) FROM model_jobs) < ? AND
      COALESCE((SELECT calls FROM model_budget WHERE bucket = ?), 0) < ? AND
      COALESCE((SELECT calls FROM model_budget WHERE bucket = ?), 0) < ? AND
      COALESCE((SELECT calls FROM model_budget WHERE bucket = ?), 0) < ? RETURNING id`,
      id, now + JOB_TTL_MS, l.concurrency, day, l.perDay, minute, l.perMinute, device, l.perDeviceDay),
    ...[[day, dayEnd], [minute, minuteEnd], [device, dayEnd]].map(([bucket, expiry]) =>
      sql(`INSERT INTO model_budget(bucket, calls, expires_at) SELECT ?, 1, ? WHERE EXISTS (SELECT 1 FROM model_jobs WHERE id = ?)
        ON CONFLICT(bucket) DO UPDATE SET calls = calls + 1`, bucket, expiry, id)),
    sql('SELECT bucket, calls FROM model_budget WHERE bucket IN (?, ?, ?)', day, minute, device),
  ]);
  const used = Object.fromEntries(result.at(-1).results.map(r => [r.bucket, r.calls]));
  const quota = { device_used: used[device] ?? 0, device_limit: l.perDeviceDay, resets_at: new Date(dayEnd).toISOString() };
  if (result[2].results.length) return { job: id, quota };
  const untilMidnight = Math.ceil((dayEnd - now) / 1000);
  if ((used[device] ?? 0) >= l.perDeviceDay)
    throw new HttpError(429, `This computer has used today's ${l.perDeviceDay} AI calls. The allowance resets at ${inEastern(dayEnd)}.`, untilMidnight);
  if ((used[day] ?? 0) >= l.perDay)
    throw new HttpError(429, `Courtside's shared AI budget for today is used up. It resets at ${inEastern(dayEnd)}.`, untilMidnight);
  throw new HttpError(429, 'The AI assistant is busy right now. Try again in a minute.', 30);
}

const isObject = v => Boolean(v) && typeof v === 'object' && !Array.isArray(v);

export function validateRuling(body) {
  if (!isObject(body)) throw new HttpError(400, 'Invalid ruling.');
  const { ruling, note = '', trade, numbers = null, decided_at: decidedAt } = body;
  if (ruling !== 'fair' && ruling !== 'unfair') throw new HttpError(400, 'A ruling is fair or unfair.');
  if (!isString(note, 500)) throw new HttpError(400, 'The note must be 500 characters or fewer.');
  if (!isObject(trade) || (numbers !== null && !isObject(numbers))) throw new HttpError(400, 'Invalid ruling.');
  if (!isString(decidedAt, 40) || Number.isNaN(Date.parse(decidedAt))) throw new HttpError(400, 'Invalid ruling date.');
  return { ruling, note, trade, numbers, decided_at: decidedAt };
}

async function sha256Hex(value) {
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)));
  return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
}

// The client token is in every release build, so this keeps a leaked one from filling the database.
async function spendRulingWrite(db, env, now) {
  const day = Math.floor(now / 86_400_000);
  const dayEnd = (day + 1) * 86_400_000;
  const row = await db.prepare(`INSERT INTO model_budget(bucket, calls, expires_at) VALUES (?, 1, ?)
    ON CONFLICT(bucket) DO UPDATE SET calls = calls + 1 RETURNING calls`)
    .bind(`rulings:${day}`, dayEnd).first();
  if (row.calls > limit(env.RULINGS_DAILY_LIMIT, 2000)) throw new HttpError(429, `Courtside's rulings limit for today is used up. It resets at ${inEastern(dayEnd)}.`);
}

async function handleRulings(request, env, league, tradeKey, now = Date.now()) {
  const db = env.DB;
  const adminKey = request.headers.get('x-league-admin') ?? '';
  if (adminKey.length > 200) throw new HttpError(400, 'Invalid admin key.');
  const hash = adminKey ? await sha256Hex(adminKey) : null;
  const admin = await db.prepare('SELECT key_hash FROM league_admins WHERE league = ?').bind(league).first();

  if (!tradeKey) {
    if (request.method !== 'GET') throw new HttpError(405, 'Use GET.');
    const { results } = await db.prepare('SELECT trade_key, body FROM league_rulings WHERE league = ? ORDER BY updated_at DESC').bind(league).all();
    return {
      status: 200,
      body: {
        claimed: Boolean(admin), you: Boolean(admin && hash && admin.key_hash === hash),
        rulings: results.map(r => ({ ...JSON.parse(r.body), key: r.trade_key })),
      },
    };
  }

  if (request.method !== 'PUT' && request.method !== 'DELETE') throw new HttpError(405, 'Use PUT or DELETE.');
  if (!hash) throw new HttpError(403, "Only the league's admin can do that.");
  if (admin && admin.key_hash !== hash) throw new HttpError(403, "Only the league's admin can do that.");

  if (request.method === 'DELETE') {
    await db.prepare('DELETE FROM league_rulings WHERE league = ? AND trade_key = ?').bind(league, tradeKey).run();
    return { status: 204 };
  }

  const ruling = validateRuling(await readJSON(request, MAX_RULING_BYTES));
  const held = await db.prepare('SELECT COUNT(*) AS total, COALESCE(SUM(trade_key = ?), 0) AS existing FROM league_rulings WHERE league = ?')
    .bind(tradeKey, league).first();
  if (!held.existing && held.total >= MAX_LEAGUE_RULINGS) throw new HttpError(429, 'This league has reached the limit on rulings.');
  await spendRulingWrite(db, env, now);
  // One transaction, and the write only lands for the key that holds the claim, so two first writes can't both win.
  const sql = (query, ...args) => db.prepare(query).bind(...args);
  const result = await db.batch([
    sql('INSERT OR IGNORE INTO league_admins(league, key_hash, claimed_at) VALUES (?, ?, ?)', league, hash, now),
    sql(`INSERT INTO league_rulings(league, trade_key, body, updated_at)
      SELECT ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM league_admins WHERE league = ? AND key_hash = ?)
      ON CONFLICT(league, trade_key) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at`,
      league, tradeKey, JSON.stringify(ruling), now, league, hash),
    sql('SELECT key_hash FROM league_admins WHERE league = ?', league),
  ]);
  if (result.at(-1).results[0]?.key_hash !== hash) throw new HttpError(403, "Only the league's admin can do that.");
  return { status: 200, body: { ok: true } };
}

async function callOpenAI(env, request, maxOutputTokens, signal) {
  let upstream;
  try {
    upstream = await fetch(OPENAI_URL, {
      method: 'POST',
      headers: { Authorization: `Bearer ${env.OPENAI_API_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: env.MODEL || 'gpt-6-luna', reasoning: { effort: 'low' }, max_output_tokens: maxOutputTokens,
        store: true, ...request,
      }),
      signal: AbortSignal.any([signal, AbortSignal.timeout(UPSTREAM_TIMEOUT_MS)].filter(Boolean)),
    });
  } catch (error) {
    if (error.name === 'TimeoutError' || error.name === 'AbortError') throw new HttpError(504, 'The model took too long. Try again.');
    throw new HttpError(502, 'The AI service could not reach OpenAI. Try again.');
  }
  const data = await upstream.json().catch(() => ({}));
  if (upstream.ok) return { id: data.id, output: Array.isArray(data.output) ? data.output : [], usage: data.usage ?? null };
  const message = data?.error?.message;
  if (upstream.status === 401) throw new HttpError(502, "The AI service's OpenAI key was rejected. The owner needs to update it in Cloudflare.");
  if (upstream.status === 429) throw new HttpError(429, 'OpenAI is rate limiting this service. Try again in a minute.', 60);
  if (upstream.status === 400 && /previous response/i.test(message ?? '')) throw new HttpError(400, 'That conversation expired. Start a new question.');
  throw new HttpError(502, `OpenAI returned an error (${upstream.status}).`);
}

export default {
  async fetch(request, env) {
    const route = new URL(request.url).pathname;
    const headers = { ...securityHeaders };
    const send = (status, body, extra = {}) => Response.json(body, { status, headers: { ...headers, ...extra } });
    // Desktop and server clients never send Origin; refusing it keeps browsers from using a leaked token.
    if (request.headers.get('origin')) return send(403, { error: 'Browser access is not allowed.' });
    if (!env.DB) return send(503, { error: 'Bind the D1 database before using this service.' });
    try {
      if (route === '/health' && request.method === 'GET') {
        await env.DB.prepare('SELECT bucket FROM model_budget LIMIT 1').first();
        return send(200, { service: 'courtside-ai', version: 1, model: env.MODEL || 'gpt-6-luna', ready: Boolean(env.OPENAI_API_KEY && env.CLIENT_TOKEN) });
      }
      const rulings = route.match(RULINGS_ROUTE);
      if (route !== '/v1/responses' && !rulings) return send(404, { error: 'Not found.' });
      if (!rulings && request.method !== 'POST') return send(405, { error: 'Use POST.' }, { Allow: 'POST' });
      if (typeof env.CLIENT_TOKEN !== 'string' || env.CLIENT_TOKEN.length < 24) return send(503, { error: 'The AI service is not configured yet.' });
      if (!await matchesToken(request.headers.get('authorization'), env.CLIENT_TOKEN))
        return send(401, { error: 'This copy of Courtside is out of date. Download the latest release.' });
      if (rulings) {
        const { status, body } = await handleRulings(request, env, rulings[1], rulings[2]);
        return status === 204 ? new Response(null, { status, headers }) : send(status, body);
      }
      const deviceId = request.headers.get('x-device-id') ?? '';
      if (!DEVICE_ID.test(deviceId)) return send(400, { error: 'Missing device id.' });
      if (!env.OPENAI_API_KEY) return send(503, { error: 'The AI service owner still needs to add the OpenAI key.' });
      const body = validateRequest(await readJSON(request, MAX_BODY_BYTES));
      const { job, quota } = await reserveModel(env.DB, env, deviceId.toLowerCase());
      try {
        const result = await callOpenAI(env, body, limits(env).maxOutputTokens, request.signal);
        return send(200, { ...result, quota });
      } finally {
        await env.DB.prepare('DELETE FROM model_jobs WHERE id = ?').bind(job).run().catch(() => {});
      }
    } catch (error) {
      if (error instanceof HttpError)
        return send(error.status, { error: error.message }, error.retryAfter ? { 'Retry-After': String(error.retryAfter) } : {});
      console.error(error);
      return send(500, { error: 'Unexpected server error.' });
    }
  },

  async scheduled(_event, env) {
    const now = Date.now();
    await env.DB.batch([
      env.DB.prepare('DELETE FROM model_jobs WHERE expires_at <= ?').bind(now),
      env.DB.prepare('DELETE FROM model_budget WHERE expires_at <= ?').bind(now),
    ]);
  },
};
