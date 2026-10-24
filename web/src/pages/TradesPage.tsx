import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { useLeagueCtx } from "../components/Layout";
import { Delta, Empty, ErrorBox, Injury, LoadingBlock, Notice, PageHead, Panel, PlayerName, SubTabs, Tag } from "../components/ui";
import { askPath } from "../lib/ai";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { count, fmt0, fmt1, pct, relTime, signed, signedPct } from "../lib/format";
import { useActivity, useAiStatus, useTeam } from "../lib/queries";
import { useToast } from "../lib/toast";
import type { TradeEval, TradeOffer, TradeOffers, TradeSide, TradeSuggestion } from "../lib/types";

type Tab = "analyze" | "find" | "target" | "board";
interface Draft {
  a: number | null;
  b: number | null;
  aGives: string[];
  bGives: string[];
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
          { value: "board", label: "League board" },
        ]} />
        <div style={{ paddingTop: 10 }}>
          {tab === "analyze" && <Analyzer draft={draft} setDraft={setDraft} autoRun={autoRun} />}
          {tab === "find" && <Finder onLoad={load} />}
          {tab === "target" && <Targeter key={search.get("target") ?? ""} onLoad={load} />}
          {tab === "board" && <Board onLoad={load} />}
        </div>
      </div>
    </>
  );
}

