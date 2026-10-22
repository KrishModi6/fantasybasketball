// Where the user was last, so reopening Courtside (or its home page) goes straight back there.

const LAST_KEY = "cs.last-path";
// One-off parameters that shouldn't come back on the next launch: an open player drawer, a queued AI question.
const TRANSIENT = ["player", "ask", "preset"];

export function rememberPath(pathname: string, search: string) {
  const params = new URLSearchParams(search);
  TRANSIENT.forEach((k) => params.delete(k));
  const qs = params.toString();
  try {
    localStorage.setItem(LAST_KEY, pathname + (qs ? `?${qs}` : ""));
  } catch {
    /* storage unavailable: the app just opens on the league list */
  }
}

export function rememberedPath(): string | null {
  try {
    return localStorage.getItem(LAST_KEY);
  } catch {
    return null;
  }
}

/** True when the router has an earlier in-app page to go back to (react-router keeps an index in history.state). */
export const canGoBack = () => ((window.history.state as { idx?: number } | null)?.idx ?? 0) > 0;
