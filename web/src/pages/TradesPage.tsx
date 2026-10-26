import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { useLeagueCtx } from "../components/Layout";
import { Delta, Empty, ErrorBox, Injury, LoadingBlock, Notice, PageHead, Panel, PlayerName, Segmented, SubTabs, Tag } from "../components/ui";
import { askPath } from "../lib/ai";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { count, fmt0, fmt1, pct, relTime, signed, signedPct } from "../lib/format";
import { useActivity, useAiStatus, useTeam } from "../lib/queries";
import { useToast } from "../lib/toast";
import type { ThreeTeamSuggestion, TradeAsks, TradeEval, TradeMove, TradeOffer, TradeOffers, TradeSide, TradeSuggestion } from "../lib/types";

type Tab = "analyze" | "find" | "target" | "shop" | "board";
type Side = "a" | "b" | "c";
interface Draft {
  a: number | null;
  b: number | null;
  c: number | null; // set only for a three-team trade
  aGives: string[];
  bGives: string[];
  cGives: string[];
  to: Partial<Record<string, Side>>; // three-team trades: the side that gets each player (default: the next one)
}

const GIVES = { a: "aGives", b: "bGives", c: "cGives" } as const;
const NEXT: Record<Side, Side> = { a: "b", b: "c", c: "a" };
const sidesOf = (d: Draft): Side[] => (d.c == null ? ["a", "b"] : ["a", "b", "c"]);
const destOf = (d: Draft, s: Side, id: string): Side => d.to[id] ?? NEXT[s];

const twoTeam = (a: number | null, b: number | null, aGives: string[], bGives: string[]): Draft =>
  ({ a, b, c: null, aGives, bGives, cGives: [], to: {} });

/** A draft from a saved trade: its teams in order and every player who changes teams. */
function draftFromMoves(teams: number[], moves: TradeMove[]): Draft {
  const side = (team: number) => (["a", "b", "c"] as const)[teams.indexOf(team)]!;
  const gives = (s: Side) => moves.filter((m) => side(m.from_team) === s).map((m) => m.player_id);
  return {
    a: teams[0] ?? null, b: teams[1] ?? null, c: teams[2] ?? null, aGives: gives("a"), bGives: gives("b"), cGives: gives("c"),
    to: teams.length > 2 ? Object.fromEntries(moves.map((m) => [m.player_id, side(m.to_team)])) : {},
  };
}

function tradeBody(d: Draft) {
  const body = { team_a: d.a, team_b: d.b, a_gives: d.aGives, b_gives: d.bGives };
  if (d.c == null) return body;
  const sendTo = Object.fromEntries(sidesOf(d).flatMap((s) => d[GIVES[s]].map((id) => [id, d[destOf(d, s, id)]])));
  return { ...body, team_c: d.c, c_gives: d.cGives, send_to: sendTo };
}

const verdictTone = (v: TradeSide["verdict"]) => (v === "advantageous" ? "up" : v === "disadvantageous" ? "down" : undefined);

export default function TradesPage() {
  const { myTeamId, teams } = useLeagueCtx();
  // The tool lives in the URL so Back (or reopening the app) returns to the same one.
  const [search, setSearch] = useSearchParams();
  const tab = (search.get("tab") as Tab) || "analyze";
  const setTab = (t: Tab) => {
    const next = new URLSearchParams(search);
    next.set("tab", t);
    setSearch(next, { replace: true });
  };
  const [autoRun, setAutoRun] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => twoTeam(
    myTeamId ?? teams[0]?.id ?? null,
    teams.find((t) => t.id !== (myTeamId ?? teams[0]?.id))?.id ?? null,
    [],
    [],
  ));
  const load = (d: Draft) => {
    setDraft(d);
    setAutoRun(true);
    setTab("analyze");
  };
  return (
    <>
      <PageHead title="Trades"
        desc="Trades are judged on each team's optimal daily lineups for the rest of the season (positions, schedule overlap, depth, and the roster spot a trade frees or costs), then on a season simulation." />
      <div className="stack" style={{ gap: 0 }}>
        <SubTabs label="Trade tools" value={tab} onChange={setTab} tabs={[
          { value: "analyze", label: "Analyze" },
          { value: "find", label: "Find trades" },
          { value: "target", label: "Target a player" },
          { value: "shop", label: "Shop a player" },
          { value: "board", label: "League board" },
        ]} />
        <div style={{ paddingTop: 10 }}>
          {tab === "analyze" && <Analyzer draft={draft} setDraft={setDraft} autoRun={autoRun} />}
          {tab === "find" && <Finder onLoad={load} />}
          {tab === "target" && <Targeter key={search.get("target") ?? ""} onLoad={load} />}
          {tab === "shop" && <Shopper key={search.get("shop") ?? ""} onLoad={load} />}
          {tab === "board" && <Board onLoad={load} />}
        </div>
      </div>
    </>
  );
}

