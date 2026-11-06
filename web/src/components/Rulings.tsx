import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { pct, relTime, verdictTone } from "../lib/format";
import type { Ruling, RulingCall, RulingNumbers } from "../lib/types";
import { useLeagueCtx } from "./Layout";
import { Delta, Panel, Tag, Term } from "./ui";

export const RULING_WORDS: Record<RulingCall, string> = { fair: "Fair", unfair: "Not fair" };

/** The admin's call. "Admin:" keeps it from reading as Courtside's own verdict, which can sit right beside it. */
export function RulingTag({ ruling }: { ruling: RulingCall }) {
  return <Tag tone={ruling === "fair" ? "up" : "down"}>Admin: {RULING_WORDS[ruling]}</Tag>;
}

const odds = (before: number | null, after: number | null) =>
  before == null && after == null ? "–" : `${pct(before)} → ${pct(after)}`;

/** Courtside's own numbers for a trade, one row a team. */
export function NumbersTable({ numbers }: { numbers: RulingNumbers }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Team</th>
            <th className="n"><Term t="change_wk" /></th>
            <th className="n"><Term t="odds_change">Playoff odds</Term></th>
            <th className="n"><Term t="odds_change">Title odds</Term></th>
            <th><Term t="verdict">Courtside's verdict</Term></th>
          </tr>
        </thead>
        <tbody>
          {numbers.sides.map((s) => (
            <tr key={s.team_id}>
              <td>{s.team_name ?? `Team ${s.team_id}`}</td>
              <td className="n">{s.delta_per_week == null ? "–" : <Delta value={s.delta_per_week} />}</td>
              <td className="n">{odds(s.before_playoff_odds, s.after_playoff_odds)}</td>
              <td className="n">{odds(s.before_title_odds, s.after_title_odds)}</td>
              <td>{s.verdict ? <Tag tone={verdictTone(s.verdict)}>{s.verdict}</Tag> : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Where a finished trade's numbers come from: they aren't the before-and-after the trade analyzer shows. */
export function UndoNote() {
  return (
    <p className="small muted">
      This trade already happened, so these numbers come from undoing it on today's rosters. They're not what the trade analyzer showed before it went through.
    </p>
  );
}

/** The admin's rulings, for everyone in the league. A separate panel, since they don't change Courtside's numbers. */
export function LeagueRulings() {
  const { league } = useLeagueCtx();
  const { data } = useQuery({
    queryKey: ["league", league.id, "rulings"],
    queryFn: () => api<Ruling[]>(`/api/leagues/${league.id}/trades/rulings`),
  });
  if (!data?.length) return null;
  return (
    <Panel title="League admin's rulings" note="The league admin's own calls. They don't change Courtside's numbers, and they can't block or undo anything on ESPN.">
      <ul className="list">
        {data.map((r) => (
          <li key={r.key}>
            <div className="row between">
              <span><RulingTag ruling={r.ruling} /> {r.trade.text}</span>
              <span className="small muted">{relTime(r.decided_at)}</span>
            </div>
            {r.note && <p className="small">{r.note}</p>}
            {r.numbers && (
              <details className="small">
                <summary>Courtside's numbers when the admin ruled</summary>
                {r.numbers.completed && <UndoNote />}
                <NumbersTable numbers={r.numbers} />
              </details>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
