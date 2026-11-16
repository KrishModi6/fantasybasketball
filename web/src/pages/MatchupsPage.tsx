import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Distribution, MarginChart, SERIES_A, SERIES_B } from "../components/charts";
import { useLeagueCtx } from "../components/Layout";
import { Delta, Empty, ErrorBox, Kpi, Kpis, LoadingBlock, PageHead, Panel, PlayerName, Term } from "../components/ui";
import { askPath } from "../lib/ai";
import { api } from "../lib/api";
import { fmt0, fmt1, pct, shortDate } from "../lib/format";
import { useAiStatus } from "../lib/queries";
import type { SimResult, SimTeam, WeekMatchup, WeekOption, WeekResult } from "../lib/types";

interface WeekData {
  weeks: WeekOption[];
  matchup_period: number | null;
  matchups: WeekMatchup[];
  final?: boolean; // a week already played: `results` instead of `matchups`
  results?: WeekResult[];
  scoring: string;
}

const weekName = (w: WeekOption) => (w.is_playoff ? "Playoffs" : `Week ${w.matchup_period}`);
export const weekLabel = (w: WeekOption) =>
  `${weekName(w)} (${shortDate(w.start)}–${shortDate(w.end)})${w.is_current ? ", live" : w.is_final ? ", final" : ""}`;

