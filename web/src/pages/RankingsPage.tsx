import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, Diverging } from "../components/charts";
import { useLeagueCtx } from "../components/Layout";
import { Delta, ErrorBox, LoadingBlock, PageHead, Panel, PlayerName, Segmented } from "../components/ui";
import { fmt0, fmt1, pct, powerMix, record, shortDate, signed } from "../lib/format";
import { useRankings, useWeekly } from "../lib/queries";
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
        desc="Ranked by power score: this season's record counts for the share of the regular season already played, and the projected all-play win rate (each team against every other team in every remaining week, with optimal daily lineups) counts for the rest. In week 1 it's all projection; halfway it's half each. Records and odds from 5,000 simulations of the actual schedule and playoff bracket. Select a row for the breakdown."
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
                  <th className="n" title="Power score: this season's record for the share played, projected all-play for the rest">Power</th>
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
                      <td className="n" title={powerMix(t)}>{pct(t.power_score)}</td>
                      <td className="hide-sm" title={powerMix(t)}><Bar value={t.power_score} max={1} tick={0.5} label={`${pct(t.power_score)} power score`} /></td>
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
      {data && <WeeklyGrid order={data.teams.map((t) => t.team_id)} />}
    </>
  );
}

/** Every team's projected points (or win odds) in each remaining week. */
function WeeklyGrid({ order }: { order: number[] }) {
  const { league, myTeamId, teamName } = useLeagueCtx();
  const { data, error, isLoading } = useWeekly(league.id);
  const [view, setView] = useState<"points" | "odds">("points");
  const isPoints = data?.scoring !== "categories";
  const shown = isPoints ? view : "odds";
  if (error) return <ErrorBox error={error} />;
  if (isLoading || !data) return <LoadingBlock rows={6} />;
  const rank = new Map(order.map((id, i) => [id, i]));
  const teams = [...data.teams].sort((a, b) => (rank.get(a.team_id) ?? 99) - (rank.get(b.team_id) ?? 99));
  const abbrev = new Map(data.teams.map((t) => [t.team_id, t.abbrev || t.name]));
  // Best and worst projection each week, for colouring.
  const hi = data.weeks.map((_, i) => Math.max(...teams.map((t) => t.weeks[i]?.proj ?? -Infinity)));
  const lo = data.weeks.map((_, i) => Math.min(...teams.map((t) => t.weeks[i]?.proj ?? Infinity)));
  const avg = data.weeks.map((_, i) => teams.reduce((s, t) => s + (t.weeks[i]?.proj ?? 0), 0) / Math.max(1, teams.length));
  return (
    <Panel
      title={shown === "points" ? "Projected points by week" : "Win odds by week"}
      flush
      actions={isPoints && (
        <Segmented label="Show" value={view} onChange={setView}
          options={[{ value: "points", label: "Points" }, { value: "odds", label: "Win odds" }]} />
      )}
      foot={shown === "points"
        ? "Each team's expected total with optimal daily lineups. This week adds the points already scored to the days left; two-week weeks and playoff rounds show the combined total. Green is the week's highest, red the lowest. Hover a cell for the opponent, range and win odds."
        : "Chance of beating the scheduled opponent that week. Playoff opponents depend on the bracket, so those weeks are blank until they're set."}
    >
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th className="pin">Team</th>
              {data.weeks.map((w) => (
                <th key={w.matchup_period} className="n" title={`${shortDate(w.start)} – ${shortDate(w.end)}, ${w.days} days`}>
                  <div>{w.playoffs ? "Playoffs" : `Wk ${w.matchup_period}`}{w.current ? " · now" : ""}</div>
                  <div className="tiny faint">{shortDate(w.start)}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {teams.map((t) => (
              <tr key={t.team_id} className={t.team_id === myTeamId ? "mine" : ""}>
                <td className="pin"><Link to={`../team/${t.team_id}`} relative="path" style={{ color: "var(--text)" }}>{t.name}</Link></td>
                {t.weeks.map((c, i) => {
                  const opp = c.opponent_id != null ? teamName(c.opponent_id) : null;
                  const title = [
                    `${t.name}${opp ? ` vs ${opp}` : ""}`,
                    `${fmt0(c.proj)} ± ${fmt0(c.sd)} points`,
                    c.scored != null ? `${fmt0(c.scored)} scored so far` : null,
                    c.win_prob != null ? `${pct(c.win_prob)} to win` : null,
                  ].filter(Boolean).join(" · ");
                  if (shown === "odds") {
                    return (
                      <td key={c.matchup_period} className="n" title={title}>
                        {c.win_prob == null ? <span className="faint">–</span> : (
                          <><span className="tiny faint">{abbrev.get(c.opponent_id!)}</span>{" "}
                            <span className={c.win_prob >= 0.5 ? "up" : "down"}>{pct(c.win_prob)}</span></>
                        )}
                      </td>
                    );
                  }
                  const tone = teams.length > 1 && c.proj === hi[i] ? "up" : teams.length > 1 && c.proj === lo[i] ? "down" : "";
                  return <td key={c.matchup_period} className={`n ${tone}`} title={title}>{fmt0(c.proj)}</td>;
                })}
              </tr>
            ))}
            {shown === "points" && (
              <tr>
                <td className="pin muted">League average</td>
                {avg.map((v, i) => <td key={data.weeks[i]!.matchup_period} className="n muted">{fmt0(v)}</td>)}
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Panel>
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
            <dt>Power score</dt><dd>{pct(t.power_score)} <span className="faint">{powerMix(t)}</span></dd>
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
