import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Bar, Diverging } from "../components/charts";
import { useLeagueCtx } from "../components/Layout";
import { Delta, ErrorBox, LoadingBlock, PageHead } from "../components/ui";
import { fmt0, fmt1, pct, record, signed } from "../lib/format";
import { useRankings } from "../lib/queries";
import type { TeamStrength } from "../lib/types";

export default function RankingsPage() {
  const { league, myTeamId } = useLeagueCtx();
  const { data, error, isLoading } = useRankings(league.id, Boolean(league.settings));
  const [open, setOpen] = useState<number | null>(myTeamId);
  const isPoints = data?.scoring !== "categories";
  return (
    <>
      <PageHead
        kicker="Team strength model"
        title="Power rankings"
        lede="Ranked by expected all-play win rate: every team against every other team, every remaining week, using optimal daily lineups. Records and odds come from 5,000 simulations of the actual schedule and playoff bracket."
      />
      {error && <ErrorBox error={error} />}
      {isLoading && <LoadingBlock rows={12} />}
      {data && (
        <section className="panel">
          <div className="table-wrap">
            <table className="data">
              <caption className="sr-only">Power rankings with projections; expand a row to see why a team ranks there.</caption>
              <thead>
                <tr>
                  <th style={{ width: 40 }}><span className="sr-only">Expand</span></th>
                  <th className="n">#</th>
                  <th>Team</th>
                  <th style={{ width: 150 }}>All-play</th>
                  {isPoints && <th className="n">Pts / wk</th>}
                  {isPoints && <th className="n hide-sm">vs avg</th>}
                  <th className="n">Record</th>
                  <th className="n hide-sm">Proj.</th>
                  <th className="n">Playoffs</th>
                  <th className="n">Title</th>
                </tr>
              </thead>
              <tbody>
                {data.teams.map((t, i) => (
                  <Fragment key={t.team_id}>
                    <tr className={`clickable rise ${t.team_id === myTeamId ? "mine" : ""}`} style={{ "--i": i } as React.CSSProperties}
                      onClick={() => setOpen(open === t.team_id ? null : t.team_id)}>
                      <td>
                        <button className="btn ghost sm icon" aria-expanded={open === t.team_id} aria-label={`Why ${t.name} ranks #${t.rank}`}
                          onClick={(e) => { e.stopPropagation(); setOpen(open === t.team_id ? null : t.team_id); }}>
                          {open === t.team_id ? <ChevronDown /> : <ChevronRight />}
                        </button>
                      </td>
                      <td className="n"><span className={`rank-num ${t.rank <= 3 ? "top" : ""}`}>{t.rank}</span></td>
                      <td className="player">
                        <div className="player-cell">
                          <Link to={`../team/${t.team_id}`} relative="path" className="nm" onClick={(e) => e.stopPropagation()} style={{ textDecoration: "none" }}>{t.name}</Link>
                          <span className="pos-tag">{t.owner ?? "—"}</span>
                        </div>
                      </td>
                      <td>
                        <div className="row" style={{ gap: 8 }}>
                          <div style={{ flex: 1 }}><Bar value={t.power_score} max={1} tick={0.5} accent={t.team_id === myTeamId} label={`${pct(t.power_score)} all-play`} /></div>
                          <span className="small num">{pct(t.power_score)}</span>
                        </div>
                      </td>
                      {isPoints && <td className="n strong">{fmt0(t.ppw)}</td>}
                      {isPoints && <td className="n hide-sm"><Delta value={t.ppw_vs_avg} digits={0} /></td>}
                      <td className="n">{record(t.wins, t.losses, t.ties)}</td>
                      <td className="n hide-sm">{fmt0(t.proj_wins)}–{fmt0(t.proj_losses)}</td>
                      <td className="n">{pct(t.playoff_odds)}</td>
                      <td className="n">{pct(t.title_odds)}</td>
                    </tr>
                    {open === t.team_id && (
                      <tr>
                        <td colSpan={isPoints ? 10 : 8} style={{ whiteSpace: "normal", background: "var(--paper)", padding: "16px 14px 20px" }}>
                          <Explanation t={t} isPoints={isPoints} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}

function Explanation({ t, isPoints }: { t: TeamStrength; isPoints: boolean }) {
  const { openPlayer } = useLeagueCtx();
  const ex = t.explanation;
  const sb = ex.stat_breakdown ?? [];
  const maxDiff = Math.max(...sb.map((s) => Math.abs(s.diff)), 1);
  const maxShare = Math.max(...ex.players.map((p) => p.share), 0.01);
  return (
    <div className="stack" style={{ gap: 18 }}>
      <p className="lede" style={{ maxWidth: "none" }}><strong>Why #{t.rank}:</strong> {ex.summary}</p>
      <div className="grid grid-3" style={{ alignItems: "start" }}>
        {isPoints ? (
          <div className="stack" style={{ gap: 8 }}>
            <div className="kicker">Points by stat vs league average (per week)</div>
            {sb.map((s) => (
              <div key={s.stat} style={{ display: "grid", gridTemplateColumns: "44px 1fr 46px", gap: 8, alignItems: "center" }}>
                <span className="small strong">{s.stat}</span>
                <Diverging value={s.diff} max={maxDiff} label={`${s.stat} ${signed(s.diff, 0)} vs average`} />
                <span className="small num" style={{ textAlign: "right" }}><Delta value={s.diff} digits={0} /></span>
              </div>
            ))}
          </div>
        ) : (
          <div className="stack" style={{ gap: 8 }}>
            <div className="kicker">Category win probability vs an average opponent</div>
            {(ex.category_win_prob ?? []).map((c) => (
              <div key={c.category} style={{ display: "grid", gridTemplateColumns: "44px 1fr 40px", gap: 8, alignItems: "center" }}>
                <span className="small strong">{c.category}</span>
                <Bar value={c.win_prob} max={1} tick={0.5} />
                <span className="small num">{pct(c.win_prob)}</span>
              </div>
            ))}
          </div>
        )}
        <div className="stack" style={{ gap: 8 }}>
          <div className="kicker">Who produces it (expected, per week)</div>
          {ex.players.slice(0, 9).map((p) => (
            <div key={p.player_id} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 70px 40px", gap: 8, alignItems: "center" }}>
              <button className="btn ghost sm" style={{ justifyContent: "flex-start", padding: 0, height: 22, overflow: "hidden" }} onClick={() => openPlayer(p.player_id)}>{p.name}</button>
              <Bar value={p.share} max={maxShare} />
              <span className="small num" style={{ textAlign: "right" }}>{fmt0(p.per_week)}</span>
            </div>
          ))}
        </div>
        <div className="stack" style={{ gap: 10 }}>
          <div className="kicker">Roster construction</div>
          <dl className="kv">
            <dt>Player-games / week</dt>
            <dd>{fmt1(ex.schedule.player_games_per_week)} <span className="faint">(avg {fmt1(ex.schedule.league_avg)})</span></dd>
            <dt>Empty starting slots / wk</dt>
            <dd>{fmt1(ex.depth.empty_slots_per_week)} <span className="faint">(avg {fmt1(ex.depth.league_avg_empty)})</span></dd>
            <dt>Games stuck on bench / wk</dt>
            <dd>{fmt1(ex.depth.bench_games_per_week)} <span className="faint">(avg {fmt1(ex.depth.league_avg_bench)})</span></dd>
          </dl>
          {ex.injuries.length > 0 && (
            <>
              <div className="kicker">Injury drag</div>
              <ul className="list small">
                {ex.injuries.map((i) => (
                  <li key={i.player_id} className="row between" style={{ padding: "6px 0" }}>
                    <button className="btn ghost sm" style={{ padding: 0, height: 22 }} onClick={() => openPlayer(i.player_id)}>{i.name}</button>
                    <span className="num neg">~{fmt0(i.games_out)} g · −{fmt0(i.fp_lost)} pts</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
