import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, Diverging } from "../components/charts";
import { useLeagueCtx } from "../components/Layout";
import { Delta, ErrorBox, LoadingBlock, PageHead, Panel, PlayerName, Segmented, Term } from "../components/ui";
import { fmt0, fmt1, pct, powerMix, projRecord, projRecordNote, record, shortDate, signed } from "../lib/format";
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
                  <th className="n"><Term t="power">Power</Term></th>
                  <th className="hide-sm" style={{ width: 120 }} />
                  {isPoints && <th className="n"><Term t="pts_wk_team" /></th>}
                  {isPoints && <th className="n"><Term t="vs_avg" /></th>}
                  <th className="n">Record</th>
                  <th className="n hide-sm"><Term t="proj_record" /></th>
                  <th className="n"><Term t="playoffs" /></th>
                  <th className="n"><Term t="title" /></th>
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
                      <td className="n hide-sm" title={projRecordNote(t)}>{projRecord(t)}</td>
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

const RESULT = { W: "Won", L: "Lost", T: "Tied" } as const;

/** A played week's result: W, L or T, in a fixed-width slot so the scores above and below line up. */
function Result({ r }: { r?: "W" | "L" | "T" | null }) {
  return <span className={`result tiny strong ${r === "W" ? "up" : r === "L" ? "down" : "muted"}`}>{r}</span>;
}

/** Every team's final score (and W/L) in the weeks played, then its projected points (or win odds) in each week left. */
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
  // Best and worst projection each week left, for colouring (a played week shows W or L instead).
  const hi = data.weeks.map((_, i) => Math.max(...teams.map((t) => t.weeks[i]?.proj ?? -Infinity)));
  const lo = data.weeks.map((_, i) => Math.min(...teams.map((t) => t.weeks[i]?.proj ?? Infinity)));
  const avg = data.weeks.map((w, i) => {
    const scores = teams.map((t) => (w.final ? t.weeks[i]?.final : t.weeks[i]?.proj)).filter((v): v is number => v != null);
    return scores.length ? scores.reduce((s, v) => s + v, 0) / scores.length : null;
  });
  return (
    <Panel
      title={shown === "points" ? "Projected points by week" : "Win odds by week"}
      flush
      actions={isPoints && (
        <Segmented label="Show" value={view} onChange={setView}
          options={[{ value: "points", label: "Points" }, { value: "odds", label: "Win odds" }]} />
      )}
      foot={shown === "points"
        ? "Weeks already played show the final score, with W or L. The rest are each team's expected total with optimal daily lineups: this week adds the points already scored to the days left; two-week weeks and playoff rounds show the combined total. Green is the week's highest, red the lowest. Hover a cell for the opponent, range and win odds."
        : "Weeks already played show who won. The rest are the chance of beating the scheduled opponent that week. Playoff opponents depend on the bracket, so those weeks are blank until they're set."}
    >
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th className="pin">Team</th>
              {data.weeks.map((w) => (
                <th key={w.matchup_period} className="n" title={`${shortDate(w.start)} – ${shortDate(w.end)}, ${w.days} days${w.final ? ", final" : ""}`}>
                  <div>{w.playoffs ? "Playoffs" : `Wk ${w.matchup_period}`}{w.current ? " · now" : w.final ? " · final" : ""}</div>
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
                  if (data.weeks[i]!.final) {
                    const title = [
                      `${t.name}${opp ? ` vs ${opp}` : ""}`,
                      c.final != null ? `Final ${fmt0(c.final)}${c.opp_final != null ? `–${fmt0(c.opp_final)}` : ""}` : null,
                      c.result ? RESULT[c.result] : null,
                    ].filter(Boolean).join(" · ");
                    return (
                      <td key={c.matchup_period} className="n" title={title}>
                        {shown === "odds"
                          ? (c.result ? <><span className="tiny faint">{abbrev.get(c.opponent_id!)}</span> <Result r={c.result} /></> : <span className="faint">–</span>)
                          : (c.final == null ? <span className="faint">–</span> : <>{fmt0(c.final)}<Result r={c.result} /></>)}
                      </td>
                    );
                  }
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
                {avg.map((v, i) => <td key={data.weeks[i]!.matchup_period} className="n muted">{fmt0(v)}{data.weeks[i]!.final && <Result />}</td>)}
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
          <div className="table-wrap boxed">
            <table className="data">
              <thead><tr><th>Stat</th><th className="n"><Term t="pts_wk_team" /></th><th className="n">League</th><th className="n"><Term t="stat_diff" /></th><th /></tr></thead>
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
          <div className="table-wrap boxed">
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
        <div className="table-wrap boxed">
          <table className="data">
            <thead><tr><th>Player</th><th className="n"><Term t="pts_wk_player" /></th><th className="n"><Term t="share" /></th><th className="n"><Term t="starts_ros" /></th></tr></thead>
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
            <dt><Term t="power" /></dt><dd>{pct(t.power_score)} <span className="faint">{powerMix(t)}</span></dd>
            <dt><Term t="player_games_wk" /></dt><dd>{fmt1(ex.schedule.player_games_per_week)} <span className="faint">avg {fmt1(ex.schedule.league_avg)}</span></dd>
            <dt><Term t="empty_slots">Empty starting slots per week</Term></dt><dd>{fmt1(ex.depth.empty_slots_per_week)} <span className="faint">avg {fmt1(ex.depth.league_avg_empty)}</span></dd>
            <dt><Term t="bench_games">Playable games on bench per week</Term></dt><dd>{fmt1(ex.depth.bench_games_per_week)} <span className="faint">avg {fmt1(ex.depth.league_avg_bench)}</span></dd>
          </dl>
          {ex.injuries.length > 0 && (
            <div className="table-wrap boxed">
              <table className="data">
                <thead><tr><th>Injured</th><th className="n">Games</th><th className="n"><Term t="pts_lost" /></th></tr></thead>
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