function RosterPicker({ title, label, teamId, picked, onToggle, onTeam, others, dest }: {
  title: string;
  label: string;
  teamId: number | null;
  picked: string[];
  onToggle: (id: string) => void;
  onTeam: (id: number) => void;
  others: (number | null)[]; // the other teams in the trade
  // Three-team trades: where each picked player goes (replaces the Pos and ROS columns to save room).
  dest?: { options: { side: Side; name: string }[]; of: (id: string) => Side; set: (id: string, to: Side) => void };
}) {
  const { league, teams, myTeamId } = useLeagueCtx();
  const { data, isLoading } = useTeam(league.id, teamId);
  return (
    <Panel title={title} flush actions={
      <select className="select" value={teamId ?? ""} onChange={(e) => onTeam(Number(e.target.value))} aria-label={label}>
        {teams.map((t) => <option key={t.id} value={t.id} disabled={others.includes(t.id)}>{t.name}{t.id === myTeamId ? " (you)" : ""}</option>)}
      </select>
    }>
      {isLoading && <div style={{ padding: 10 }}><LoadingBlock rows={6} /></div>}
      {data && (
        <div className="table-wrap pick-list" style={{ border: 0, borderTop: "1px solid var(--border)" }}>
          <table className="data">
            <thead>
              <tr>
                <th style={{ width: 28 }}><span className="sr-only">Include</span></th><th>Player</th>
                {!dest && <th className="hide-sm">Pos</th>}<th className="n">FP/G</th>{dest ? <th>To</th> : <th className="n">ROS</th>}
              </tr>
            </thead>
            <tbody>
              {data.roster.map((p) => {
                const on = picked.includes(p.id);
                return (
                  <tr key={p.id} className={`clickable ${on ? "selected" : ""}`} onClick={() => onToggle(p.id)}>
                    <td><input type="checkbox" checked={on} onChange={() => onToggle(p.id)} onClick={(e) => e.stopPropagation()} aria-label={`Include ${p.name}`} /></td>
                    <td>{p.name}<Injury status={p.injury_status} /></td>
                    {!dest && <td className="hide-sm muted">{p.positions.join(", ")}</td>}
                    <td className="n">{fmt1(p.fp_pg)}</td>
                    {dest ? (
                      <td onClick={(e) => e.stopPropagation()}>
                        {on && (
                          <select className="select" style={{ maxWidth: 130 }} value={dest.of(p.id)} aria-label={`Team that gets ${p.name}`}
                            onChange={(e) => dest.set(p.id, e.target.value as Side)}>
                            {dest.options.map((o) => <option key={o.side} value={o.side}>{o.name}</option>)}
                          </select>
                        )}
                      </td>
                    ) : <td className="n muted">{fmt0(p.ros_value)}</td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function tradeQuestion(ev: TradeEval): string {
  const name = (id: string) => ev.players.find((p) => p.player_id === id)?.name ?? id;
  const ask = " Who wins it and why, what are the risks, and is there a better version of this deal?";
  if (ev.sides.length > 2) {
    const team = (id: number) => ev.sides.find((s) => s.team_id === id)?.team_name ?? `Team ${id}`;
    const sends = ev.sides.map((s) => `${s.team_name} sends ${(ev.moves ?? []).filter((m) => m.from_team === s.team_id)
      .map((m) => `${name(m.player_id)} to ${team(m.to_team)}`).join(" and ") || "nobody"}`);
    return `Explain this three-team trade: ${sends.join("; ")}.${ask}`;
  }
  const [a, b] = ev.sides;
  return `Explain this trade: ${a.team_name} gives ${a.gives.map(name).join(", ") || "nothing"} to ${b.team_name} for `
    + `${a.receives.map(name).join(", ") || "nothing"}.${ask}`;
}

function Analyzer({ draft, setDraft, autoRun }: { draft: Draft; setDraft: (d: Draft) => void; autoRun: boolean }) {
  const { league, teams, teamName } = useLeagueCtx();
  const ai = useAiStatus();
  const toast = useToast();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  // The evaluated trade is whatever was last submitted; editing the selection marks results as stale.
  const [submitted, setSubmitted] = useState<Draft | null>(autoRun ? draft : null);
  const evaluate = useQuery({
    queryKey: ["league", league.id, "evaluate", submitted],
    queryFn: () => api<TradeEval>(`/api/leagues/${league.id}/trades/evaluate`, { body: tradeBody(submitted!) }),
    enabled: submitted != null,
    staleTime: 5 * 60_000,
  });
  const stale = submitted != null && JSON.stringify(submitted) !== JSON.stringify(draft);
  const share = useMutation({
    mutationFn: () => api(`/api/leagues/${league.id}/trades/board`, { body: { ...tradeBody(submitted!), note } }),
    onSuccess: () => {
      toast("Trade posted to the league board.");
      setNote("");
      qc.invalidateQueries({ queryKey: ["league", league.id, "board"] });
    },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const three = draft.c != null;
  const sides = sidesOf(draft);
  const toggle = (s: Side, id: string) => {
    const list = draft[GIVES[s]];
    const to = { ...draft.to };
    delete to[id];
    setDraft({ ...draft, [GIVES[s]]: list.includes(id) ? list.filter((x) => x !== id) : [...list, id], to });
  };
  // Switching to three teams adds the first team not already in the trade; going back to two drops it.
  const setMode = (n: "2" | "3") => setDraft({
    ...draft, c: n === "3" ? teams.find((t) => t.id !== draft.a && t.id !== draft.b)?.id ?? null : null, cGives: [], to: {},
  });
  const moving = sides.flatMap((s) => draft[GIVES[s]]);
  const gets = (s: Side) => sides.some((o) => draft[GIVES[o]].some((id) => destOf(draft, o, id) === s));
  const idle = three ? sides.filter((s) => !draft[GIVES[s]].length && !gets(s)) : [];
  const ids = sides.map((s) => draft[s]);
  const ready = ids.every((t) => t != null) && new Set(ids).size === ids.length && moving.length > 0 && !idle.length;

  return (
    <div className="stack">
      {teams.length >= 3 && (
        <div className="toolbar">
          <Segmented label="Teams in the trade" value={three ? "3" : "2"} onChange={setMode}
            options={[{ value: "2", label: "2 teams" }, { value: "3", label: "3 teams" }]} />
          {three && <span className="small muted">Tick the players each team gives, then pick which team gets each one.</span>}
        </div>
      )}
      <div className={`grid ${three ? "cols-3" : "cols-2"}`} style={{ alignItems: "start" }}>
        {sides.map((s) => {
          const others = sides.filter((o) => o !== s);
          return (
            <RosterPicker key={s} title={`Team ${s.toUpperCase()} gives`} label={`Team ${s.toUpperCase()}`} teamId={draft[s]} picked={draft[GIVES[s]]} onToggle={(id) => toggle(s, id)}
              others={others.map((o) => draft[o])} onTeam={(id) => setDraft({ ...draft, [s]: id, [GIVES[s]]: [] })}
              dest={three ? {
                options: others.map((o) => ({ side: o, name: teamName(draft[o]) })),
                of: (id) => destOf(draft, s, id),
                set: (id, to) => setDraft({ ...draft, to: { ...draft.to, [id]: to } }),
              } : undefined} />
          );
        })}
      </div>
      <div className="toolbar">
        <button className="btn primary" disabled={!ready || evaluate.isFetching} onClick={() => setSubmitted({ ...draft })}>
          {evaluate.isFetching ? "Evaluating…" : "Evaluate trade"}
        </button>
        <span className="small muted">
          {!three && `${draft.aGives.length} for ${draft.bGives.length}. Uneven trades drop the weakest player or pick up a free agent.`}
          {three && (idle.length && moving.length
            ? `${teamName(draft[idle[0]!])} has to send or get at least one player.`
            : `${moving.length} player${moving.length === 1 ? "" : "s"} changing teams. A team that ends up with extra players drops its weakest; an open spot goes to a free agent.`)}
        </span>
      </div>
      {evaluate.error && <ErrorBox error={evaluate.error} />}
      {evaluate.isFetching && !evaluate.data && <LoadingBlock rows={6} />}
      {stale && evaluate.data && <Notice level="warn">Selection changed since this evaluation. Evaluate again to update the numbers.</Notice>}
      {evaluate.data && (
        <>
          <TradeResult ev={evaluate.data} />
          {ai.data?.enabled && (
            <div className="toolbar">
              <Link className="btn" to={askPath(league.id, { prompt: tradeQuestion(evaluate.data) })}>Ask AI about this trade</Link>
              <span className="small muted">Get a plain-English read on the numbers, the risks, and a message to send.</span>
            </div>
          )}
          <Panel title="Post to league board">
            <div className="row wrap">
              <input className="input" style={{ flex: 1, minWidth: 220 }} placeholder="Note (optional)" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
              <button className="btn" onClick={() => share.mutate()} disabled={share.isPending}>Post</button>
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}

function SidePanel({ s }: { s: TradeSide }) {
  return (
    <Panel title={s.team_name} actions={<Tag tone={verdictTone(s.verdict)}>{s.verdict}</Tag>}>
      <div className="kpi" style={{ marginBottom: 8 }}>
        <span className="k">Change per week</span>
        <span className={`v ${s.delta_per_week > 0 ? "up" : s.delta_per_week < 0 ? "down" : ""}`}>{s.delta_per_week > 0 ? "+" : ""}{fmt1(s.delta_per_week)}</span>
      </div>
      <dl className="kv">
        <dt>Rest of season</dt><dd><Delta value={s.delta_ros} digits={0} /> ({signedPct(s.delta_pct)})</dd>
        <dt>Fantasy playoff weeks</dt><dd><Delta value={s.delta_playoffs} digits={0} /></dd>
        <dt>Raw value change</dt><dd><Delta value={s.raw_value_delta} digits={0} /></dd>
        {s.before_playoff_odds != null && (
          <>
            <dt>Playoff odds</dt><dd>{pct(s.before_playoff_odds)} → {pct(s.after_playoff_odds)}</dd>
            <dt>Title odds</dt><dd>{pct(s.before_title_odds)} → {pct(s.after_title_odds)}</dd>
          </>
        )}
      </dl>
    </Panel>
  );
}

export function TradeResult({ ev }: { ev: TradeEval }) {
  const { openPlayer, teamName } = useLeagueCtx();
  const [, ...partners] = ev.sides;
  const three = ev.sides.length > 2;
  const cols = three ? "cols-3" : "cols-2";
  // Three-team trades: where each player ends up (traded, picked up to fill a spot, or dropped).
  const destination = (id: string) => {
    const move = ev.moves?.find((m) => m.player_id === id);
    const adder = ev.sides.find((s) => s.auto_adds.includes(id));
    return move ? teamName(move.to_team) : adder ? teamName(adder.team_id) : "Dropped";
  };
  return (
    <div className="stack">
      <div className={`grid ${cols}`} style={{ alignItems: "start" }}>
        {ev.sides.map((s) => <SidePanel key={s.team_id} s={s} />)}
      </div>
      <Notice>
        <strong>{ev.fairness.label}.</strong> {ev.summary} Chance {partners.map((p) => p.team_name).join(" and ")} {three ? "both see" : "sees"} it
        as a gain: {pct(ev.fairness.partner_accept_likelihood)}.
      </Notice>
      <div className={`grid ${cols}`} style={{ alignItems: "start" }}>
        {ev.math.map((m) => (
          <Panel key={m.team_id} title={`Calculation: ${m.team_name}`}>
            <ol className="steps">{m.lines.map((l, i) => <li key={i}>{l}</li>)}</ol>
          </Panel>
        ))}
      </div>
      <Panel title="Players involved" flush>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th>Player</th><th className="hide-sm">From</th>{three && <th className="hide-sm">To</th>}<th className="hide-sm">Pos</th><th className="n">FP/G</th><th className="n">± game</th><th className="n">ROS games</th><th className="n">ROS pts</th><th className="n">Over repl.</th><th className="n hide-sm">Last 10</th></tr>
            </thead>
            <tbody>
              {ev.players.map((p) => (
                <tr key={p.player_id}>
                  <td><PlayerName name={p.name} injury={p.injury_status} onOpen={() => openPlayer(p.player_id)} sub={p.games_out > 0 ? `out ~${fmt0(p.games_out)}g` : undefined} /></td>
                  <td className="hide-sm muted">{teamName(p.fantasy_team_id)}</td>
                  {three && <td className="hide-sm muted">{destination(p.player_id)}</td>}
                  <td className="hide-sm muted">{p.pro_team} · {p.positions.join(", ")}</td>
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

// Package sizes the finder searches (give-get). Even trades are the default; uneven ones are opt-in.
const PACKAGES: Record<string, { label: string; sizes: string }> = {
  even: { label: "1-for-1 and 2-for-2", sizes: "1-1,2-2" },
  "1-1": { label: "1-for-1 only", sizes: "1-1" },
  "2-2": { label: "2-for-2 only", sizes: "2-2" },
  "3-3": { label: "3-for-3 only", sizes: "3-3" },
  "2-1": { label: "Give 2, get 1", sizes: "2-1" },
  "1-2": { label: "Give 1, get 2", sizes: "1-2" },
  any: { label: "Any size up to 2-for-2", sizes: "1-1,1-2,2-1,2-2" },
};

function Finder({ onLoad }: { onLoad: (d: Draft) => void }) {
  const { teams } = useLeagueCtx();
  const [mode, setMode] = useState<"2" | "3">("2");
  return (
    <div className="stack">
      {teams.length >= 3 && (
        <Segmented label="Teams in the trade" value={mode} onChange={setMode}
          options={[{ value: "2", label: "2 teams" }, { value: "3", label: "3 teams" }]} />
      )}
      {mode === "2" ? <TwoTeamFinder onLoad={onLoad} /> : <ThreeTeamFinder onLoad={onLoad} />}
    </div>
  );
}

function TwoTeamFinder({ onLoad }: { onLoad: (d: Draft) => void }) {
  const { league, myTeamId, teams } = useLeagueCtx();
  const [team, setTeam] = useState<number | null>(myTeamId ?? teams[0]?.id ?? null);
  const [shape, setShape] = useState("even");
  const [partner, setPartner] = useState("");
  const [run, setRun] = useState(0);
  const sizes = PACKAGES[shape]!.sizes;
  const q = useQuery({
    queryKey: ["league", league.id, "suggest", team, shape, partner, run],
    queryFn: () => api<{ suggestions: TradeSuggestion[]; checked?: Record<string, number> }>(
      `/api/leagues/${league.id}/trades/suggestions?team_id=${team}&sizes=${sizes}${partner ? `&partner_id=${partner}` : ""}`),
    enabled: run > 0 && team != null,
    staleTime: 5 * 60_000,
  });
  return (
    <div className="stack">
      <div className="toolbar">
        <label className="field-inline"><span className="label">For</span>
          <select className="select" value={team ?? ""} onChange={(e) => setTeam(Number(e.target.value))}>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.name}{t.id === myTeamId ? " (you)" : ""}</option>)}
          </select>
        </label>
        <label className="field-inline"><span className="label">Package</span>
          <select className="select" value={shape} onChange={(e) => setShape(e.target.value)}>
            {Object.entries(PACKAGES).map(([value, p]) => <option key={value} value={value}>{p.label}</option>)}
          </select>
        </label>
        <label className="field-inline"><span className="label">Partner</span>
          <select className="select" value={partner} onChange={(e) => setPartner(e.target.value)}>
            <option value="">Any team</option>
            {teams.filter((t) => t.id !== team).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
        <button className="btn primary" onClick={() => setRun((n) => n + 1)} disabled={q.isFetching}>{q.isFetching ? "Searching…" : "Search"}</button>
      </div>
      <p className="small muted">
        Screens every package by rest-of-season value, then fully simulates the most promising for both teams. Listed only if the partner's projection doesn't fall more than 0.4%; ranked by your gain.
      </p>
      {q.error && <ErrorBox error={q.error} />}
      {q.isFetching && <LoadingBlock rows={6} />}
      {q.data && q.data.suggestions.length === 0 && <Panel><Empty title="No mutually acceptable trades found">Try a larger package or a different partner.</Empty></Panel>}
      {q.data && (["two", "three"] as const).map((word, i) => {
        const n = i + 2;
        const simulated = q.data.checked?.[`${n}-for-${n}`] ?? 0;
        if (!sizes.includes(`${n}-${n}`) || !simulated || q.data.suggestions.some((s) => s.give.length === n && s.get.length === n)) return null;
        return (
          <p key={n} className="small muted">
            Fully simulated {simulated} {word}-for-{word} packages. None helped you without costing the other team more than 0.4%, so none are listed.
          </p>
        );
      })}
      {q.data && q.data.suggestions.length > 0 && (
        <Panel flush>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Partner</th><th>You give</th><th>You get</th><th className="n">You/wk</th><th className="n hide-sm">You ROS</th><th className="n">Partner</th><th className="hide-sm">Fit</th><th /></tr>
              </thead>
              <tbody>
                {q.data.suggestions.map((s, i) => (
                  <tr key={i}>
                    <td>{s.partner_name}</td>
                    <td className="wrap">{s.give_names.join(", ")}</td>
                    <td className="wrap">{s.get_names.join(", ")}</td>
                    <td className="n"><Delta value={s.my_delta_per_week} /></td>
                    <td className="n hide-sm">{signedPct(s.my_delta_pct)}</td>
                    <td className="n">{signedPct(s.partner_delta_pct)}</td>
                    <td className="hide-sm"><Tag tone={s.fairness.label === "win-win" ? "up" : undefined}>{s.fairness.label}</Tag></td>
                    <td className="n"><button className="btn" onClick={() => onLoad(twoTeam(team, s.partner_id, s.give, s.get))}>Analyze</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}

/** Three-team rotations: you send one player to a team, it sends one on to a third, and that team sends one back. */
function ThreeTeamFinder({ onLoad }: { onLoad: (d: Draft) => void }) {
  const { league, myTeamId, teams, teamName } = useLeagueCtx();
  const [team, setTeam] = useState<number | null>(myTeamId ?? teams[0]?.id ?? null);
  const [partner, setPartner] = useState("");
  const [run, setRun] = useState(0);
  const q = useQuery({
    queryKey: ["league", league.id, "three-team", team, partner, run],
    queryFn: () => api<{ suggestions: ThreeTeamSuggestion[]; checked?: Record<string, number> }>(
      `/api/leagues/${league.id}/trades/three-team?team_id=${team}${partner ? `&partner_id=${partner}` : ""}`),
    enabled: run > 0 && team != null,
    staleTime: 5 * 60_000,
  });
  return (
    <div className="stack">
      <div className="toolbar">
        <label className="field-inline"><span className="label">For</span>
          <select className="select" value={team ?? ""} onChange={(e) => { setTeam(Number(e.target.value)); if (e.target.value === partner) setPartner(""); }}>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.name}{t.id === myTeamId ? " (you)" : ""}</option>)}
          </select>
        </label>
        <label className="field-inline"><span className="label">Including</span>
          <select className="select" value={partner} onChange={(e) => setPartner(e.target.value)}>
            <option value="">Any two teams</option>
            {teams.filter((t) => t.id !== team).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
        <button className="btn primary" onClick={() => setRun((n) => n + 1)} disabled={q.isFetching}>{q.isFetching ? "Searching…" : "Search"}</button>
      </div>
      <p className="small muted">
        You send a player to one team, that team sends one on to a third, and the third sends one back to you, so each team gives one player and gets one. This finds deals a two-team trade can't, like when the team with the player you want doesn't want anyone of yours. Screens every rotation by rest-of-season value, fully simulates the most promising for all three teams, and lists those where neither other team's projection falls more than 0.4%, ranked by your gain.
      </p>
      {q.error && <ErrorBox error={q.error} />}
      {q.isFetching && <LoadingBlock rows={6} />}
      {q.data && !q.isFetching && q.data.suggestions.length === 0 && (
        <Panel><Empty title="No three-team trade works for all three teams">
          {q.data.checked?.["3-team"]
            ? `Fully simulated ${count(q.data.checked["3-team"], "rotation")}. None helped you without costing another team more than 0.4%.`
            : "Try including another team, or search two-team trades."}
        </Empty></Panel>
      )}
      {q.data && !q.isFetching && q.data.suggestions.length > 0 && (
        <Panel flush>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>You send</th><th>They pass on</th><th>You get</th><th className="n">You/wk</th><th className="n hide-sm">You ROS</th><th className="n">Partners</th><th /></tr>
              </thead>
              <tbody>
                {q.data.suggestions.map((r) => {
                  const [, p, q3] = r.teams as [number, number, number];
                  const middle = r.moves.find((m) => m.from_team === p);
                  return (
                    <tr key={r.moves.map((m) => m.player_id).join()}>
                      <td className="wrap">{r.give_names[0]} <span className="muted">to {teamName(p)}</span></td>
                      <td className="wrap">{middle?.name} <span className="muted">{teamName(p)} to {teamName(q3)}</span></td>
                      <td className="wrap">{r.get_names[0]} <span className="muted">from {teamName(q3)}</span></td>
                      <td className="n"><Delta value={r.my_delta_per_week} /></td>
                      <td className="n hide-sm">{signedPct(r.my_delta_pct)}</td>
                      <td className="n" title={r.partners.map((s) => `${s.team_name}: ${signedPct(s.delta_pct)}`).join("\n")}>
                        {r.partners.map((s) => signedPct(s.delta_pct)).join(" / ")}
                      </td>
                      <td className="n"><button className="btn" onClick={() => onLoad(draftFromMoves(r.teams, r.moves))}>Analyze</button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}

// How many of your players an offer may include (the offers endpoint's give=).
const OFFER_SIZES: Record<string, { label: string; give: string }> = {
  "3": { label: "Up to 3 players", give: "1,2,3" },
  "2": { label: "Up to 2 players", give: "1,2" },
  "1": { label: "1 player", give: "1" },
};
const MAX_TARGETS = 3;

interface TargetSearch {
  team: number | null;
  from: number | null;
  targets: string[];
  size: string;
}

const andList = (names: string[]) => (names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0] ?? "");

/** Pick players on another team; lists what to offer for them. */
function Targeter({ onLoad }: { onLoad: (d: Draft) => void }) {
  const { league, myTeamId, teams } = useLeagueCtx();
  const toast = useToast();
  // A player's card links here with ?target=<player>&from=<their team> and searches straight away.
  const [search] = useSearchParams();
  const linked = search.get("target");
  const linkedFrom = Number(search.get("from")) || null;
  const [team, setTeam] = useState<number | null>(myTeamId ?? teams.find((t) => t.id !== linkedFrom)?.id ?? null);
  const [from, setFrom] = useState<number | null>(linkedFrom ?? teams.find((t) => t.id !== team)?.id ?? null);
  const [targets, setTargets] = useState<string[]>(linked && linkedFrom ? [linked] : []);
  const [size, setSize] = useState("3");
  const current: TargetSearch = { team, from, targets, size };
  const ready = team != null && from != null && team !== from && targets.length > 0;
  // The list shows whatever was last searched; changing the picks marks it as out of date.
  const [submitted, setSubmitted] = useState<TargetSearch | null>(ready ? current : null);
  const q = useQuery({
    queryKey: ["league", league.id, "offers", submitted],
    queryFn: () => {
      const params = new URLSearchParams({ team_id: String(submitted!.team), give: OFFER_SIZES[submitted!.size]!.give });
      submitted!.targets.forEach((id) => params.append("target", id));
      return api<TradeOffers>(`/api/leagues/${league.id}/trades/offers?${params}`);
    },
    enabled: submitted != null,
    staleTime: 5 * 60_000,
  });
  const stale = submitted != null && JSON.stringify(submitted) !== JSON.stringify(current);
  const toggle = (id: string) => {
    if (targets.includes(id)) setTargets(targets.filter((x) => x !== id));
    else if (targets.length >= MAX_TARGETS) toast(`You can target up to ${MAX_TARGETS} players at once. Untick one to pick someone else.`, "err");
    else setTargets([...targets, id]);
  };
  return (
    <div className="stack">
      <div className="toolbar">
        <label className="field-inline"><span className="label">For</span>
          <select className="select" value={team ?? ""} onChange={(e) => setTeam(Number(e.target.value))}>
            {teams.map((t) => <option key={t.id} value={t.id} disabled={t.id === from}>{t.name}{t.id === myTeamId ? " (you)" : ""}</option>)}
          </select>
        </label>
        <label className="field-inline"><span className="label">Offer</span>
          <select className="select" value={size} onChange={(e) => setSize(e.target.value)}>
            {Object.entries(OFFER_SIZES).map(([value, o]) => <option key={value} value={value}>{o.label}</option>)}
          </select>
        </label>
        <button className="btn primary" disabled={!ready || q.isFetching} onClick={() => setSubmitted(current)}>
          {q.isFetching ? "Searching…" : "Find offers"}
        </button>
        <span className="small muted">{targets.length ? `${targets.length} of ${MAX_TARGETS} picked` : "Tick the players you want below."}</span>
      </div>
      <RosterPicker title="Players you want" label="Their team" teamId={from} picked={targets} onToggle={toggle} others={[team]}
        onTeam={(id) => { setFrom(id); setTargets([]); }} />
      <p className="small muted">
        Tries every package of your players for them, fully simulates the most promising for both teams, and lists the offers the other team gains from or loses at most 0.4% on, best for you first. Offering more players than you get opens roster spots, filled from free agents.
      </p>
      {q.error && <ErrorBox error={q.error} />}
      {q.isFetching && !q.data && <LoadingBlock rows={6} />}
      {stale && q.data && <Notice level="warn">Your picks changed since this search. Find offers again to update the list.</Notice>}
      {q.data && <OfferResults data={q.data} team={submitted!.team!} onLoad={onLoad} />}
    </div>
  );
}

function OfferResults({ data, team, onLoad }: { data: TradeOffers; team: number; onLoad: (d: Draft) => void }) {
  const { league } = useLeagueCtx();
  const ai = useAiStatus();
  const want = andList(data.get_names);
  const analyze = (o: TradeOffer) => onLoad(twoTeam(team, data.partner_id, o.give, data.get));
  const best = data.offers[0];
  const askAi = ai.data?.enabled && (
    <div className="toolbar">
      <Link className="btn" to={askPath(league.id, { prompt: `What should I offer ${data.partner_name} for ${want}? Compare the best offers, say which one to send first, and write a short message I could send them.` })}>Ask AI about these offers</Link>
    </div>
  );
  if (!best) {
    return (
      <div className="stack">
        <Panel>
          <Empty title={`No offer ${data.partner_name} should accept for ${want}`}>
            {data.closest.length ? "These came closest, but the other team loses more than 0.4% on each. Try offering more players, or target someone else." : "Try offering more players, or target someone else."}
          </Empty>
        </Panel>
        {data.closest.length > 0 && <OfferTable title="Closest offers" offers={data.closest} show="give" onAnalyze={analyze} />}
        {askAi}
      </div>
    );
  }
  return (
    <div className="stack">
      <Notice>
        <strong>Best offer for {want}:</strong> {andList(best.give_names)}. Your team: {signed(best.my_delta_per_week)} per week ({signedPct(best.my_delta_pct)} rest of season). {data.partner_name}: {signedPct(best.partner_delta_pct)}.
      </Notice>
      {[1, 2, 3].map((n) => {
        const group = data.offers.filter((o) => o.give.length === n);
        return group.length > 0 && <OfferTable key={n} title={`Offer ${count(n, "player")}`} offers={group} show="give" onAnalyze={analyze} />;
      })}
      {askAi}
    </div>
  );
}

/** Two-team trades with one side fixed: the other side (what you give, or what you get), and the team if they vary. */
function OfferTable({ title, offers, show, withTeam = false, onAnalyze }: {
  title: string;
  offers: TradeOffer[];
  show: "give" | "get";
  withTeam?: boolean;
  onAnalyze: (o: TradeOffer) => void;
}) {
  return (
    <Panel title={title} flush>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              {withTeam && <th>From</th>}<th style={{ width: withTeam ? "35%" : "45%" }}>{show === "give" ? "You give" : "You get"}</th>
              <th className="n">You/wk</th><th className="n hide-sm">You ROS</th><th className="n">Partner</th><th className="hide-sm">Fit</th><th />
            </tr>
          </thead>
          <tbody>
            {offers.map((o) => (
              <tr key={`${o.partner_id}:${o.give.join()}:${o.get.join()}`}>
                {withTeam && <td className="wrap">{o.partner_name}</td>}
                <td className="wrap">{(show === "give" ? o.give_names : o.get_names).join(", ")}</td>
                <td className="n"><Delta value={o.my_delta_per_week} /></td>
                <td className="n hide-sm">{signedPct(o.my_delta_pct)}</td>
                <td className="n">{signedPct(o.partner_delta_pct)}</td>
                <td className="hide-sm"><Tag tone={o.fairness.label === "win-win" ? "up" : undefined}>{o.fairness.label}</Tag></td>
                <td className="n"><button className="btn" onClick={() => onAnalyze(o)}>Analyze</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

// How many of the other team's players to ask for (the asks endpoint's get=), given how many you shop. Even
// trades are the default: an uneven ask counts the free agent the other team picks up for the open spot,
// which can make it look fairer on paper than a manager would think, as in Find trades.
const ASK_SIZES: Record<string, { label: string; get: (n: number) => string }> = {
  same: { label: "As many as you give", get: (n) => String(n) },
  one: { label: "1 player", get: () => "1" },
  upTo2: { label: "Up to 2 players", get: () => "1,2" },
  upTo3: { label: "Up to 3 players", get: () => "1,2,3" },
};

interface ShopSearch {
  team: number | null;
  partner: number | null; // null: every other team
  players: string[];
  size: string;
}

/** Pick your own players that someone wants; lists what you can fairly ask for them. */
function Shopper({ onLoad }: { onLoad: (d: Draft) => void }) {
  const { league, myTeamId, teams } = useLeagueCtx();
  const toast = useToast();
  // A player's card links here with ?shop=<player>&team=<their team> and searches every team straight away.
  const [search] = useSearchParams();
  const linked = search.get("shop");
  const linkedTeam = Number(search.get("team")) || null;
  const [team, setTeam] = useState<number | null>(linkedTeam ?? myTeamId ?? teams[0]?.id ?? null);
  const [partner, setPartner] = useState<number | null>(null);
  const [players, setPlayers] = useState<string[]>(linked && linkedTeam ? [linked] : []);
  const [size, setSize] = useState("same");
  const current: ShopSearch = { team, partner, players, size };
  const ready = team != null && team !== partner && players.length > 0;
  // The list shows whatever was last searched; changing the picks marks it as out of date.
  const [submitted, setSubmitted] = useState<ShopSearch | null>(ready ? current : null);
  const q = useQuery({
    queryKey: ["league", league.id, "asks", submitted],
    queryFn: () => {
      const params = new URLSearchParams({ team_id: String(submitted!.team), get: ASK_SIZES[submitted!.size]!.get(submitted!.players.length) });
      if (submitted!.partner != null) params.set("partner_id", String(submitted!.partner));
      submitted!.players.forEach((id) => params.append("player", id));
      return api<TradeAsks>(`/api/leagues/${league.id}/trades/asks?${params}`);
    },
    enabled: submitted != null,
    staleTime: 5 * 60_000,
  });
  const stale = submitted != null && JSON.stringify(submitted) !== JSON.stringify(current);
  const toggle = (id: string) => {
    if (players.includes(id)) setPlayers(players.filter((x) => x !== id));
    else if (players.length >= MAX_TARGETS) toast(`You can shop up to ${MAX_TARGETS} players at once. Untick one to pick someone else.`, "err");
    else setPlayers([...players, id]);
  };
  return (
    <div className="stack">
      <div className="toolbar">
        <label className="field-inline"><span className="label">Team that wants them</span>
          <select className="select" value={partner ?? ""} onChange={(e) => setPartner(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Any team</option>
            {teams.filter((t) => t.id !== team).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
        <label className="field-inline"><span className="label">Ask for</span>
          <select className="select" value={size} onChange={(e) => setSize(e.target.value)}>
            {Object.entries(ASK_SIZES).map(([value, o]) => <option key={value} value={value}>{o.label}</option>)}
          </select>
        </label>
        <button className="btn primary" disabled={!ready || q.isFetching} onClick={() => setSubmitted(current)}>
          {q.isFetching ? "Searching…" : "Find what to ask for"}
        </button>
        <span className="small muted">{players.length ? `${players.length} of ${MAX_TARGETS} picked` : "Tick the players they want below."}</span>
      </div>
      <RosterPicker title="Players they want" label="Your team" teamId={team} picked={players} onToggle={toggle} others={[partner]}
        onTeam={(id) => { setTeam(id); setPlayers([]); if (id === partner) setPartner(null); }} />
      <p className="small muted">
        For when another manager wants your player but won't make an offer. Tries every package of their players for yours, fully simulates the most promising for both teams, and lists the packages their team gains from or loses at most 0.4% on, the most you can ask for first. Asking for more players than you give means dropping your weakest, while the other team fills the open spot from free agents.
      </p>
      {q.error && <ErrorBox error={q.error} />}
      {q.isFetching && !q.data && <LoadingBlock rows={6} />}
      {stale && q.data && <Notice level="warn">Your picks changed since this search. Search again to update the list.</Notice>}
      {q.data && <AskResults data={q.data} team={submitted!.team!} onLoad={onLoad} />}
    </div>
  );
}

function AskResults({ data, team, onLoad }: { data: TradeAsks; team: number; onLoad: (d: Draft) => void }) {
  const { league } = useLeagueCtx();
  const ai = useAiStatus();
  const shopping = andList(data.give_names);
  const everyTeam = data.partner_id == null;
  const analyze = (o: TradeOffer) => onLoad(twoTeam(team, o.partner_id, data.give, o.get));
  const best = data.offers[0];
  const prompt = data.partner_name
    ? `${data.partner_name} wants ${shopping} but won't make an offer. What should I ask them for? Compare the best packages, say which to propose first, and write a short message I could send them.`
    : `Which teams should I shop ${shopping} to, and what should I ask each for? Compare the best packages and write a short message I could send.`;
  const askAi = ai.data?.enabled && (
    <div className="toolbar">
      <Link className="btn" to={askPath(league.id, { prompt })}>Ask AI about these</Link>
    </div>
  );
  if (!best) {
    return (
      <div className="stack">
        <Panel>
          <Empty title={`No fair return for ${shopping}${data.partner_name ? ` from ${data.partner_name}` : ""}`}>
            {data.closest.length
              ? "These came closest, but the other team loses more than 0.4% on each. Try asking for fewer players, or another team."
              : "Everything they'd accept costs you more than 0.4%. Try asking for more players, or another team."}
          </Empty>
        </Panel>
        {data.closest.length > 0 && <OfferTable title="Closest asks" offers={data.closest} show="get" withTeam={everyTeam} onAnalyze={analyze} />}
        {askAi}
      </div>
    );
  }
  return (
    <div className="stack">
      <Notice>
        <strong>Most you can ask for {shopping}:</strong> {andList(best.get_names)}{everyTeam ? ` from ${best.partner_name}` : ""}. Your team: {signed(best.my_delta_per_week)} per week ({signedPct(best.my_delta_pct)} rest of season). {best.partner_name}: {signedPct(best.partner_delta_pct)}.
      </Notice>
      {[1, 2, 3].map((n) => {
        const group = data.offers.filter((o) => o.get.length === n);
        return group.length > 0 && <OfferTable key={n} title={`Ask for ${count(n, "player")}`} offers={group} show="get" withTeam={everyTeam} onAnalyze={analyze} />;
      })}
      {askAi}
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
  teams: number[]; // three for a three-team trade
  moves: TradeMove[];
  note: string;
  // Re-scored after every refresh; status says whether the trade is still possible.
  evaluation: TradeEval & { status?: "open" | "done" | "stale"; status_note?: string | null; as_of?: string };
  created_at: string;
}

/** Real trades and waiver moves found in the league's latest refreshes. */
function RecentMoves() {
  const { league } = useLeagueCtx();
  const { data } = useActivity(league.id);
  const moves = (data ?? []).filter((a) => a.kind === "transaction").slice(0, 8);
  if (!moves.length) return null;
  return (
    <Panel title={`Recent moves${league.source === "espn" ? " on ESPN" : ""}`}>
      <ul className="list">
        {moves.map((m) => (
          <li key={m.id} className="row between">
            <span>{m.message}</span>
            <span className="small muted">{relTime(m.created_at)}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
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
  if (!data?.length) {
    return (
      <div className="stack">
        <RecentMoves />
        <Panel><Empty title="No trades posted yet">Evaluate a trade and post it to share the numbers with the league. They update automatically after every refresh.</Empty></Panel>
      </div>
    );
  }
  const anyThree = data.some((p) => p.teams.length > 2);
  return (
    <div className="stack">
      <RecentMoves />
      <Panel flush note="Numbers update automatically after every refresh, so they always reflect the latest stats and rosters.">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Posted</th><th>Trade</th><th className="n">Team A/wk</th><th className="n">Team B/wk</th>
                {anyThree && <th className="n">Team C/wk</th>}<th className="hide-sm">Note</th><th />
              </tr>
            </thead>
            <tbody>
              {data.map((p) => {
                const name = (id: string) => p.evaluation.players.find((x) => x.player_id === id)?.name ?? id;
                const three = p.teams.length > 2;
                const gives = (team: number) => p.moves.filter((m) => m.from_team === team)
                  .map((m) => (three ? `${name(m.player_id)} → ${teamName(m.to_team)}` : name(m.player_id))).join(", ") || "nothing";
                const [a, b, c] = p.evaluation.sides;
                const status = p.evaluation.status ?? "open";
                return (
                  <tr key={p.id} className={status === "open" ? "" : "muted"}>
                    <td className="muted">{relTime(p.created_at)}<div className="tiny">{p.author}</div></td>
                    <td className="wrap">
                      {p.teams.map((t) => <div key={t}><span className="muted">{teamName(t)}:</span> {gives(t)}</div>)}
                      <div className="tiny">
                        {status === "done" && <Tag tone="up">Happened</Tag>}
                        {status === "stale" && <Tag tone="warn">No longer possible</Tag>}{" "}
                        <span className="faint">
                          {status === "open" ? `Updated ${relTime(p.evaluation.as_of ?? p.created_at)}` : p.evaluation.status_note}
                        </span>
                      </div>
                    </td>
                    <td className="n"><Delta value={a.delta_per_week} /></td>
                    <td className="n"><Delta value={b.delta_per_week} /></td>
                    {anyThree && <td className="n">{c ? <Delta value={c.delta_per_week} /> : <span className="faint">–</span>}</td>}
                    <td className="wrap hide-sm muted">{p.note || "–"}</td>
                    <td className="n">
                      <div className="row" style={{ justifyContent: "flex-end" }}>
                        <button className="btn" onClick={() => onLoad(draftFromMoves(p.teams, p.moves))}>Re-run</button>
                        {(p.author_id === user?.id || league.me?.role === "owner") && (
                          <button className="btn danger" onClick={() => del.mutate(p.id)}>Delete</button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
