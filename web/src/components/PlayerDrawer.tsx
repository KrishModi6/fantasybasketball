import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { Link } from "react-router-dom";
import { askPath } from "../lib/ai";
import { api } from "../lib/api";
import { fmt0, fmt1, pct, relTime, shortDate, signed } from "../lib/format";
import { useAiStatus, usePlayer } from "../lib/queries";
import { useToast } from "../lib/toast";
import type { PlayerDetail } from "../lib/types";
import { Diverging, GameColumns, Sparkline } from "./charts";
import { useLeagueCtx } from "./Layout";
import { Delta, ErrorBox, Injury, Kpi, Kpis, LoadingBlock, Notice, Panel } from "./ui";

const BOX = ["MIN", "PTS", "REB", "AST", "STL", "BLK", "TO", "3PM"];

export function PlayerDrawer({ playerId, onClose }: { playerId: string; onClose: () => void }) {
  const { league } = useLeagueCtx();
  const { data: p, error, isLoading } = usePlayer(league.id, playerId);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus();
    };
  }, [onClose]);

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={p?.name ?? "Player"} tabIndex={-1} ref={ref}>
        {isLoading && <div className="drawer-body"><LoadingBlock rows={10} /></div>}
        {error && <div className="drawer-body"><ErrorBox error={error} /></div>}
        {p && <PlayerBody p={p} onClose={onClose} />}
      </aside>
    </>
  );
}

