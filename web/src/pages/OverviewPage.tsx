import { Link } from "react-router-dom";
import { useLeagueCtx } from "../components/Layout";
import { Delta, Empty, ErrorBox, Kpi, Kpis, LoadingBlock, Notice, PageHead, Panel, PlayerName } from "../components/ui";
import { fmt0, pct, powerMix, record, relTime, shortDate } from "../lib/format";
import { useActivity, useClaimTeam, useNews, useRankings, useTeamReport } from "../lib/queries";
import { useToast } from "../lib/toast";
import type { TeamStrength } from "../lib/types";

export function OverviewPage() {
  const { league, myTeamId } = useLeagueCtx();
  const rankings = useRankings(league.id, Boolean(league.settings));
  const teams = rankings.data?.teams ?? [];
  const mine = teams.find((t) => t.team_id === myTeamId) ?? null;
  const s = league.settings;
  const isPoints = s?.scoring.kind !== "categories";
  const opp = mine?.current ? teams.find((t) => t.team_id === mine.current!.opponent_id) : undefined;

  return (
    <>
      <PageHead
        title={mine ? mine.name : league.name}
        desc={mine ? mine.explanation.summary : "League view. Claim your team to get recommendations for it."}
      />
      {myTeamId == null && s && <ClaimTeam />}
      {rankings.error && <ErrorBox error={rankings.error} />}
      {rankings.isLoading && <LoadingBlock rows={10} />}
      {mine && (
        <Kpis label="Your team">
          <Kpi k="Power rank" v={`${mine.rank} / ${teams.length}`} s={`${pct(mine.power_score)} power score`} />
          {isPoints && <Kpi k="Points per week" v={fmt0(mine.ppw)} s={<><Delta value={mine.ppw_vs_avg} digits={0} /> vs league average</>} />}
          <Kpi k="Projected record" v={`${fmt0(mine.proj_wins)}–${fmt0(mine.proj_losses)}`} s={`currently ${record(mine.wins, mine.losses, mine.ties)}`} />
          <Kpi k="Playoff odds" v={pct(mine.playoff_odds)} s={mine.avg_seed ? `average seed ${mine.avg_seed.toFixed(1)}` : "5,000 simulated seasons"} />
          <Kpi k="Title odds" v={pct(mine.title_odds)} />
          {mine.current && (
            <Kpi k={`Week ${mine.current.matchup_period} win probability`} v={pct(mine.current.win_prob)}
              tone={mine.current.win_prob >= 0.5 ? "up" : "down"}
              s={`${fmt0(mine.current.proj_score)} – ${fmt0(mine.current.opp_proj_score)} vs ${opp?.name ?? "opponent"}`} />
          )}
        </Kpis>
      )}
      {rankings.data && (
        <div className="grid cols-main" style={{ alignItems: "start" }}>
          <div className="stack">
            {mine?.current && opp && <ThisWeek me={mine} opp={opp} />}
            <Panel title="Power rankings" flush actions={<Link to="rankings" className="small">Details</Link>}>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th className="n">#</th><th>Team</th>{isPoints && <th className="n">Pts/wk</th>}{isPoints && <th className="n hide-sm">vs avg</th>}
                      <th className="n">Power</th><th className="n hide-sm">Record</th><th className="n hide-sm">Proj.</th><th className="n">Playoffs</th><th className="n">Title</th>
                    </tr>
                  </thead>
                  <tbody>
                    {teams.map((t) => (
                      <tr key={t.team_id} className={t.team_id === myTeamId ? "mine" : ""}>
                        <td className="n muted">{t.rank}</td>
                        <td><Link to={`team/${t.team_id}`} style={{ color: "var(--text)" }}>{t.name}</Link></td>
                        {isPoints && <td className="n">{fmt0(t.ppw)}</td>}
                        {isPoints && <td className="n hide-sm"><Delta value={t.ppw_vs_avg} digits={0} /></td>}
                        <td className="n" title={powerMix(t)}>{pct(t.power_score)}</td>
                        <td className="n hide-sm">{record(t.wins, t.losses, t.ties)}</td>
                        <td className="n hide-sm">{fmt0(t.proj_wins)}–{fmt0(t.proj_losses)}</td>
                        <td className="n">{pct(t.playoff_odds)}</td>
                        <td className="n">{pct(t.title_odds)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="panel-foot">Power: this season's record for the share of the season played, plus the expected share of wins against every team in every remaining week for the rest. Odds from 5,000 simulated seasons of the actual schedule.</p>
            </Panel>
            {myTeamId != null && <QuickMoves teamId={myTeamId} />}
          </div>
          <div className="stack">
            <ActivityPanel />
            <NewsPanel />
          </div>
        </div>
      )}
    </>
  );
}

function ThisWeek({ me, opp }: { me: TeamStrength; opp: TeamStrength }) {
  const c = me.current!;
  return (
    <Panel title={`Week ${c.matchup_period} matchup`} flush
      actions={<Link to={`matchups?a=${me.team_id}&b=${c.opponent_id}`} className="small">Simulate</Link>}>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>Team</th><th className="n">Projected</th><th className="n">Win</th><th style={{ width: "40%" }} className="hide-sm" /></tr></thead>
          <tbody>
            <tr className="mine">
              <td>{me.name}</td><td className="n">{fmt0(c.proj_score)}</td><td className="n strong">{pct(c.win_prob)}</td>
              <td className="hide-sm" rowSpan={2}>
                <div className="wpbar" role="img" aria-label={`${me.name} ${pct(c.win_prob)}`}><span style={{ width: `${c.win_prob * 100}%` }} /></div>
              </td>
            </tr>
            <tr>
              <td>{opp.name}</td><td className="n">{fmt0(c.opp_proj_score)}</td><td className="n">{pct(1 - c.win_prob)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function ClaimTeam() {
  const { league, teams, teamName } = useLeagueCtx();
  const toast = useToast();
  const claim = useClaimTeam(league.id);
  const pick = (id: number) =>
    claim.mutate(id, { onSuccess: () => toast(`Saved. Courtside will open ${teamName(id)} as your team from now on.`) });
  return (
    <Notice>
      <div className="row wrap between">
        <span>Which team is yours? Pick it once and Courtside remembers it, with lineup advice, add/drop moves and trade ideas for it.</span>
        <select className="select" defaultValue="" onChange={(e) => e.target.value && pick(Number(e.target.value))} aria-label="Claim a team">
          <option value="" disabled>Choose team…</option>
          {teams.map((t) => <option key={t.id} value={t.id} disabled={Boolean(t.claimed_by)}>{t.name}{t.claimed_by ? ` (${t.claimed_by})` : ""}</option>)}
        </select>
      </div>
      {claim.error && <div className="error-text">{(claim.error as Error).message}</div>}
    </Notice>
  );
}

function QuickMoves({ teamId }: { teamId: number }) {
  const { league, openPlayer } = useLeagueCtx();
  const { data, isLoading } = useTeamReport(league.id, teamId);
  return (
    <Panel title="Best add/drop moves" flush actions={<Link to={`team/${teamId}?tab=improve`} className="small">All suggestions</Link>}>
      {isLoading && <div style={{ padding: 10 }}><LoadingBlock rows={3} /></div>}
      {data && data.moves.length === 0 && <Empty title="No free agent improves this roster" />}
      {data && data.moves.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Add</th><th>Drop</th><th className="hide-sm">Why</th><th className="n">Pts/wk</th></tr></thead>
            <tbody>
              {data.moves.slice(0, 4).map((m) => (
                <tr key={m.add + m.drop}>
                  <td><PlayerName name={m.add_name} onOpen={() => openPlayer(m.add)} /></td>
                  <td><PlayerName name={m.drop_name} onOpen={() => openPlayer(m.drop)} /></td>
                  <td className="hide-sm muted">{m.reasons.join(", ")}</td>
                  <td className="n"><Delta value={m.delta_per_week} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function ActivityPanel() {
  const { league } = useLeagueCtx();
  const { data } = useActivity(league.id);
  return (
    <Panel title="League activity">
      {data && data.length === 0 && <p className="small muted">No activity yet.</p>}
      <ul className="list feed">
        {data?.slice(0, 10).map((a) => (
          <li key={a.id} className="small">
            <time dateTime={a.created_at}>{relTime(a.created_at)}</time>
            <span className={a.kind === "injury" ? "warn" : a.kind === "sync_error" ? "down" : ""}>{a.message}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function NewsPanel() {
  const { league, openPlayer, teamName } = useLeagueCtx();
  const { data } = useNews(league.id);
  const items = (data ?? []).slice().sort((a, b) =>
    Number(Boolean(b.players?.some((p) => p.fantasy_team_id))) - Number(Boolean(a.players?.some((p) => p.fantasy_team_id))));
  return (
    <Panel title="NBA news" note="Shown for context. Headlines do not change projections.">
      {items.length === 0 && <p className="small muted">No recent headlines.</p>}
      <ul className="list">
        {items.slice(0, 8).map((n) => (
          <li key={n.id} className="small">
            <div>{n.link ? <a href={n.link} target="_blank" rel="noreferrer" style={{ color: "var(--text)" }}>{n.headline}</a> : n.headline}</div>
            <div className="tiny muted">
              {n.published ? shortDate(n.published) : ""}
              {n.players?.map((p) => (
                <span key={p.id}>
                  {" · "}<button className="linkbtn muted" onClick={() => openPlayer(p.id)}>{p.name}</button>
                  {p.fantasy_team_id ? ` (${teamName(p.fantasy_team_id)})` : " (FA)"}
                </span>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