function RosterPicker({ title, label, teamId, picked, onToggle, onTeam, otherTeam }: {
  title: string;
  label: string;
  teamId: number | null;
  picked: string[];
  onToggle: (id: string) => void;
  onTeam: (id: number) => void;
  otherTeam: number | null;
}) {
  const { league, teams, myTeamId } = useLeagueCtx();
  const { data, isLoading } = useTeam(league.id, teamId);
  return (
    <Panel title={title} flush actions={
      <select className="select" value={teamId ?? ""} onChange={(e) => onTeam(Number(e.target.value))} aria-label={label}>
        {teams.map((t) => <option key={t.id} value={t.id} disabled={t.id === otherTeam}>{t.name}{t.id === myTeamId ? " (you)" : ""}</option>)}
      </select>
    }>
      {isLoading && <div style={{ padding: 10 }}><LoadingBlock rows={6} /></div>}
      {data && (
        <div className="table-wrap pick-list" style={{ border: 0, borderTop: "1px solid var(--border)" }}>
          <table className="data">
            <thead><tr><th style={{ width: 28 }}><span className="sr-only">Include</span></th><th>Player</th><th className="hide-sm">Pos</th><th className="n">FP/G</th><th className="n">ROS</th></tr></thead>
            <tbody>
              {data.roster.map((p) => {
                const on = picked.includes(p.id);
                return (
                  <tr key={p.id} className={`clickable ${on ? "selected" : ""}`} onClick={() => onToggle(p.id)}>
                    <td><input type="checkbox" checked={on} onChange={() => onToggle(p.id)} onClick={(e) => e.stopPropagation()} aria-label={`Include ${p.name}`} /></td>
                    <td>{p.name}<Injury status={p.injury_status} /></td>
                    <td className="hide-sm muted">{p.positions.join(", ")}</td>
                    <td className="n">{fmt1(p.fp_pg)}</td>
                    <td className="n muted">{fmt0(p.ros_value)}</td>
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
  const [a, b] = ev.sides;
  return `Explain this trade: ${a.team_name} gives ${a.gives.map(name).join(", ") || "nothing"} to ${b.team_name} for `
    + `${a.receives.map(name).join(", ") || "nothing"}. Who wins it and why, what are the risks, `
    + "and is there a better version of this deal?";
}

function Analyzer({ draft, setDraft, autoRun }: { draft: Draft; setDraft: (d: Draft) => void; autoRun: boolean }) {
  const { league } = useLeagueCtx();
  const ai = useAiStatus();
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
      toast("Trade posted to the league board.");
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
    <div className="stack">
      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <RosterPicker title="Team A gives" label="Team A" teamId={draft.a} picked={draft.aGives} onToggle={(id) => toggle("aGives", id)} otherTeam={draft.b}
          onTeam={(id) => setDraft({ ...draft, a: id, aGives: [] })} />
        <RosterPicker title="Team B gives" label="Team B" teamId={draft.b} picked={draft.bGives} onToggle={(id) => toggle("bGives", id)} otherTeam={draft.a}
          onTeam={(id) => setDraft({ ...draft, b: id, bGives: [] })} />
      </div>
      <div className="toolbar">
        <button className="btn primary" disabled={!ready || evaluate.isFetching} onClick={() => setSubmitted({ ...draft })}>
          {evaluate.isFetching ? "Evaluating…" : "Evaluate trade"}
        </button>
        <span className="small muted">{draft.aGives.length} for {draft.bGives.length}. Uneven trades drop the weakest player or pick up the best free agent.</span>
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
  const [a, b] = ev.sides;
  return (
    <div className="stack">
      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <SidePanel s={a} />
        <SidePanel s={b} />
      </div>
      <Notice>
        <strong>{ev.fairness.label}.</strong> {ev.summary} Chance {b.team_name} sees it as a gain: {pct(ev.fairness.partner_accept_likelihood)}.
      </Notice>
      <div className="grid cols-2" style={{ alignItems: "start" }}>
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
              <tr><th>Player</th><th className="hide-sm">From</th><th className="hide-sm">Pos</th><th className="n">FP/G</th><th className="n">± game</th><th className="n">ROS games</th><th className="n">ROS pts</th><th className="n">Over repl.</th><th className="n hide-sm">Last 10</th></tr>
            </thead>
            <tbody>
              {ev.players.map((p) => (
                <tr key={p.player_id}>
                  <td><PlayerName name={p.name} injury={p.injury_status} onOpen={() => openPlayer(p.player_id)} sub={p.games_out > 0 ? `out ~${fmt0(p.games_out)}g` : undefined} /></td>
                  <td className="hide-sm muted">{teamName(p.fantasy_team_id)}</td>
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
                    <td className="n"><button className="btn" onClick={() => onLoad({ a: team, b: s.partner_id, aGives: s.give, bGives: s.get })}>Analyze</button></td>
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
      <RosterPicker title="Players you want" label="Their team" teamId={from} picked={targets} onToggle={toggle} otherTeam={team}
        onTeam={(id) => { setFrom(id); setTargets([]); }} />
      <p className="small muted">
        Tries every package of your players for them, fully simulates the most promising for both teams, and lists the offers the other team gains from or loses at most 0.4% on, best for you first. Offering more players than you get opens roster spots, filled with the best free agents.
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
  const analyze = (o: TradeOffer) => onLoad({ a: team, b: data.partner_id, aGives: o.give, bGives: data.get });
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
        {data.closest.length > 0 && <OfferTable title="Closest offers" offers={data.closest} onAnalyze={analyze} />}
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
        return group.length > 0 && <OfferTable key={n} title={`Offer ${count(n, "player")}`} offers={group} onAnalyze={analyze} />;
      })}
      {askAi}
    </div>
  );
}

function OfferTable({ title, offers, onAnalyze }: { title: string; offers: TradeOffer[]; onAnalyze: (o: TradeOffer) => void }) {
  return (
    <Panel title={title} flush>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr><th style={{ width: "45%" }}>You give</th><th className="n">You/wk</th><th className="n hide-sm">You ROS</th><th className="n">Partner</th><th className="hide-sm">Fit</th><th /></tr>
          </thead>
          <tbody>
            {offers.map((o) => (
              <tr key={o.give.join()}>
                <td className="wrap">{o.give_names.join(", ")}</td>
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

interface Post {
  id: number;
  author: string;
  author_id: string;
  team_a: number;
  team_b: number;
  a_gives: string[];
  b_gives: string[];
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
  return (
    <div className="stack">
      <RecentMoves />
      <Panel flush note="Numbers update automatically after every refresh, so they always reflect the latest stats and rosters.">
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Posted</th><th>Trade</th><th className="n">Team A/wk</th><th className="n">Team B/wk</th><th className="hide-sm">Note</th><th /></tr></thead>
            <tbody>
              {data.map((p) => {
                const names = (ids: string[]) => ids.map((id) => p.evaluation.players.find((x) => x.player_id === id)?.name ?? id).join(", ") || "nothing";
                const [a, b] = p.evaluation.sides;
                const status = p.evaluation.status ?? "open";
                return (
                  <tr key={p.id} className={status === "open" ? "" : "muted"}>
                    <td className="muted">{relTime(p.created_at)}<div className="tiny">{p.author}</div></td>
                    <td className="wrap">
                      <div><span className="muted">{teamName(p.team_a)}:</span> {names(p.a_gives)}</div>
                      <div><span className="muted">{teamName(p.team_b)}:</span> {names(p.b_gives)}</div>
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
                    <td className="wrap hide-sm muted">{p.note || "–"}</td>
                    <td className="n">
                      <div className="row" style={{ justifyContent: "flex-end" }}>
                        <button className="btn" onClick={() => onLoad({ a: p.team_a, b: p.team_b, aGives: p.a_gives, bGives: p.b_gives })}>Re-run</button>
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
