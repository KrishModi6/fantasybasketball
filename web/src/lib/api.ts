// Thin fetch wrapper. The API base is same-origin on the web; the desktop app
// (or a phone pointed at a home server) can set a different server URL.

const TOKEN_KEY = "cs.token";
const SERVER_KEY = "cs.server";

/** Auto-update status from the desktop app (see desktop/main.js). */
export interface UpdateState {
  status: "idle" | "checking" | "current" | "downloading" | "ready" | "available" | "error";
  current?: string;
  version?: string;
  percent?: number;
  error?: string;
  canInstall?: boolean;
  checkedAt?: string; // when the last check finished
}

declare global {
  interface Window {
    courtside?: {
      defaultServer?: string;
      platform?: string;
      version?: string;
      local?: boolean;
      updates?: {
        state: () => Promise<UpdateState>;
        install: () => Promise<void>;
        download: () => Promise<void>;
        check: () => Promise<void>;
        onChange: (callback: (state: UpdateState) => void) => () => void;
      };
    };
  }
}

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function safeSet(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage unavailable (private mode) — session-only */
  }
}

export function serverUrl(): string {
  const cs = window.courtside;
  if (cs?.local && cs.defaultServer) return cs.defaultServer.replace(/\/$/, ""); // the bundled engine
  const saved = safeGet(SERVER_KEY);
  if (saved) return saved.replace(/\/$/, "");
  if (window.courtside?.defaultServer) return window.courtside.defaultServer.replace(/\/$/, "");
  return ""; // same origin
}
export function setServerUrl(url: string | null) {
  safeSet(SERVER_KEY, url ? url.trim().replace(/\/$/, "") : null);
}
export const isDesktop = () => Boolean(window.courtside);
/** Desktop app running its own engine: one local user, signed in automatically. */
export const isLocalApp = () => Boolean(window.courtside?.local);

let token: string | null = safeGet(TOKEN_KEY);
export const getToken = () => token;
export function setToken(t: string | null) {
  token = t;
  safeSet(TOKEN_KEY, t);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Opts = { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal };

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(`${serverUrl()}${path}`, {
      method: opts.method ?? (body ? "POST" : "GET"),
      headers,
      body,
      signal: opts.signal,
    });
  } catch {
    throw new ApiError(0, "Can't reach the Courtside server. Check your connection or server address.");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = undefined;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    data = text;
  }
  if (!res.ok) {
    if (res.status === 401 && token) {
      setToken(null);
      window.dispatchEvent(new Event("cs:signed-out"));
    }
    throw new ApiError(res.status, errorMessage(data) ?? `Request failed (${res.status})`);
  }
  return data as T;
}

function errorMessage(data: unknown): string | null {
  if (!data || typeof data !== "object") return typeof data === "string" && data ? data.slice(0, 200) : null;
  const d = (data as { detail?: unknown }).detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d.length) {
    const first = d[0] as { msg?: string; loc?: unknown[] };
    const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : "";
    return `${field ? field.replace(/_/g, " ") + ": " : ""}${first.msg ?? "invalid value"}`;
  }
  return null;
}

export function wsUrl(path: string): string {
  const base = serverUrl() || window.location.origin;
  const u = new URL(path, base.startsWith("http") ? base : window.location.origin);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  if (token) u.searchParams.set("token", token);
  return u.toString();
}
