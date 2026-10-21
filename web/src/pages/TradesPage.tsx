import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, Search, Share2, Trash2 } from "lucide-react";
import { useLeagueCtx } from "../components/Layout";
import { Delta, Empty, ErrorBox, Injury, LoadingBlock, PageHead, Panel, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { fmt0, fmt1, pct, relTime, signedPct } from "../lib/format";
import { useTeam } from "../lib/queries";
import { useToast } from "../lib/toast";
import type { PlayerRow, TradeEval, TradeSide, TradeSuggestion } from "../lib/types";
import { useAuth } from "../lib/auth";

type Tab = "analyze" | "find" | "board";
interface Draft {
  a: number | null;
  b: number | null;
  aGives: string[];
  bGives: string[];
}

export default function TradesPage() {
  const { myTeamId, teams } = useLeagueCtx();
  const [tab, setTab] = useState<Tab>("analyze");
  const [autoRun, setAutoRun] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => ({
    a: myTeamId ?? teams[0]?.id ?? null,
    b: teams.find((t) => t.id !== (myTeamId ?? teams[0]?.id))?.id ?? null,
    aGives: [],
    bGives: [],
  }));
  const load = (d: Draft) => {
    setDraft(d);
    setAutoRun(true);
    setTab("analyze");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  return (
    <>
      <PageHead
        kicker="Trade analyzer"
        title="Trades"
        lede="A trade is judged by what it does to each team's optimal daily lineups for the rest of the season (positions, schedule overlap, depth and the roster spot it frees or costs), then by a full season simulation."
      />
      <Tabs label="Trade tools" value={tab} onChange={setTab} tabs={[
        { value: "analyze", label: "Analyze a trade" },
        { value: "find", label: "Find trades" },
        { value: "board", label: "League board" },
      ]} />
      {tab === "analyze" && <Analyzer draft={draft} setDraft={setDraft} autoRun={autoRun} />}
      {tab === "find" && <Finder onLoad={load} />}
      {tab === "board" && <Board onLoad={load} />}
    </>
  );
}

function RosterPicker({ teamId, picked, onToggle, label }: { teamId: number | null; picked: string[]; onToggle: (id: string) => void; label: string }) {
  const { league } = useLeagueCtx();
  const { data, isLoading } = useTeam(league.id, teamId);
  if (teamId == null) return null;
  if (isLoading) return <LoadingBlock rows={6} />;
  return (
    <fieldset className="pick-list" style={{ margin: 0, padding: 0 }}>
      <legend className="sr-only">{label}</legend>
      {data?.roster.map((p: PlayerRow) => (
        <label key={p.id}>
          <input type="checkbox" checked={picked.includes(p.id)} onChange={() => onToggle(p.id)} />
          <span className="player-cell">
            <span className="nm">{p.name}<Injury status={p.injury_status} /></span>
            <span className="pos-tag">{p.pro_team} · {p.positions.join(", ")}</span>
          </span>
          <span className="small num">{fmt1(p.fp_pg)}</span>
        </label>
      ))}
    </fieldset>
  );
}

function Analyzer({ draft, setDraft, autoRun }: { draft: Draft; setDraft: (d: Draft) => void; autoRun: boolean }) {
  const { league, teams } = useLeagueCtx();
  const toast = useToast();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  // The evaluated trade is whatever was last submitted; editing the selection marks results as stale.
  const [submitted, setSubmitted] = useState<Draft | null>(autoRun ? draft : null);
  const evaluate = useQuery({
    queryKey: ["league", league.id, "evaluate", submitted],
    queryFn: () => api<TradeEval>(`/api/leagues/${league.id}/trades/evaluate`, {
      body: { team_a: submitted!.a, team_b: submitted!.b, a_gives: submitted!.aGives, b_gives: submitted!.bGives },
    }),
    enabled: submitted != null,
    staleTime: 5 * 60_000,
  });
  const stale = submitted != null && JSON.stringify(submitted) !== JSON.stringify(draft);
  const share = useMutation({
    mutationFn: () => api(`/api/leagues/${league.id}/trades/board`, {
      body: { team_a: submitted!.a, team_b: submitted!.b, a_gives: submitted!.aGives, b_gives: submitted!.bGives, note },
    }),
    onSuccess: () => {
      toast("Shared with the league.");
      setNote("");
      qc.invalidateQueries({ queryKey: ["league", league.id, "board"] });
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const toggle = (side: "aGives" | "bGives", id: string) => {
    const list = draft[side];
    setDraft({ ...draft, [side]: list.includes(id) ? list.filter((x) => x !== id) : [...list, id] });
  };
  const ready = draft.a != null && draft.b != null && draft.a !== draft.b && (draft.aGives.length > 0 || draft.bGives.length > 0);

  return (
    <div className="stack" style={{ gap: 22 }}>
      <div className="grid grid-2">
        {(["a", "b"] as const).map((side) => (
          <div key={side} className="stack" style={{ gap: 10 }}>
            <div className="field">
              <label htmlFor={`team-${side}`}>{side === "a" ? "Team A gives" : "Team B gives"}</label>
              <select id={`team-${side}`} className="select" value={draft[side] ?? ""}
                onChange={(e) => setDraft({ ...draft, [side]: Number(e.target.value), [side === "a" ? "aGives" : "bGives"]: [] })}>
                {teams.map((t) => <option key={t.id} value={t.id} disabled={t.id === (side === "a" ? draft.b : draft.a)}>{t.name}</option>)}
              </select>
            </div>
            <RosterPicker teamId={draft[side]} picked={side === "a" ? draft.aGives : draft.bGives}
              onToggle={(id) => toggle(side === "a" ? "aGives" : "bGives", id)} label={`Players team ${side.toUpperCase()} gives`} />
          </div>
        ))}
      </div>
      <div className="row wrap">
        <button className="btn accent" disabled={!ready || evaluate.isFetching} onClick={() => setSubmitted({ ...draft })}>
          <ArrowLeftRight /> {evaluate.isFetching ? "Simulating…" : "Evaluate trade"}
        </button>
        <span className="small muted">{draft.aGives.length} for {draft.bGives.length}. Uneven trades automatically drop the weakest player or add the best free agent.</span>
      </div>
      {evaluate.error && <ErrorBox error={evaluate.error} />}
      {evaluate.isFetching && !evaluate.data && <LoadingBlock rows={6} />}
      {stale && evaluate.data && <div className="callout neutral small">Selection changed. Evaluate again to update the numbers below.</div>}
      {evaluate.data && (
        <>
          <TradeResult ev={evaluate.data} />
          <Panel title="Share with the league" kicker="Posts this trade and its numbers to the board">
            <div className="row wrap">
              <input className="input" style={{ flex: 1, minWidth: 220 }} placeholder="Add a note (optional)" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
              <button className="btn secondary" onClick={() => share.mutate()} disabled={share.isPending}><Share2 /> Share</button>
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}

function SideCard({ s }: { s: TradeSide }) {
  return (
    <div className={`verdict ${s.verdict}`}>
      <div className="row between">
        <span className="h3">{s.team_name}</span>
        <span className={`badge ${s.verdict === "advantageous" ? "pos" : s.verdict === "disadvantageous" ? "neg" : ""}`}>{s.verdict}</span>
      </div>
      <div className="row" style={{ alignItems: "baseline", gap: 8 }}>
        <span className="big"><Delta value={s.delta_per_week} /></span>
        <span className="small muted">pts / week</span>
      </div>
      <dl className="kv">
        <dt>Rest of season</dt>
        <dd><Delta value={s.delta_ros} digits={0} /> ({signedPct(s.delta_pct)})</dd>
        <dt>Fantasy playoff weeks</dt>
        <dd><Delta value={s.delta_playoffs} digits={0} /></dd>
        {s.before_playoff_odds != null && (
          <>
            <dt>Playoff odds</dt>
            <dd>{pct(s.before_playoff_odds)} → {pct(s.after_playoff_odds)}</dd>
            <dt>Title odds</dt>
            <dd>{pct(s.before_title_odds)} → {pct(s.after_title_odds)}</dd>
          </>
        )}
      </dl>
    </div>
  );
}

export function TradeResult({ ev }: { ev: TradeEval }) {
  const { openPlayer, teamName } = useLeagueCtx();
  const [a, b] = ev.sides;
  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="grid grid-2">
        <SideCard s={a} />
        <SideCard s={b} />
      </div>
      <div className="callout neutral row wrap between">
        <span><strong className="badge ink" style={{ marginRight: 8 }}>{ev.fairness.label}</strong>{ev.summary}</span>
        <span className="small muted">Chance {b.team_name} sees it as a gain: {pct(ev.fairness.partner_accept_likelihood)}</span>
      </div>
      <div className="grid grid-2" style={{ alignItems: "start" }}>
        {ev.math.map((m) => (
          <Panel key={m.team_id} title={m.team_name} kicker="The math">
            <ol className="math-steps">{m.lines.map((l, i) => <li key={i}><span>{l}</span></li>)}</ol>
          </Panel>
        ))}
      </div>
      <Panel title="Players involved" flush>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th>Player</th><th className="hide-sm">From</th><th className="n">FP/G</th><th className="n">± game</th><th className="n">ROS games</th><th className="n">ROS pts</th><th className="n">Over repl.</th><th className="n hide-sm">Last 10</th></tr>
            </thead>
            <tbody>
              {ev.players.map((p) => (
                <tr key={p.player_id}>
                  <td className="player">
                    <div className="player-cell">
                      <span><button className="nm" onClick={() => openPlayer(p.player_id)}>{p.name}</button><Injury status={p.injury_status} /></span>
                      <span className="pos-tag">{p.pro_team} · {p.positions.join(", ")}{p.games_out > 0 ? ` · out ~${fmt0(p.games_out)} g` : ""}</span>
                    </div>
                  </td>
                  <td className="hide-sm small">{teamName(p.fantasy_team_id)}</td>
                  <td className="n strong">{fmt1(p.fp_pg)}</td>
                  <td className="n muted">{fmt0(p.fp_sd)}</td>
                  <td className="n">{fmt0(p.ros_games)}</td>
                  <td className="n">{fmt0(p.ros_points)}</td>
                  <td className="n"><Delta value={p.vorp} digits={0} /></td>
                  <td className="n hide-sm">{fmt1(p.recent_fp_pg)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function Finder({ onLoad }: { onLoad: (d: Draft) => void }) {
  const { league, myTeamId, teams } = useLeagueCtx();
  const [team, setTeam] = useState<number | null>(myTeamId ?? teams[0]?.id ?? null);
  const [shape, setShape] = useState("2-2");
  const [partner, setPartner] = useState<string>("");
  const [run, setRun] = useState(0);
  const [give, get] = shape.split("-");
  const q = useQuery({
    queryKey: ["league", league.id, "suggest", team, shape, partner, run],
    queryFn: () => api<{ suggestions: TradeSuggestion[] }>(
      `/api/leagues/${league.id}/trades/suggestions?team_id=${team}&max_give=${give}&max_get=${get}${partner ? `&partner_id=${partner}` : ""}`),
    enabled: run > 0 && team != null,
    staleTime: 5 * 60_000,
  });
  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="row wrap" style={{ alignItems: "flex-end" }}>
        <div className="field">
          <label htmlFor="ft">Find trades for</label>
          <select id="ft" className="select" style={{ width: 220 }} value={team ?? ""} onChange={(e) => setTeam(Number(e.target.value))}>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.name}{t.id === myTeamId ? " (you)" : ""}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="fs">Package size</label>
          <select id="fs" className="select" style={{ width: 170 }} value={shape} onChange={(e) => setShape(e.target.value)}>
            <option value="1-1">1-for-1 only</option>
            <option value="2-2">Up to 2-for-2</option>
            <option value="2-1">Give up to 2, get 1</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="fp">Trade partner</label>
          <select id="fp" className="select" style={{ width: 200 }} value={partner} onChange={(e) => setPartner(e.target.value)}>
            <option value="">Any team</option>
            {teams.filter((t) => t.id !== team).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </div>
        <button className="btn accent" onClick={() => setRun((n) => n + 1)} disabled={q.isFetching}><Search /> {q.isFetching ? "Searching…" : "Find trades"}</button>
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Screens every package by rest-of-season value, then fully simulates the most promising ones for both teams. Only trades our model expects the partner to accept
        (their projection doesn't fall more than 0.4%) are listed, ranked by your gain.
      </p>
      {q.error && <ErrorBox error={q.error} />}
      {q.isFetching && <LoadingBlock rows={6} />}
      {q.data && q.data.suggestions.length === 0 && <div className="panel"><Empty title="No mutually beneficial trades found">Try a bigger package size, or a different partner.</Empty></div>}
      {q.data && q.data.suggestions.length > 0 && (
        <section className="panel">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Partner</th><th>You give</th><th>You get</th><th className="n">You / wk</th><th className="n hide-sm">You ROS</th><th className="n">Them</th><th className="hide-sm">Fit</th><th /></tr>
              </thead>
              <tbody>
                {q.data.suggestions.map((s, i) => (
                  <tr key={i} className="rise" style={{ "--i": i } as React.CSSProperties}>
                    <td className="strong">{s.partner_name}</td>
                    <td style={{ whiteSpace: "normal" }}>{s.give_names.join(", ")}</td>
                    <td style={{ whiteSpace: "normal" }} className="strong">{s.get_names.join(", ")}</td>
                    <td className="n strong"><Delta value={s.my_delta_per_week} /></td>
                    <td className="n hide-sm">{signedPct(s.my_delta_pct)}</td>
                    <td className="n">{signedPct(s.partner_delta_pct)}</td>
                    <td className="hide-sm"><span className={`badge ${s.fairness.label === "win-win" ? "pos" : ""}`}>{s.fairness.label}</span></td>
                    <td><button className="btn secondary sm" onClick={() => onLoad({ a: team, b: s.partner_id, aGives: s.give, bGives: s.get })}>Analyze</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

interface Post {
  id: number;
  author: string;
  author_id: string;
  team_a: number;
  team_b: number;
  a_gives: string[];
  b_gives: string[];
  note: string;
  evaluation: TradeEval;
  created_at: string;
}

function Board({ onLoad }: { onLoad: (d: Draft) => void }) {
  const { league, teamName } = useLeagueCtx();
  const { user } = useAuth();
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["league", league.id, "board"],
    queryFn: () => api<Post[]>(`/api/leagues/${league.id}/trades/board`),
  });
  const del = useMutation({
    mutationFn: (id: number) => api(`/api/leagues/${league.id}/trades/board/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["league", league.id, "board"] }),
  });
  if (isLoading) return <LoadingBlock rows={6} />;
  if (error) return <ErrorBox error={error} />;
  if (!data?.length) return <div className="panel"><Empty title="No trades shared yet">Evaluate a trade and share it here to get the league talking. Numbers are frozen at the time of posting.</Empty></div>;
  return (
    <div className="stack" style={{ gap: 14 }}>
      {data.map((p) => {
        const names = (ids: string[]) => ids.map((id) => p.evaluation.players.find((x) => x.player_id === id)?.name ?? id).join(", ") || "nothing";
        const [a, b] = p.evaluation.sides;
        return (
          <article key={p.id} className="panel">
            <div className="panel-body stack" style={{ gap: 10 }}>
              <div className="row between wrap">
                <span className="small muted">{p.author} · {relTime(p.created_at)}</span>
                <div className="row">
                  <button className="btn secondary sm" onClick={() => onLoad({ a: p.team_a, b: p.team_b, aGives: p.a_gives, bGives: p.b_gives })}>Re-run with today's data</button>
                  {(p.author_id === user?.id || league.me?.role === "owner") && (
                    <button className="btn ghost sm icon" aria-label="Delete post" onClick={() => del.mutate(p.id)}><Trash2 /></button>
                  )}
                </div>
              </div>
              <div className="h3">
                {teamName(p.team_a)} sends <strong>{names(p.a_gives)}</strong> to {teamName(p.team_b)} for <strong>{names(p.b_gives)}</strong>
              </div>
              {p.note && <p className="small" style={{ margin: 0 }}>“{p.note}”</p>}
              <div className="row wrap">
                {[a, b].map((s) => (
                  <span key={s.team_id} className={`badge ${s.verdict === "advantageous" ? "pos" : s.verdict === "disadvantageous" ? "neg" : ""}`}>
                    {s.team_name}: {s.delta_per_week > 0 ? "+" : ""}{fmt1(s.delta_per_week)}/wk
                  </span>
                ))}
                <span className="badge ink">{p.evaluation.fairness.label}</span>
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}