export default function MatchupsPage() {
  const { league, myTeamId, teams, teamName } = useLeagueCtx();
  const ai = useAiStatus();
  const [params, setParams] = useSearchParams();
  const mpParam = params.get("mp") ? Number(params.get("mp")) : undefined;
  const week = useQuery({
    queryKey: ["league", league.id, "matchups", mpParam ?? "current"],
    queryFn: () => api<WeekData>(`/api/leagues/${league.id}/matchups${mpParam ? `?matchup_period=${mpParam}` : ""}`),
    placeholderData: keepPreviousData,
  });
  const mp = week.data?.matchup_period ?? null;
  const final = Boolean(week.data?.final); // a week already played: its results, nothing to simulate
  const shownWeek = week.data?.weeks.find((w) => w.matchup_period === mp);

  // Selected pairing: URL params, else my matchup this week, else the first one.
  const urlA = params.get("a") ? Number(params.get("a")) : null;
  const urlB = params.get("b") ? Number(params.get("b")) : null;
  const defaultPair = useMemo<[number, number] | null>(() => {
    const ms = week.data?.matchups ?? [];
    const mine = ms.find((m) => m.home_id === myTeamId || m.away_id === myTeamId);
    if (mine) return mine.home_id === myTeamId ? [mine.home_id, mine.away_id] : [mine.away_id, mine.home_id];
    if (ms[0]) return [ms[0].home_id, ms[0].away_id];
    return teams.length > 1 ? [teams[0]!.id, teams[1]!.id] : null;
  }, [week.data, myTeamId, teams]);
  const a = urlA ?? defaultPair?.[0] ?? null;
  const b = urlB ?? defaultPair?.[1] ?? null;

  const [exclude, setExclude] = useState<string[]>([]);
  useEffect(() => setExclude([]), [a, b, mp]);

  const setPair = (na: number, nb: number) => {
    const next = new URLSearchParams(params);
    next.set("a", String(na));
    next.set("b", String(nb));
    setParams(next, { replace: true });
  };
  const setWeek = (m: number) => {
    const next = new URLSearchParams(params);
    next.set("mp", String(m));
    setParams(next, { replace: true });
  };

  const sim = useQuery({
    queryKey: ["league", league.id, "simulate", a, b, mp, [...exclude].sort().join(",")],
    queryFn: () => api<SimResult>(`/api/leagues/${league.id}/matchups/simulate`, {
      body: { team_a: a, team_b: b, matchup_period: mp, exclude },
    }),
    enabled: a != null && b != null && a !== b && mp != null && !final,
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
  });
  const toggle = (pid: string) => setExclude((xs) => (xs.includes(pid) ? xs.filter((x) => x !== pid) : [...xs, pid]));

  return (
    <>
      <PageHead title="Matchups"
        desc="Each matchup is simulated 10,000 times game by game: who plays, how big a night each player has, and who fills in when a starter sits. Uncheck players to test lineup decisions. Pick a past week to see its final scores." />
      {week.error && <ErrorBox error={week.error} />}
      {week.isLoading && <LoadingBlock rows={6} />}
      {week.data && week.data.weeks.length === 0 && <Panel><Empty title="The fantasy season is over" /></Panel>}
      {week.data && week.data.weeks.length > 0 && (
        <>
          <div className="toolbar">
            <label className="field-inline"><span className="label">Week</span>
              <select className="select" value={mp ?? ""} onChange={(e) => setWeek(Number(e.target.value))}>
                {week.data.weeks.map((w) => <option key={w.matchup_period} value={w.matchup_period}>{weekLabel(w)}</option>)}
              </select>
            </label>
            {!final && <>
            <span className="sep" />
            <label className="field-inline"><span className="label">Team A</span>
              <select className="select" value={a ?? ""} onChange={(e) => setPair(Number(e.target.value), b ?? 0)}>
                {teams.map((t) => <option key={t.id} value={t.id} disabled={t.id === b}>{t.name}{t.id === myTeamId ? " (you)" : ""}</option>)}
              </select>
            </label>
            <label className="field-inline"><span className="label">Team B</span>
              <select className="select" value={b ?? ""} onChange={(e) => setPair(a ?? 0, Number(e.target.value))}>
                {teams.map((t) => <option key={t.id} value={t.id} disabled={t.id === a}>{t.name}{t.id === myTeamId ? " (you)" : ""}</option>)}
              </select>
            </label>
            {exclude.length > 0 && <button className="btn" onClick={() => setExclude([])}>Reset lineups ({exclude.length} sitting)</button>}
            {sim.isFetching && <span className="small muted">Simulating…</span>}
            {ai.data?.enabled && a != null && b != null && mp != null && (
              <Link className="btn" to={askPath(league.id, {
                prompt: `Game plan for ${teamName(a)} vs ${teamName(b)} in matchup week ${mp}: who is favoured and why, `
                  + `which players swing it, and which lineup or streaming moves would most help ${teamName(a)}?`,
              })}>Ask AI</Link>
            )}
            </>}
          </div>

          {final && (
            <WeekResults title={`${shownWeek ? weekName(shownWeek) : `Week ${mp}`} results`}
              rows={week.data.results ?? []} points={week.data.scoring === "points"} myTeamId={myTeamId} />
          )}

          {!final && week.data.matchups.length > 0 && (
            <Panel title={`${week.data.weeks.find((w) => w.matchup_period === mp)?.is_playoff ? "Playoff" : `Week ${mp}`} schedule`} flush
              note="Quick estimates from the season model. Select a row to simulate it.">
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>Home</th><th className="n"><Term t="pts_wk_team">Proj</Term></th><th className="n"><Term t="win_prob" /></th><th className="hide-sm" style={{ width: 140 }} /><th className="n"><Term t="win_prob" /></th><th className="n"><Term t="pts_wk_team">Proj</Term></th><th>Away</th></tr></thead>
                  <tbody>
                    {week.data.matchups.map((m) => {
                      const active = (m.home_id === a && m.away_id === b) || (m.home_id === b && m.away_id === a);
                      return (
                        <tr key={`${m.home_id}-${m.away_id}`} className={`clickable ${active ? "selected" : ""}`} onClick={() => setPair(m.home_id, m.away_id)}>
                          <td>{m.home_name}</td>
                          <td className="n">{fmt0(m.home_proj)}</td>
                          <td className="n">{pct(m.home_win_prob)}</td>
                          <td className="hide-sm"><div className="wpbar"><span style={{ width: `${m.home_win_prob * 100}%` }} /></div></td>
                          <td className="n">{pct(1 - m.home_win_prob)}</td>
                          <td className="n">{fmt0(m.away_proj)}</td>
                          <td>{m.away_name}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          {!final && sim.error && <ErrorBox error={sim.error} />}
          {!final && sim.isLoading && <LoadingBlock rows={10} />}
          {!final && sim.data && <SimView r={sim.data} exclude={exclude} onToggle={toggle} busy={sim.isFetching} />}
        </>
      )}
    </>
  );
}

/** A finished week: ESPN's final scores, the winner in bold, your matchup marked and summed up. */
export function WeekResults({ title, rows, points, myTeamId }: { title: string; rows: WeekResult[]; points: boolean; myTeamId: number | null }) {
  const score = (v: number) => (points ? fmt1(v) : fmt0(v));
  const mine = rows.find((r) => r.home_id === myTeamId || r.away_id === myTeamId);
  let note = points ? "Final scores from ESPN." : "Categories won, from ESPN.";
  if (mine) {
    const home = mine.home_id === myTeamId;
    const [me, them, foe] = home ? [mine.home_score, mine.away_score, mine.away_name] : [mine.away_score, mine.home_score, mine.home_name];
    const verb = mine.winner === "TIE" ? "tied" : (mine.winner === "HOME") === home ? "beat" : "lost to";
    note = `You ${verb} ${foe}, ${score(me)} to ${score(them)}.`;
  }
  return (
    <Panel title={title} flush note={note}>
      {rows.length === 0 ? <Empty title="No matchups this week" /> : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Home</th><th className="n">{points ? "Pts" : "Cats"}</th><th className="n">{points ? "Pts" : "Cats"}</th><th>Away</th><th className="n hide-sm">Margin</th></tr></thead>
            <tbody>
              {rows.map((r) => {
                const homeWon = r.winner === "HOME";
                const awayWon = r.winner === "AWAY";
                const margin = Math.abs(r.home_score - r.away_score);
                return (
                  <tr key={`${r.home_id}-${r.away_id}`} className={r === mine ? "mine" : ""}>
                    <td className={homeWon ? "strong" : ""}>{r.home_name}{r.home_id === myTeamId ? " (you)" : ""}</td>
                    <td className={`n ${homeWon ? "strong" : "muted"}`}>{score(r.home_score)}</td>
                    <td className={`n ${awayWon ? "strong" : "muted"}`}>{score(r.away_score)}</td>
                    <td className={awayWon ? "strong" : ""}>
                      {r.away_name}{r.away_id === myTeamId ? " (you)" : ""}
                      {r.consolation && <span className="small muted"> · consolation</span>}
                    </td>
                    <td className="n hide-sm">{r.winner === "TIE" ? "Tie" : score(margin)}</td>
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

function SimView({ r, exclude, onToggle, busy }: { r: SimResult; exclude: string[]; onToggle: (pid: string) => void; busy: boolean }) {
  const [ta, tb] = r.teams;
  const isPoints = r.scoring === "points";
  const accrued = r.is_current && isPoints && (ta.accrued || tb.accrued);
  return (
    <div className="stack" style={{ opacity: busy ? 0.65 : 1 }} aria-busy={busy}>
      <Kpis label="Simulation result">
        <Kpi k={<><span className="legend"><i style={{ background: SERIES_A }} /></span> {ta.name} · <Term t="win_prob">win chance</Term></>} v={pct(ta.win_prob)} tone="accent"
          s={isPoints ? <>{fmt0(ta.mean)} pts, <Term t="range80" /> {fmt0(ta.p10)}–{fmt0(ta.p90)}</> : `${fmt1(r.categories?.a_cats_mean)} categories won on average`} />
        <Kpi k={<><span className="legend"><i style={{ background: SERIES_B }} /></span> {tb.name} · <Term t="win_prob">win chance</Term></>} v={<span style={{ color: SERIES_B }}>{pct(tb.win_prob)}</span>}
          s={isPoints ? <>{fmt0(tb.mean)} pts, <Term t="range80" /> {fmt0(tb.p10)}–{fmt0(tb.p90)}</> : `${fmt1(r.categories?.b_cats_mean)} categories won on average`} />
        {isPoints && <Kpi k={<Term t="margin" />} v={<Delta value={r.margin.mean} digits={0} />} s={<><Term t="range80" /> {fmt0(r.margin.p10)} to {fmt0(r.margin.p90)}</>} />}
        <Kpi k={<Term t="player_games" />} v={`${fmt1(ta.games)} / ${fmt1(tb.games)}`} s={`${r.days_simulated} day${r.days_simulated === 1 ? "" : "s"}, ${shortDate(r.start)}–${shortDate(r.end)}`} />
        {accrued ? <Kpi k="Already scored" v={`${fmt0(ta.accrued)} – ${fmt0(tb.accrued)}`} /> : null}
        <Kpi k={<Term t="tie" />} v={pct(r.tie_prob, 1)} s={`${r.sims.toLocaleString()} simulations`} />
      </Kpis>

      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <Panel title={isPoints ? "Score distribution" : "Production distribution (fantasy-point scale)"}>
          <Distribution bins={r.bins} label={`Simulated ${isPoints ? "scores" : "production"} for both teams`}
            series={[{ hist: ta.hist, color: SERIES_A, mean: ta.mean, name: ta.name }, { hist: tb.hist, color: SERIES_B, mean: tb.mean, name: tb.name }]} />
          <div className="row wrap" style={{ marginTop: 6, gap: 14 }}>
            <span className="legend"><i style={{ background: SERIES_A }} />{ta.name}, median {fmt0(ta.median)}</span>
            <span className="legend"><i style={{ background: SERIES_B }} />{tb.name}, median {fmt0(tb.median)}</span>
          </div>
        </Panel>
        {isPoints ? (
          <Panel title="Winning margin" note={`${ta.name} minus ${tb.name}.`}>
            <MarginChart bins={r.margin.bins} hist={r.margin.hist} label="Distribution of the winning margin" />
          </Panel>
        ) : (
          <Panel title="Categories" flush>
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Category</th><th className="n">{ta.abbrev || "A"}</th><th className="n">Win</th><th className="n">{tb.abbrev || "B"}</th><th className="n">Win</th></tr></thead>
                <tbody>
                  {r.categories?.rows.map((c) => {
                    const f = (v: number) => (c.category.endsWith("%") ? (v * 100).toFixed(1) : fmt1(v));
                    return (
                      <tr key={c.category}>
                        <td>{c.category}</td>
                        <td className="n">{f(c.a_mean)}</td>
                        <td className={`n ${c.a_win > 0.5 ? "strong" : "muted"}`}>{pct(c.a_win)}</td>
                        <td className="n">{f(c.b_mean)}</td>
                        <td className={`n ${c.b_win > 0.5 ? "strong" : "muted"}`}>{pct(c.b_win)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Panel>
        )}
      </div>

      <Panel title="Day by day" flush>
        <DayTable a={ta} b={tb} />
      </Panel>

      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <PlayerSimTable t={ta} exclude={exclude} onToggle={onToggle} color={SERIES_A} />
        <PlayerSimTable t={tb} exclude={exclude} onToggle={onToggle} color={SERIES_B} />
      </div>
      <p className="small muted">
        Swing: how much more often the team wins when the player has a big week (top quarter of his simulated outcomes) than a quiet one (bottom quarter).
        A fixed random seed is used, so sitting a player gives a clean before/after comparison.
      </p>
    </div>
  );
}

function DayTable({ a, b }: { a: SimTeam; b: SimTeam }) {
  const periods = Array.from(new Set([...a.by_day, ...b.by_day].map((d) => d.period))).sort((x, y) => x - y);
  const get = (t: SimTeam, p: number) => t.by_day.find((d) => d.period === p);
  const max = Math.max(1, ...a.by_day.map((d) => d.mean), ...b.by_day.map((d) => d.mean));
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr><th>Day</th><th className="n">{a.abbrev || "A"} pts</th><th className="n">Games</th><th className="hide-sm" style={{ width: "28%" }} /><th className="n">{b.abbrev || "B"} pts</th><th className="n">Games</th><th className="hide-sm" style={{ width: "28%" }} /></tr></thead>
        <tbody>
          {periods.map((p) => {
            const da = get(a, p);
            const db = get(b, p);
            const date = da?.date ?? db?.date;
            return (
              <tr key={p}>
                <td>{date ? shortDate(date, true) : `Day ${p}`}</td>
                <td className="n">{fmt0(da?.mean ?? 0)}</td>
                <td className="n muted">{fmt1(da?.games ?? 0)}</td>
                <td className="hide-sm"><div className="bar"><span style={{ width: `${((da?.mean ?? 0) / max) * 100}%`, background: SERIES_A }} /></div></td>
                <td className="n">{fmt0(db?.mean ?? 0)}</td>
                <td className="n muted">{fmt1(db?.games ?? 0)}</td>
                <td className="hide-sm"><div className="bar"><span style={{ width: `${((db?.mean ?? 0) / max) * 100}%`, background: SERIES_B }} /></div></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PlayerSimTable({ t, exclude, onToggle, color }: { t: SimTeam; exclude: string[]; onToggle: (pid: string) => void; color: string }) {
  const { openPlayer } = useLeagueCtx();
  return (
    <Panel title={<span className="row" style={{ gap: 6 }}><span className="legend"><i style={{ background: color }} /></span>{t.name}</span>} flush>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr><th style={{ width: 44 }}>Plays</th><th>Player</th><th className="n"><Term t="sim_games" /></th><th className="n"><Term t="sim_starts" /></th><th className="n"><Term t="sim_pts" /></th><th className="n hide-sm"><Term t="range80" /></th><th className="n"><Term t="swing" /></th></tr>
          </thead>
          <tbody>
            {t.players.map((p) => {
              const sitting = exclude.includes(p.player_id);
              return (
                <tr key={p.player_id} style={{ opacity: sitting ? 0.5 : 1 }}>
                  <td><input type="checkbox" checked={!sitting} onChange={() => onToggle(p.player_id)} aria-label={`${sitting ? "Play" : "Sit"} ${p.name}`} /></td>
                  <td><PlayerName name={p.name} injury={p.injury_status} onOpen={() => openPlayer(p.player_id)} sub={p.pro_team} /></td>
                  <td className="n">{fmt1(p.games)}</td>
                  <td className="n muted">{fmt1(p.starts)}</td>
                  <td className="n strong">{fmt0(p.mean)}</td>
                  <td className="n hide-sm muted">{fmt0(p.p10)}–{fmt0(p.p90)}</td>
                  <td className="n">{p.swing ? <Delta value={p.swing * 100} digits={0} suffix="%" /> : <span className="faint">–</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
