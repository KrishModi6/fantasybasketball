import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, X } from "lucide-react";
import { api } from "../lib/api";
import { fmt0, fmt1, pct, relTime, shortDate, signed } from "../lib/format";
import { usePlayer } from "../lib/queries";
import { useToast } from "../lib/toast";
import type { PlayerDetail } from "../lib/types";
import { Bar, Diverging, GameColumns, Sparkline } from "./charts";
import { useLeagueCtx } from "./Layout";
import { Delta, ErrorBox, Injury, LoadingBlock } from "./ui";

export function PlayerDrawer({ playerId, onClose }: { playerId: string; onClose: () => void }) {
  const { league } = useLeagueCtx();
  const { data: p, error, isLoading } = usePlayer(league.id, playerId);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
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
  const { teamName, league } = useLeagueCtx();
  const isPoints = league.settings?.scoring.kind !== "categories";
  const maxSrc = Math.max(...p.sources.map((s) => s.fp_pg), p.fp_pg, 1);
  const maxEx = Math.max(...p.explanation.map((e) => Math.abs(e.fp_pg)), 0.5);
  const preBlend = p.preseason_fp_pg;
  const last = [...p.game_log].reverse().slice(-15);
  return (
    <>
      <header className="drawer-head">
        <div className="stack" style={{ gap: 6 }}>
          <div className="kicker accent">
            {p.pro_team} · {p.positions.join(" / ")} {p.age ? `· age ${Math.floor(p.age)}` : ""}
          </div>
          <h2 className="h1" style={{ fontSize: 34 }}>
            {p.name}
            <Injury status={p.injury_status} />
          </h2>
          <div className="row wrap small">
            <span className={`badge ${p.fantasy_team_id ? "ink" : "pos"}`}>{p.fantasy_team_id ? teamName(p.fantasy_team_id) : p.on_waivers ? "On waivers" : "Free agent"}</span>
            <span className="muted">Overall rank #{p.rank}</span>
            {p.ownership_pct != null && <span className="muted">· {fmt1(p.ownership_pct)}% rostered on ESPN</span>}
          </div>
        </div>
        <button className="btn ghost icon" onClick={onClose} aria-label="Close player">
          <X />
        </button>
      </header>
      <div className="drawer-body">
        {p.injury_note && <div className="callout neg small">{p.injury_note}</div>}

        <div className="stat-strip">
          <div>
            <span className="kicker">{isPoints ? "Proj FP/G" : "Value / G"}</span>
            <span className="v">{fmt1(isPoints ? p.fp_pg : p.value_pg)}<small>±{fmt0(p.fp_sd)}</small></span>
          </div>
          <div>
            <span className="kicker">ROS games</span>
            <span className="v">{fmt0(p.ros_games)}</span>
          </div>
          <div>
            <span className="kicker">ROS value</span>
            <span className="v">{fmt0(p.ros_value)}</span>
          </div>
          <div>
            <span className="kicker">Over repl.</span>
            <span className="v"><Delta value={p.vorp} digits={0} /></span>
          </div>
        </div>

        <section className="stack">
          <div className="kicker">How this projection is built</div>
          <ol className="math-steps">
            <li>
              <div className="stack" style={{ gap: 8 }}>
                <span><strong>Preseason sources</strong>, blended with weights learned in the backtest:</span>
                {p.sources.map((s) => (
                  <div key={s.source} style={{ display: "grid", gridTemplateColumns: "150px 1fr 54px 52px", gap: 10, alignItems: "center" }}>
                    <span className="small">{s.source}</span>
                    <Bar value={s.fp_pg} max={maxSrc} label={`${s.source} ${s.fp_pg.toFixed(1)}`} />
                    <span className="small num" style={{ textAlign: "right" }}>{fmt1(s.fp_pg)}</span>
                    <span className="tiny muted num" style={{ textAlign: "right" }}>{pct(s.weight)} wt</span>
                  </div>
                ))}
                <span className="small">Blend: <strong className="num">{fmt1(preBlend)}</strong> FP/G preseason.</span>
              </div>
            </li>
            <li>
              <span>
                <strong>This season</strong>: {p.games_played} game{p.games_played === 1 ? "" : "s"}
                {p.season_fp_pg != null && <> averaging <strong className="num">{fmt1(p.season_fp_pg)}</strong></>}. Recent games
                carry <strong>{pct(p.in_season_weight)}</strong> of the weight (stat-by-stat Bayesian update; steals and blocks need more games to
                move than points or rebounds).
              </span>
            </li>
            <li>
              <div className="stack" style={{ gap: 6 }}>
                <span>
                  <strong>Bounded adjustments</strong> from injuries, role and team context (minutes capped at −20%/+15%; injuries only change games played):
                </span>
                {p.adjustments.length === 0 && <span className="small muted">None — the projection is purely statistical.</span>}
                {p.adjustments.map((a, i) => (
                  <div key={i} className="callout neutral small">
                    <div className="row between">
                      <strong>{a.label}</strong>
                      <span className="num">
                        {a.minutes_pct ? <Delta value={a.minutes_pct * 100} digits={1} suffix="% min" /> : null}
                        {a.games_out ? <span className="neg"> {fmt1(a.games_out)} games out</span> : null}
                      </span>
                    </div>
                    <div className="muted">{a.detail}</div>
                  </div>
                ))}
              </div>
            </li>
            <li>
              <span>
                <strong>Result</strong>: <strong className="num">{fmt1(p.fp_pg)}</strong> FP per game in {fmt1(p.mpg)} minutes, available for{" "}
                {pct(p.availability)} of games when healthy.
              </span>
            </li>
          </ol>
        </section>

        {p.explanation.length > 0 && (
          <section className="stack">
            <div className="kicker">What moved the ML model (FP/G, SHAP)</div>
            <div className="stack" style={{ gap: 7 }}>
              {p.explanation.slice(0, 7).map((e) => (
                <div key={e.factor} style={{ display: "grid", gridTemplateColumns: "170px 1fr 50px", gap: 10, alignItems: "center" }}>
                  <span className="small">{e.factor}</span>
                  <Diverging value={e.fp_pg} max={maxEx} label={`${e.factor} ${signed(e.fp_pg)}`} />
                  <span className="small num" style={{ textAlign: "right" }}><Delta value={e.fp_pg} /></span>
                </div>
              ))}
            </div>
            <p className="tiny muted" style={{ margin: 0 }}>
              Contributions to the ML model's correction of the statistical baseline, converted to fantasy points per game.
            </p>
          </section>
        )}

        <section className="stack">
          <div className="kicker">Projected per game</div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  {["MIN", "PTS", "REB", "AST", "STL", "BLK", "TO", "3PM"].map((k) => <th key={k} className="n">{k}</th>)}
                  <th className="n">FG%</th>
                  <th className="n">FT%</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  {["MIN", "PTS", "REB", "AST", "STL", "BLK", "TO", "3PM"].map((k) => <td key={k} className="n">{fmt1(p.per_game[k])}</td>)}
                  <td className="n">{p.fg_pct != null ? (p.fg_pct * 100).toFixed(1) : "–"}</td>
                  <td className="n">{p.ft_pct != null ? (p.ft_pct * 100).toFixed(1) : "–"}</td>
                </tr>
              </tbody>
            </table>
          </div>
          {isPoints && (
            <div className="row wrap small">
              {Object.entries(p.fp_breakdown)
                .filter(([, v]) => Math.abs(v) >= 0.05)
                .sort((a, b) => b[1] - a[1])
                .map(([k, v]) => (
                  <span key={k} className={`badge ${v < 0 ? "neg" : ""}`}>
                    {k} {signed(v)}
                  </span>
                ))}
            </div>
          )}
        </section>

        {last.length > 0 && (
          <section className="stack">
            <div className="row between">
              <div className="kicker">Last {last.length} games</div>
              <span className="tiny muted">dashed line = projection</span>
            </div>
            <GameColumns values={last.map((g) => g.fp)} projection={p.fp_pg} />
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th className="n">MIN</th>
                    <th className="n">FP</th>
                    {["PTS", "REB", "AST", "STL", "BLK", "TO", "3PM"].map((k) => <th key={k} className="n">{k}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {p.game_log.slice(0, 8).map((g) => (
                    <tr key={`${g.season}-${g.period}`}>
                      <td>{g.date ? shortDate(g.date) : `${g.season - 1}–${String(g.season).slice(2)}`}<span className="tiny muted"> {g.team}</span></td>
                      <td className="n">{g.min}</td>
                      <td className="n strong">{fmt1(g.fp)}</td>
                      {["PTS", "REB", "AST", "STL", "BLK", "TO", "3PM"].map((k) => <td key={k} className="n">{g[k] as number}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {p.seasons.length > 0 && (
          <section className="stack">
            <div className="kicker">Season history</div>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Season</th>
                    <th>Team</th>
                    <th className="n">GP</th>
                    <th className="n">MPG</th>
                    <th className="n">FP/G</th>
                    <th className="n">PTS</th>
                    <th className="n">REB</th>
                    <th className="n">AST</th>
                  </tr>
                </thead>
                <tbody>
                  {[...p.seasons].reverse().map((s) => (
                    <tr key={s.season}>
                      <td>{s.label}</td>
                      <td>{s.team}</td>
                      <td className="n">{s.gp}</td>
                      <td className="n">{fmt1(s.mpg)}</td>
                      <td className="n strong">{fmt1(s.fp_pg)}</td>
                      <td className="n">{fmt1(s.PTS as number)}</td>
                      <td className="n">{fmt1(s.REB as number)}</td>
                      <td className="n">{fmt1(s.AST as number)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {p.trend.length > 1 && (
          <section className="row between">
            <div className="stack" style={{ gap: 2 }}>
              <div className="kicker">Projection trend</div>
              <span className="small muted">{p.trend.length} daily snapshots since {shortDate(p.trend[0]!.as_of)}</span>
            </div>
            <Sparkline values={p.trend.map((t) => t.fp_pg)} label="Projected FP/G over time" />
          </section>
        )}

        {p.news.length > 0 && (
          <section className="stack">
            <div className="kicker">News (shown for context, not used in the math)</div>
            <ul className="list">
              {p.news.map((n) => (
                <li key={n.id}>
                  <div className="strong">{n.headline}</div>
                  <div className="small muted">{n.description}</div>
                  <div className="tiny faint row">
                    {relTime(n.published)}
                    {n.link && (
                      <a href={n.link} target="_blank" rel="noreferrer" className="row" style={{ gap: 3 }}>
                        ESPN <ExternalLink size={11} />
                      </a>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </section>
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
  const [open, setOpen] = useState(Boolean(p.override));
  const [mins, setMins] = useState(Math.round((p.override?.minutes_pct ?? 0) * 100));
  const [outGames, setOutGames] = useState<string>(p.override?.out_games != null ? String(p.override.out_games) : "");
  const [note, setNote] = useState(p.override?.note ?? "");
  const done = () => {
    qc.invalidateQueries({ queryKey: ["league", league.id] });
  };
  const save = useMutation({
    mutationFn: () =>
      api(`/api/leagues/${league.id}/players/${p.id}/override`, {
        method: "PUT",
        body: { minutes_pct: mins / 100, out_games: outGames === "" ? null : Number(outGames), note },
      }),
    onSuccess: () => {
      toast("Override saved for the whole league.");
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
  if (!open) {
    return (
      <button className="btn secondary" onClick={() => setOpen(true)} style={{ justifySelf: "start" }}>
        Adjust projection for this league…
      </button>
    );
  }
  return (
    <section className="panel">
      <div className="panel-body stack">
        <div className="kicker">League override</div>
        <p className="small muted" style={{ margin: 0 }}>
          For news the data can't see yet (a confirmed starting role, a known absence). Bounded to ±15% minutes and logged to the league feed.
        </p>
        <div className="field">
          <label htmlFor="ov-min">Minutes adjustment: <span className="num">{mins > 0 ? "+" : ""}{mins}%</span></label>
          <input id="ov-min" type="range" min={-15} max={15} step={1} value={mins} onChange={(e) => setMins(Number(e.target.value))} style={{ accentColor: "var(--accent)" }} />
        </div>
        <div className="grid grid-2">
          <div className="field">
            <label htmlFor="ov-out">Games out (optional)</label>
            <input id="ov-out" className="input" inputMode="numeric" value={outGames} onChange={(e) => setOutGames(e.target.value.replace(/\D/g, ""))} placeholder="Use injury report" />
          </div>
          <div className="field">
            <label htmlFor="ov-note">Reason</label>
            <input id="ov-note" className="input" value={note} maxLength={280} onChange={(e) => setNote(e.target.value)} placeholder="e.g. named starter" />
          </div>
        </div>
        <div className="row">
          <button className="btn" onClick={() => save.mutate()} disabled={save.isPending}>Save override</button>
          {p.override && <button className="btn danger" onClick={() => clear.mutate()} disabled={clear.isPending}>Remove</button>}
        </div>
      </div>
    </section>
  );
}
