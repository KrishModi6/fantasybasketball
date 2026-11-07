import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, isLocalApp } from "../lib/api";
import { pct, relTime, verdictTone } from "../lib/format";
import { useToast } from "../lib/toast";
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

/** Each app has its own projection overrides, so nobody else can see that the admin's went into these numbers. */
export function AdjustedNote({ mine = false }: { mine?: boolean }) {
  return (
    <p className="small muted">
      {mine
        ? "These numbers include your projection changes, and anyone reading your ruling is told so."
        : "These numbers include projection changes the admin made in their own app, so they can differ from what yours shows."}
    </p>
  );
}

/** How the league's admin takes charge from a desktop copy that can't claim the league: it came from an invite code, or a new computer. */
export function AdminCode() {
  const { league } = useLeagueCtx();
  const qc = useQueryClient();
  const toast = useToast();
  const [code, setCode] = useState("");
  const redeem = useMutation({
    mutationFn: () => api(`/api/leagues/${league.id}/trades/admin-code`, { method: "POST", body: { code } }),
    onSuccess: () => {
      toast("You're the league's admin. Rule on trades from the Admin tab.");
      setCode("");
      qc.invalidateQueries({ queryKey: ["league", league.id, "meta"] });
    },
    onError: (e: Error) => toast(e.message, "err"),
  });
  if (!isLocalApp() || league.source !== "espn" || league.me?.role !== "owner" || league.me.is_admin) return null;
  return (
    <Panel title="League admin">
      <form className="form" onSubmit={(e) => { e.preventDefault(); redeem.mutate(); }}>
        <p className="small muted">Only whoever runs this league has an admin code. It turns on the Admin tab, where they rule on trades.</p>
        <div className="field">
          <label htmlFor="admin-code">Admin code</label>
          <input id="admin-code" type="password" className="input mono" value={code} onChange={(e) => setCode(e.target.value)} maxLength={200} autoComplete="off" />
        </div>
        <div className="row">
          <button className="btn primary" disabled={!code.trim() || redeem.isPending}>Enter code</button>
        </div>
      </form>
    </Panel>
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
                {r.numbers.adjusted && <AdjustedNote />}
                <NumbersTable numbers={r.numbers} />
              </details>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
