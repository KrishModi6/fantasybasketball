import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, Diverging } from "../components/charts";
import { useLeagueCtx } from "../components/Layout";
import { Delta, ErrorBox, LoadingBlock, PageHead, Panel, PlayerName } from "../components/ui";
import { fmt0, fmt1, pct, record, signed } from "../lib/format";
import { useRankings } from "../lib/queries";
import type { TeamStrength } from "../lib/types";

export default function RankingsPage() {
  const { league, myTeamId } = useLeagueCtx();
  const { data, error, isLoading } = useRankings(league.id, Boolean(league.settings));
  const [open, setOpen] = useState<number | null>(myTeamId);
  const isPoints = data?.scoring !== "categories";
  const cols = isPoints ? 11 : 9;
  return (
    <>
      <PageHead
        title="Power rankings"
        desc="Ranked by all-play win rate: each team against every other team in every remaining week, with optimal daily lineups. Records and odds from 5,000 simulations of the actual schedule and playoff bracket. Select a row for the breakdown."
      />
      {error && <ErrorBox error={error} />}
      {isLoading && <LoadingBlock rows={12} />}
      {data && (
        <Panel flush>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="n">#</th>
                  <th>Team</th>
                  <th className="hide-sm">Manager</th>
                  <th className="n">All-play</th>
                  <th className="hide-sm" style={{ width: 120 }} />
                  {isPoints && <th className="n">Pts/wk</th>}
                  {isPoints && <th className="n">vs avg</th>}
                  <th className="n">Record</th>
                  <th className="n hide-sm">Proj.</th>
                  <th className="n">Playoffs</th>
                  <th className="n">Title</th>
                </tr>
              </thead>
              <tbody>
                {data.teams.map((t) => (
                  <Fragment key={t.team_id}>
                    <tr className={`clickable ${open === t.team_id ? "selected" : ""} ${t.team_id === myTeamId ? "mine" : ""}`}
                      onClick={() => setOpen(open === t.team_id ? null : t.team_id)} aria-expanded={open === t.team_id}>
                      <td className="n muted">{t.rank}</td>
                      <td><Link to={`../team/${t.team_id}`} relative="path" onClick={(e) => e.stopPropagation()} style={{ color: "var(--text)" }}>{t.name}</Link></td>
                      <td className="hide-sm muted">{t.owner ?? "–"}</td>
                      <td className="n">{pct(t.power_score)}</td>
                      <td className="hide-sm"><Bar value={t.power_score} max={1} tick={0.5} label={`${pct(t.power_score)} all-play`} /></td>
                      {isPoints && <td className="n">{fmt0(t.ppw)}</td>}
                      {isPoints && <td className="n"><Delta value={t.ppw_vs_avg} digits={0} /></td>}
                      <td className="n">{record(t.wins, t.losses, t.ties)}</td>
                      <td className="n hide-sm">{fmt0(t.proj_wins)}–{fmt0(t.proj_losses)}</td>
                      <td className="n">{pct(t.playoff_odds)}</td>
                      <td className="n">{pct(t.title_odds)}</td>
                    </tr>
                    {open === t.team_id && (
                      <tr className="detail">
                        <td colSpan={cols}><Explanation t={t} isPoints={isPoints} /></td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  );
}

function Explanation({ t, isPoints }: { t: TeamStrength; isPoints: boolean }) {
  const { openPlayer } = useLeagueCtx();
  const ex = t.explanation;
  const sb = ex.stat_breakdown ?? [];
  const maxDiff = Math.max(...sb.map((s) => Math.abs(s.diff)), 1);
  return (
    <div className="stack">
      <p className="small">{ex.summary}</p>
      <div className="grid cols-3" style={{ alignItems: "start" }}>
        {isPoints ? (
          <div className="table-wrap" style={{ border: "1px solid var(--border)" }}>
            <table className="data">
              <thead><tr><th>Stat</th><th className="n">Pts/wk</th><th className="n">League</th><th className="n">Diff</th><th /></tr></thead>
              <tbody>
                {sb.map((s) => (
                  <tr key={s.stat}>
                    <td>{s.stat}</td>
                    <td className="n">{fmt0(s.pts_per_week)}</td>
                    <td className="n muted">{fmt0(s.league_avg)}</td>
                    <td className="n"><Delta value={s.diff} digits={0} /></td>
                    <td style={{ width: 90 }}><Diverging value={s.diff} max={maxDiff} label={`${s.stat} ${signed(s.diff, 0)}`} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="table-wrap" style={{ border: "1px solid var(--border)" }}>
            <table className="data">
              <thead><tr><th>Category</th><th className="n">Win vs avg team</th></tr></thead>
              <tbody>
                {(ex.category_win_prob ?? []).map((c) => (
                  <tr key={c.category}><td>{c.category}</td><td className={`n ${c.win_prob >= 0.5 ? "up" : "down"}`}>{pct(c.win_prob)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="table-wrap" style={{ border: "1px solid var(--border)" }}>
          <table className="data">
            <thead><tr><th>Player</th><th className="n">Pts/wk</th><th className="n">Share</th><th className="n">Starts</th></tr></thead>
            <tbody>
              {ex.players.slice(0, 10).map((p) => (
                <tr key={p.player_id}>
                  <td><PlayerName name={p.name} onOpen={() => openPlayer(p.player_id)} /></td>
                  <td className="n">{fmt0(p.per_week)}</td>
                  <td className="n muted">{pct(p.share)}</td>
                  <td className="n muted">{fmt0(p.starts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="stack" style={{ gap: 8 }}>
          <dl className="kv">
            <dt>Player-games per week</dt><dd>{fmt1(ex.schedule.player_games_per_week)} <span className="faint">avg {fmt1(ex.schedule.league_avg)}</span></dd>
            <dt>Empty starting slots per week</dt><dd>{fmt1(ex.depth.empty_slots_per_week)} <span className="faint">avg {fmt1(ex.depth.league_avg_empty)}</span></dd>
            <dt>Playable games on bench per week</dt><dd>{fmt1(ex.depth.bench_games_per_week)} <span className="faint">avg {fmt1(ex.depth.league_avg_bench)}</span></dd>
          </dl>
          {ex.injuries.length > 0 && (
            <div className="table-wrap" style={{ border: "1px solid var(--border)" }}>
              <table className="data">
                <thead><tr><th>Injured</th><th className="n">Games</th><th className="n">Pts lost</th></tr></thead>
                <tbody>
                  {ex.injuries.map((i) => (
                    <tr key={i.player_id}>
                      <td><PlayerName name={i.name} injury={i.status} onOpen={() => openPlayer(i.player_id)} /></td>
                      <td className="n">{fmt0(i.games_out)}</td>
                      <td className="n down">−{fmt0(i.fp_lost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
