const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export const fmt0 = (x: number | null | undefined) => (x == null || Number.isNaN(x) ? "–" : nf0.format(x));
export const fmt1 = (x: number | null | undefined) => (x == null || Number.isNaN(x) ? "–" : nf1.format(x));
export const pct = (x: number | null | undefined, digits = 0) =>
  x == null ? "–" : `${(x * 100).toFixed(digits)}%`;
export const signed = (x: number, digits = 1) => {
  if (Math.abs(x) < 0.5 * 10 ** -digits) return (0).toFixed(digits);
  return `${x > 0 ? "+" : "−"}${Math.abs(x).toFixed(digits)}`;
};
export const signedInt = (x: number) => signed(x, 0).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
export const signedPct = (x: number, digits = 1) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x * 100).toFixed(digits)}%`;
export const record = (w: number, l: number, t = 0) => (t ? `${w}–${l}–${t}` : `${w}–${l}`);

export function relTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function shortDate(iso: string | null | undefined, withDay = false): string {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(withDay ? { weekday: "short" } : {}) });
}

export const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");

export const injuryShort: Record<string, string> = {
  OUT: "O",
  INJURY_RESERVE: "IR",
  DAY_TO_DAY: "DTD",
  QUESTIONABLE: "Q",
  DOUBTFUL: "D",
  SUSPENSION: "SSPD",
};

export const sourceLabel: Record<string, string> = {
  espn: "ESPN",
  demo: "Demo",
  file: "File import",
  flaim: "Flaim",
};
