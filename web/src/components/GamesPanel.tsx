import { useState } from "react";
import { pct, relTime } from "../lib/format";
import { useNbaGames } from "../lib/queries";
import type { NbaGame, NbaGames } from "../lib/types";
import { Panel, Segmented, Tag, Term } from "./ui";

/** Today on the NBA's calendar (US Eastern), as YYYY-MM-DD: a late game belongs to the day it tipped off there. */
export function nbaToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** The team the model picks, its chance, and whether the final score proved it right (null until the game is over). */
export function pick(g: NbaGame): { team: string; chance: number; right: boolean | null } {
  const home = g.home_win >= 0.5;
  const over = g.status === "final" && g.home_pts != null && g.away_pts != null && g.home_pts !== g.away_pts;
  return { team: home ? g.home : g.away, chance: home ? g.home_win : 1 - g.home_win,
           right: over ? (g.home_pts! > g.away_pts!) === home : null };
}

/** The evidence that does most for the picked team (the published factors are from the home team's side). */
export function mainReason(g: NbaGame): string | null {
  const side = g.home_win >= 0.5 ? 1 : -1;
  const best = g.factors.filter((f) => f.points * side > 0).sort((a, b) => Math.abs(b.points) - Math.abs(a.points))[0];
  return best ? best.factor.replace(/ \(.*\)$/, "").toLowerCase() : null;
}

const tipTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) : "";

function when(g: NbaGame): string {
  if (g.status !== "scheduled" && g.home_pts != null && g.away_pts != null) {
    return `${g.status === "final" ? "Final" : "Live"} ${g.away_pts}–${g.home_pts}`;
  }
  return tipTime(g.tip);
}

const lastName = (name: string) => name.split(" ").slice(1).join(" ") || name;

function Game({ g }: { g: NbaGame }) {
  const p = pick(g);
  const reason = mainReason(g);
  const out = [[g.away, g.away_out], [g.home, g.home_out]] as const;
  const missing = out.filter(([, names]) => names.length).map(([team, names]) => `${team} out: ${names.map(lastName).join(", ")}`);
  return (
    <li>
      <div className="row between small">
        <span>
          <strong>{g.away}</strong> at <strong>{g.home}</strong>{g.neutral ? " (neutral court)" : ""}
          <span className="faint"> · {when(g)}</span>
        </span>
        <span className="row" style={{ gap: 6 }}>
          {p.right != null && <Tag tone={p.right ? "up" : "down"}>{p.right ? "right" : "wrong"}</Tag>}
          <span className="strong">{p.team} {pct(p.chance)}</span>
        </span>
      </div>
      <div className="wpbar" role="img" aria-label={`${g.away} ${pct(1 - g.home_win)}, ${g.home} ${pct(g.home_win)}`}
        style={{ margin: "4px 0" }}>
        <span style={{ width: `${(1 - g.home_win) * 100}%` }} />
      </div>
      <div className="tiny muted">
        {g.away_score != null && g.home_score != null && <>Predicted {g.away} {Math.round(g.away_score)}, {g.home} {Math.round(g.home_score)}</>}
        {reason && <> · {reason}</>}
        {missing.length > 0 && <> · {missing.join("; ")}</>}
      </div>
    </li>
  );
}

/** Which day to open on: today if it has games, else the next day that does. */
export function defaultDay(feed: NbaGames, today: string): string | null {
  const days = (feed.days ?? []).filter((d) => d.date >= today && d.games.length > 0);
  return days[0]?.date ?? null;
}

const dayLabel = (date: string, today: string) => {
  if (date === today) return "Today";
  const d = new Date(`${date}T12:00:00`);
  const t = new Date(`${today}T12:00:00`);
  if (Math.round((d.getTime() - t.getTime()) / 86_400_000) === 1) return "Tomorrow";
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
};

/** Tonight's NBA games with Courtside's game model: who it picks, by how much, and why. Shows nothing when there
 *  are no predictions to show (no games, or the published file couldn't be read). */
export function GamesPanel({ now }: { now?: Date }) {
  const { data } = useNbaGames();
  const today = nbaToday(now);
  const [chosen, setChosen] = useState<string | null>(null);
  if (!data?.available) return null;
  const days = (data.days ?? []).filter((d) => d.date >= today && d.games.length > 0);
  const date = chosen && days.some((d) => d.date === chosen) ? chosen : defaultDay(data, today);
  const day = days.find((d) => d.date === date);
  if (!day) return null;
  const bt = data.backtest;
  return (
    <Panel title="NBA games"
      note={bt?.accuracy != null && bt.games != null
        ? <><Term t="game_model">Courtside's game model</Term> picked {pct(bt.accuracy)} of winners in {bt.games.toLocaleString("en-US")} test games.</>
        : undefined}
      actions={days.length > 1
        ? <Segmented label="Day" value={day.date} onChange={setChosen} options={days.slice(0, 2).map((d) => ({ value: d.date, label: dayLabel(d.date, today) }))} />
        : <span className="small muted">{dayLabel(day.date, today)}</span>}
      foot={<>
        Odds are from before tip-off{data.injuries ? ", with players ESPN lists as out counted out" : "; the injury report couldn't be read, so nobody is counted out"}.
        {data.generated_at ? ` Updated ${relTime(data.generated_at)}.` : ""}
      </>}>
      <ul className="list">
        {day.games.map((g) => <Game key={g.id || `${g.away}-${g.home}`} g={g} />)}
      </ul>
    </Panel>
  );
}
