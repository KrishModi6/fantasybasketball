import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Dices, RotateCcw } from "lucide-react";
import { Distribution, MarginChart } from "../components/charts";
import { useLeagueCtx } from "../components/Layout";
import { Delta, Empty, ErrorBox, Injury, LoadingBlock, PageHead, Panel } from "../components/ui";
import { api } from "../lib/api";
import { fmt0, fmt1, pct, shortDate } from "../lib/format";
import type { SimResult, SimTeam, WeekMatchup, WeekOption } from "../lib/types";

const A_COLOR = "var(--ink)";
const B_COLOR = "var(--accent)";

interface WeekData {
  weeks: WeekOption[];
  matchup_period: number | null;
  matchups: WeekMatchup[];
  scoring: string;
}

const weekLabel = (w: WeekOption) =>
  `${w.is_playoff ? "Playoffs" : `Week ${w.matchup_period}`} · ${shortDate(w.start)}–${shortDate(w.end)}`;

export default function MatchupsPage() {
  const { league, myTeamId, teams } = useLeagueCtx();
  const [params, setParams] = useSearchParams();
  const mpParam = params.get("mp") ? Number(params.get("mp")) : undefined;
  const week = useQuery({
    queryKey: ["league", league.id, "matchups", mpParam ?? "current"],
    queryFn: () => api<WeekData>(`/api/leagues/${league.id}/matchups${mpParam ? `?matchup_period=${mpParam}` : ""}`),
    placeholderData: keepPreviousData,
  });
  const mp = week.data?.matchup_period ?? null;

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
    enabled: a != null && b != null && a !== b && mp != null,
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
  });

  const toggle = (pid: string) => setExclude((xs) => (xs.includes(pid) ? xs.filter((x) => x !== pid) : [...xs, pid]));

  return (
    <>
      <PageHead
        kicker="Matchup simulator"
        title="Matchups"
        lede="Each matchup is played out 10,000 times, box score by box score: who suits up, how big a night each player has, and who fills in when a starter sits. Toggle players to see how the odds move."
      />
      {week.error && <ErrorBox error={week.error} />}
      {week.isLoading && <LoadingBlock rows={6} />}
      {week.data && week.data.weeks.length === 0 && <div className="panel"><Empty title="The fantasy season is over" /></div>}
      {week.data && week.data.weeks.length > 0 && (
        <div className="stack" style={{ gap: 20 }}>
          <div className="row wrap" style={{ gap: 10, alignItems: "flex-end" }}>
            <div className="field">
              <label htmlFor="wk">Week</label>
              <select id="wk" className="select" style={{ width: "min(250px, 100%)" }} value={mp ?? ""} onChange={(e) => setWeek(Number(e.target.value))}>
                {week.data.weeks.map((w) => (
                  <option key={w.matchup_period} value={w.matchup_period}>{weekLabel(w)}{w.is_current ? " (live)" : ""}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="ta">Team A</label>
              <select id="ta" className="select" style={{ width: 210 }} value={a ?? ""} onChange={(e) => setPair(Number(e.target.value), b ?? 0)}>
                {teams.map((t) => <option key={t.id} value={t.id} disabled={t.id === b}>{t.name}{t.id === myTeamId ? " (you)" : ""}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="tb">Team B</label>
              <select id="tb" className="select" style={{ width: 210 }} value={b ?? ""} onChange={(e) => setPair(a ?? 0, Number(e.target.value))}>
                {teams.map((t) => <option key={t.id} value={t.id} disabled={t.id === a}>{t.name}{t.id === myTeamId ? " (you)" : ""}</option>)}
              </select>
            </div>
            {exclude.length > 0 && (
              <button className="btn secondary" onClick={() => setExclude([])}><RotateCcw /> Reset lineups ({exclude.length} sitting)</button>
            )}
          </div>

          {week.data.matchups.length > 0 && (
            <section aria-label="This week's matchups" className="grid grid-3" style={{ gap: 10 }}>
              {week.data.matchups.map((m) => {
                const active = (m.home_id === a && m.away_id === b) || (m.home_id === b && m.away_id === a);
                return (
                  <button key={`${m.home_id}-${m.away_id}`} className="option-card" aria-pressed={active}
                    onClick={() => setPair(m.home_id, m.away_id)} style={{ gap: 6 }}>
                    <span className="row between small"><span className="strong">{m.home_name}</span><span className="num">{fmt0(m.home_proj)}</span></span>
                    <span className="row between small"><span className="strong">{m.away_name}</span><span className="num">{fmt0(m.away_proj)}</span></span>
                    <span className="scoreboard-mini" aria-label={`${m.home_name} ${pct(m.home_win_prob)} to win`}>
                      <span style={{ width: `${m.home_win_prob * 100}%` }} />
                    </span>
                    <span className="tiny muted">{pct(m.home_win_prob)} – {pct(1 - m.home_win_prob)} · quick estimate</span>
                  </button>
                );
              })}
            </section>
          )}

          {sim.error && <ErrorBox error={sim.error} />}
          {sim.isLoading && <LoadingBlock rows={10} />}
          {sim.data && <SimView r={sim.data} exclude={exclude} onToggle={toggle} busy={sim.isFetching} />}
        </div>
      )}
    </>
  );
}

function SimView({ r, exclude, onToggle, busy }: { r: SimResult; exclude: string[]; onToggle: (pid: string) => void; busy: boolean }) {
  const [ta, tb] = r.teams;
  const isPoints = r.scoring === "points";
  return (
    <div className="stack" style={{ gap: 18, opacity: busy ? 0.6 : 1, transition: "opacity .15s" }} aria-busy={busy}>
      <section className="scoreboard" aria-label="Simulated result">
        <div className="row between wrap">
          <span className="kicker">
            {r.is_playoff ? "Playoffs" : `Week ${r.matchup_period}`} · {shortDate(r.start)}–{shortDate(r.end)} · {r.days_simulated} day{r.days_simulated === 1 ? "" : "s"} left
            {r.is_current && isPoints && (ta.accrued || tb.accrued) ? " · includes points already scored" : ""}
          </span>
          <span className="kicker"><Dices size={13} style={{ verticalAlign: -2 }} /> {r.sims.toLocaleString()} simulations</span>
        </div>
        <div className="teams">
          <div className="side">
            <span className="tname">{ta.name}</span>
            <span className="score">{pct(ta.win_prob)}</span>
            <span className="small" style={{ opacity: 0.75 }}>{isPoints ? `${fmt0(ta.mean)} pts · 80%: ${fmt0(ta.p10)}–${fmt0(ta.p90)}` : `${fmt1(r.categories?.a_cats_mean)} cats won on avg`}</span>
          </div>
          <span className="vs">VS</span>
          <div className="side right">
            <span className="tname">{tb.name}</span>
            <span className="score">{pct(tb.win_prob)}</span>
            <span className="small" style={{ opacity: 0.75 }}>{isPoints ? `${fmt0(tb.mean)} pts · 80%: ${fmt0(tb.p10)}–${fmt0(tb.p90)}` : `${fmt1(r.categories?.b_cats_mean)} cats won on avg`}</span>
          </div>
        </div>
        <div className="wp">
          <div className="wpbar" style={{ background: "var(--accent)" }} role="img" aria-label={`${ta.name} ${pct(ta.win_prob)}, ${tb.name} ${pct(tb.win_prob)}`}>
            <span style={{ width: `${ta.win_prob * 100}%`, background: "#f4f1ea" }} />
          </div>
          {r.tie_prob >= 0.005 && <span className="tiny" style={{ opacity: 0.7 }}>Tie {pct(r.tie_prob, 1)}</span>}
        </div>
      </section>

      <div className="grid grid-2" style={{ alignItems: "start" }}>
        <Panel title={isPoints ? "Score distribution" : "Production distribution"} kicker={isPoints ? "Fantasy points, all simulations" : "Fantasy-point equivalent, for scale"}>
          <Distribution bins={r.bins} label={`Simulated ${isPoints ? "scores" : "production"} for both teams`}
            series={[{ hist: ta.hist, color: A_COLOR, mean: ta.mean, name: ta.name }, { hist: tb.hist, color: B_COLOR, mean: tb.mean, name: tb.name }]} />
          <div className="row wrap small" style={{ marginTop: 8, gap: 14 }}>
            <Legend color={A_COLOR} label={`${ta.name} · median ${fmt0(ta.median)}`} />
            <Legend color={B_COLOR} label={`${tb.name} · median ${fmt0(tb.median)}`} />
          </div>
        </Panel>
        {isPoints ? (
          <Panel title="Margin" kicker={`${ta.name} minus ${tb.name}`}>
            <MarginChart bins={r.margin.bins} hist={r.margin.hist} label="Distribution of the winning margin" />
            <dl className="kv" style={{ marginTop: 10 }}>
              <dt>Expected margin</dt><dd><Delta value={r.margin.mean} digits={0} /></dd>
              <dt>80% of outcomes between</dt><dd><Delta value={r.margin.p10} digits={0} /> and <Delta value={r.margin.p90} digits={0} /></dd>
              <dt>Player-games left</dt><dd>{fmt1(ta.games)} vs {fmt1(tb.games)}</dd>
              <dt>Starts filled</dt><dd>{fmt1(ta.starts)} vs {fmt1(tb.starts)}</dd>
              {r.is_current && ta.accrued != null && <><dt>Already scored</dt><dd>{fmt0(ta.accrued)} – {fmt0(tb.accrued)}</dd></>}
            </dl>
          </Panel>
        ) : (
          <Panel title="Categories" kicker="Win probability by category" flush>
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Cat</th><th className="n">{ta.abbrev || "A"}</th><th className="n">Win</th><th className="n">{tb.abbrev || "B"}</th><th className="n">Win</th></tr></thead>
                <tbody>
                  {r.categories?.rows.map((c) => {
                    const ratio = c.category.endsWith("%");
                    const f = (v: number) => (ratio ? (v * 100).toFixed(1) : fmt1(v));
                    return (
                      <tr key={c.category}>
                        <td className="strong">{c.category}</td>
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

      <Panel title="Day by day" kicker="Expected points and player-games each day" flush>
        <DayTable a={ta} b={tb} />
      </Panel>

      <div className="grid grid-2" style={{ alignItems: "start" }}>
        <PlayerSimTable t={ta} exclude={exclude} onToggle={onToggle} color={A_COLOR} />
        <PlayerSimTable t={tb} exclude={exclude} onToggle={onToggle} color={B_COLOR} />
      </div>
      <p className="tiny muted" style={{ margin: 0 }}>
        Swing: how much more often the team wins when that player has a big week (top quarter of his simulated outcomes) than a quiet one (bottom quarter).
        Simulations use a fixed random seed, so sitting a player is a clean before/after comparison.
      </p>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="row" style={{ gap: 6 }}>
      <span style={{ width: 14, height: 3, background: color, display: "inline-block" }} aria-hidden="true" />
      {label}
    </span>
  );
}

function DayTable({ a, b }: { a: SimTeam; b: SimTeam }) {
  const periods = Array.from(new Set([...a.by_day, ...b.by_day].map((d) => d.period))).sort((x, y) => x - y);
  const get = (t: SimTeam, p: number) => t.by_day.find((d) => d.period === p);
  const max = Math.max(1, ...a.by_day.map((d) => d.mean), ...b.by_day.map((d) => d.mean));
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr><th>Day</th><th style={{ width: "34%" }}>{a.name}</th><th className="n">Games</th><th style={{ width: "34%" }}>{b.name}</th><th className="n">Games</th></tr></thead>
        <tbody>
          {periods.map((p) => {
            const da = get(a, p);
            const db = get(b, p);
            const date = da?.date ?? db?.date;
            return (
              <tr key={p}>
                <td>{date ? shortDate(date, true) : `Day ${p}`}</td>
                <td><DayBar value={da?.mean ?? 0} max={max} color={A_COLOR} /></td>
                <td className="n">{fmt1(da?.games ?? 0)}</td>
                <td><DayBar value={db?.mean ?? 0} max={max} color={B_COLOR} /></td>
                <td className="n">{fmt1(db?.games ?? 0)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function DayBar({ value, max, color }: { value: number; max: number; color: string }) {
  return (
    <div className="row" style={{ gap: 8 }}>
      <div style={{ flex: 1, height: 8, background: "var(--rule-2)", borderRadius: 1, overflow: "hidden" }}>
        <div style={{ width: `${(value / max) * 100}%`, height: "100%", background: color }} />
      </div>
      <span className="small num" style={{ width: 34, textAlign: "right" }}>{fmt0(value)}</span>
    </div>
  );
}

function PlayerSimTable({ t, exclude, onToggle, color }: { t: SimTeam; exclude: string[]; onToggle: (pid: string) => void; color: string }) {
  const { openPlayer } = useLeagueCtx();
  return (
    <Panel title={t.name} kicker="Simulated contributions" flush>
      <div style={{ height: 3, background: color }} />
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr><th>Plays</th><th>Player</th><th className="n">G</th><th className="n">Pts</th><th className="n hide-sm">80% range</th><th className="n">Swing</th></tr>
          </thead>
          <tbody>
            {t.players.map((p) => {
              const sitting = exclude.includes(p.player_id);
              return (
                <tr key={p.player_id} style={{ opacity: sitting ? 0.5 : 1 }}>
                  <td>
                    <input type="checkbox" checked={!sitting} onChange={() => onToggle(p.player_id)} style={{ accentColor: "var(--accent)", width: 16, height: 16 }}
                      aria-label={`${sitting ? "Play" : "Sit"} ${p.name}`} />
                  </td>
                  <td className="player">
                    <div className="player-cell">
                      <span><button className="nm" onClick={() => openPlayer(p.player_id)}>{p.name}</button><Injury status={p.injury_status} /></span>
                      <span className="pos-tag">{p.pro_team} · {p.positions.join(", ")} · {fmt1(p.starts)} starts</span>
                    </div>
                  </td>
                  <td className="n">{fmt1(p.games)}</td>
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