function PlayerBody({ p, onClose }: { p: PlayerDetail; onClose: () => void }) {
  const { teamName, league, myTeamId } = useLeagueCtx();
  const ai = useAiStatus();
  const isPoints = league.settings?.scoring.kind !== "categories";
  const maxEx = Math.max(...p.explanation.map((e) => Math.abs(e.fp_pg)), 0.5);
  const last = [...p.game_log].reverse().slice(-15);
  const owner = p.fantasy_team_id ? teamName(p.fantasy_team_id) : p.on_waivers ? "Waivers" : "Free agent";
  const question = p.fantasy_team_id == null
    ? `Should I pick up ${p.name}? Who would I drop, and how many points per week would it add?`
    : p.fantasy_team_id === myTeamId
      ? `Should I keep ${p.name}? Compare their rest-of-season value with my best waiver and trade alternatives.`
      : `Should I trade for ${p.name}? What would a fair offer from my roster be, and would it help me?`;
  return (
    <>
      <header className="drawer-head">
        <div>
          <h2>{p.name}<Injury status={p.injury_status} /></h2>
          <div className="small muted">
            {p.pro_team} · {p.positions.join(", ")}{p.age ? ` · age ${Math.floor(p.age)}` : ""} · {owner} · overall #{p.rank}
            {p.ownership_pct != null ? ` · ${fmt1(p.ownership_pct)}% rostered on ESPN` : ""}
          </div>
        </div>
        <div className="row">
          {p.fantasy_team_id != null && p.fantasy_team_id !== myTeamId && (
            <Link className="btn" to={`/l/${league.id}/trades?tab=target&target=${p.id}&from=${p.fantasy_team_id}`}>What to offer</Link>
          )}
          {p.fantasy_team_id != null && p.fantasy_team_id === myTeamId && (
            <Link className="btn" to={`/l/${league.id}/trades?tab=shop&shop=${p.id}&team=${p.fantasy_team_id}`}>What to ask for</Link>
          )}
          {ai.data?.enabled && <Link className="btn" to={askPath(league.id, { prompt: question })}>Ask AI</Link>}
          <button className="btn ghost icon" onClick={onClose} aria-label="Close"><X /></button>
        </div>
      </header>
      <div className="drawer-body">
        {p.injury_note && <Notice level="warn">{p.injury_note}</Notice>}
        <Kpis label="Projection summary">
          <Kpi k={isPoints ? "Projected FP/G" : "Value per game"} v={fmt1(isPoints ? p.fp_pg : p.value_pg)} s={`± ${fmt0(p.fp_sd)} per game`} />
          <Kpi k="Games left" v={fmt0(p.ros_games)} s={p.games_out > 0 ? `${fmt0(p.games_out)} missed to injury` : `${pct(p.availability)} availability`} />
          <Kpi k="Rest of season" v={fmt0(p.ros_value)} s={<>over replacement <Delta value={p.vorp} digits={0} /></>} />
          <Kpi k="Minutes" v={fmt1(p.mpg)} s="per game" />
        </Kpis>

        <Panel title="How the projection is built" flush>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Source</th><th className="n">FP/G</th><th className="n">MPG</th><th className="n">Games %</th><th className="n">Weight</th></tr></thead>
              <tbody>
                {p.sources.map((s) => (
                  <tr key={s.source}>
                    <td>{s.source}</td>
                    <td className="n">{fmt1(s.fp_pg)}</td>
                    <td className="n">{fmt1(s.mpg)}</td>
                    <td className="n">{s.availability != null ? pct(s.availability) : "–"}</td>
                    <td className="n">{pct(s.weight)}</td>
                  </tr>
                ))}
                <tr><td>Preseason blend</td><td className="n strong">{fmt1(p.preseason_fp_pg)}</td><td /><td /><td /></tr>
              </tbody>
            </table>
          </div>
          <p className="panel-foot">
            This season: {p.games_played} game{p.games_played === 1 ? "" : "s"}{p.season_fp_pg != null ? ` at ${fmt1(p.season_fp_pg)} FP/G` : ""}, carrying {pct(p.in_season_weight)} of
            the weight{p.in_season_weight >= 1
              ? ": from 15 games on, last season and the preseason blend no longer count"
              : `${p.carryover_games > 0 ? `; last season's final ${p.carryover_games} games add ${pct(p.carryover_weight)}` : ""} (updated stat by stat; steals and blocks need more games than points or rebounds)`}.
            Weights are each source's share across stats,
            weighted by their value under your scoring. Result: {fmt1(p.fp_pg)} FP/G in {fmt1(p.mpg)} minutes.
          </p>
        </Panel>

        <Panel title="Adjustments" flush note="Minutes adjustments are capped at −20% / +15% in total; injuries only change games played.">
          {p.adjustments.length === 0 ? <p className="small muted" style={{ padding: "0 10px 10px" }}>None. The projection is purely statistical.</p> : (
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Adjustment</th><th className="n">Effect</th><th>Detail</th></tr></thead>
                <tbody>
                  {p.adjustments.map((a, i) => (
                    <tr key={i}>
                      <td>{a.label}</td>
                      <td className="n">
                        {a.minutes_pct ? <Delta value={a.minutes_pct * 100} suffix="% min" /> : null}
                        {a.games_out ? <span className="down">{fmt1(a.games_out)} g out</span> : null}
                      </td>
                      <td className="wrap muted small">{a.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        {p.explanation.length > 0 && (
          <Panel title="ML model factors" flush note="TreeSHAP contributions to the model's correction of the statistical baseline, in fantasy points per game.">
            <div className="table-wrap">
              <table className="data">
                <tbody>
                  {p.explanation.slice(0, 7).map((e) => (
                    <tr key={e.factor}>
                      <td>{e.factor}</td>
                      <td style={{ width: "40%" }}><Diverging value={e.fp_pg} max={maxEx} label={`${e.factor} ${signed(e.fp_pg)}`} /></td>
                      <td className="n"><Delta value={e.fp_pg} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}

        <Panel title="Projected per game" flush>
          <div className="table-wrap">
            <table className="data">
              <thead><tr>{BOX.map((k) => <th key={k} className="n">{k}</th>)}<th className="n">FG%</th><th className="n">FT%</th></tr></thead>
              <tbody>
                <tr>
                  {BOX.map((k) => <td key={k} className="n">{fmt1(p.per_game[k])}</td>)}
                  <td className="n">{p.fg_pct != null ? (p.fg_pct * 100).toFixed(1) : "–"}</td>
                  <td className="n">{p.ft_pct != null ? (p.ft_pct * 100).toFixed(1) : "–"}</td>
                </tr>
              </tbody>
            </table>
          </div>
          {isPoints && (
            <p className="panel-foot">
              Fantasy points by stat:{" "}
              {Object.entries(p.fp_breakdown).filter(([, v]) => Math.abs(v) >= 0.05).sort((a, b) => b[1] - a[1])
                .map(([k, v]) => `${k} ${signed(v)}`).join(", ")}
            </p>
          )}
        </Panel>

        {last.length > 0 && (
          <Panel title={`Last ${last.length} games`} flush>
            <div style={{ padding: "0 10px 8px" }}>
              <GameColumns values={last.map((g) => g.fp)} projection={p.fp_pg} />
              <div className="row tiny muted" style={{ marginTop: 4, gap: 14 }}>
                <span className="legend"><i style={{ background: "var(--accent)" }} />at or above projection</span>
                <span className="legend"><i style={{ background: "var(--series-b)" }} />projection</span>
              </div>
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Date</th><th>Team</th><th className="n">MIN</th><th className="n">FP</th>{["PTS", "REB", "AST", "STL", "BLK", "TO", "3PM"].map((k) => <th key={k} className="n">{k}</th>)}</tr></thead>
                <tbody>
                  {p.game_log.slice(0, 10).map((g) => (
                    <tr key={`${g.season}-${g.period}`}>
                      <td>{g.date ? shortDate(g.date) : `${g.season - 1}–${String(g.season).slice(2)}`}</td>
                      <td className="muted">{g.team}</td>
                      <td className="n">{g.min}</td>
                      <td className="n strong">{fmt1(g.fp)}</td>
                      {["PTS", "REB", "AST", "STL", "BLK", "TO", "3PM"].map((k) => <td key={k} className="n">{g[k] as number}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}

        {p.seasons.length > 0 && (
          <Panel title="Seasons" flush>
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Season</th><th>Team</th><th className="n">GP</th><th className="n">MPG</th><th className="n">FP/G</th><th className="n">PTS</th><th className="n">REB</th><th className="n">AST</th><th className="n">STL</th><th className="n">BLK</th></tr></thead>
                <tbody>
                  {[...p.seasons].reverse().map((s) => (
                    <tr key={s.season}>
                      <td>{s.label}</td>
                      <td className="muted">{s.team}</td>
                      <td className="n">{s.gp}</td>
                      <td className="n">{fmt1(s.mpg)}</td>
                      <td className="n strong">{fmt1(s.fp_pg)}</td>
                      {["PTS", "REB", "AST", "STL", "BLK"].map((k) => <td key={k} className="n">{fmt1(s[k] as number)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}

        {p.trend.length > 1 && (
          <Panel title="Projection history" actions={<span className="small muted">{p.trend.length} daily snapshots</span>}>
            <Sparkline values={p.trend.map((t) => t.fp_pg)} width={600} height={40} label="Projected FP/G over time" />
          </Panel>
        )}

        {p.news.length > 0 && (
          <Panel title="News" note="Context only; headlines do not change the projection.">
            <ul className="list">
              {p.news.map((n) => (
                <li key={n.id} className="small">
                  <div>{n.link ? <a href={n.link} target="_blank" rel="noreferrer" style={{ color: "var(--text)" }}>{n.headline}</a> : n.headline}</div>
                  <div className="tiny muted">{relTime(n.published)}{n.description ? ` · ${n.description}` : ""}</div>
                </li>
              ))}
            </ul>
          </Panel>
        )}

        <OverrideForm p={p} />
      </div>
    </>
  );
}

function OverrideForm({ p }: { p: PlayerDetail }) {
  const { league } = useLeagueCtx();
  const qc = useQueryClient();
  const toast = useToast();
  const [mins, setMins] = useState(Math.round((p.override?.minutes_pct ?? 0) * 100));
  const [outGames, setOutGames] = useState<string>(p.override?.out_games != null ? String(p.override.out_games) : "");
  const [note, setNote] = useState(p.override?.note ?? "");
  const done = () => qc.invalidateQueries({ queryKey: ["league", league.id] });
  const save = useMutation({
    mutationFn: () => api(`/api/leagues/${league.id}/players/${p.id}/override`, {
      method: "PUT",
      body: { minutes_pct: mins / 100, out_games: outGames === "" ? null : Number(outGames), note },
    }),
    onSuccess: () => {
      toast("Override saved for the league.");
      done();
    },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const clear = useMutation({
    mutationFn: () => api(`/api/leagues/${league.id}/players/${p.id}/override`, { method: "DELETE" }),
    onSuccess: () => {
      setMins(0);
      setOutGames("");
      setNote("");
      toast("Override removed.");
      done();
    },
  });
  return (
    <Panel title="League override" note="For information the data doesn't have yet, such as a confirmed role change. Applies to the whole league and is logged in the activity feed.">
      <div className="form">
        <div className="form-grid">
          <div className="field">
            <label htmlFor="ov-min">Minutes: {mins > 0 ? "+" : ""}{mins}%</label>
            <input id="ov-min" type="range" min={-15} max={15} step={1} value={mins} onChange={(e) => setMins(Number(e.target.value))} />
          </div>
          <div className="field">
            <label htmlFor="ov-out">Games out</label>
            <input id="ov-out" className="input" inputMode="numeric" value={outGames} onChange={(e) => setOutGames(e.target.value.replace(/\D/g, ""))} placeholder="From injury report" />
          </div>
          <div className="field">
            <label htmlFor="ov-note">Reason</label>
            <input id="ov-note" className="input" value={note} maxLength={280} onChange={(e) => setNote(e.target.value)} />
          </div>
        </div>
        <div className="toolbar">
          <button className="btn primary" onClick={() => save.mutate()} disabled={save.isPending}>Save override</button>
          {p.override && <button className="btn danger" onClick={() => clear.mutate()} disabled={clear.isPending}>Remove</button>}
        </div>
      </div>
    </Panel>
  );
}
