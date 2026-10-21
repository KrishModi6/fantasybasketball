import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, ExternalLink, Users } from "lucide-react";
import { Bar } from "../components/charts";
import { useLeagueCtx } from "../components/Layout";
import { Delta, Empty, ErrorBox, LoadingBlock, PageHead, Panel } from "../components/ui";
import { api } from "../lib/api";
import { fmt0, pct, record, relTime, shortDate } from "../lib/format";
import { useActivity, useNews, useRankings, useTeamReport } from "../lib/queries";
import type { TeamStrength } from "../lib/types";

export function OverviewPage() {
  const { league, myTeamId, teamName } = useLeagueCtx();
  const rankings = useRankings(league.id, Boolean(league.settings));
  const teams = rankings.data?.teams ?? [];
  const mine = teams.find((t) => t.team_id === myTeamId) ?? null;
  const week = league.settings?.current_matchup_period;
  const isPoints = league.settings?.scoring.kind !== "categories";

  return (
    <>
      <PageHead
        kicker={`Week ${week ?? "–"}${league.settings?.today ? ` · ${shortDate(league.settings.today, true)}` : ""}`}
        title={mine ? mine.name : league.name}
        lede={mine ? mine.explanation.summary : "League-wide view. Claim your team to get personal recommendations."}
        actions={
          <Link to="league" className="btn secondary show-sm">
            <Users /> League hub
          </Link>
        }
      />
      {!myTeamId && league.settings && <ClaimTeam />}
      {rankings.error && <ErrorBox error={rankings.error} />}
      {rankings.isLoading && <LoadingBlock rows={10} />}
      {rankings.data && (
        <div className="grid grid-main" style={{ alignItems: "start" }}>
          <div className="stack" style={{ gap: 18 }}>
            {mine?.current && <Scoreboard me={mine} opp={teams.find((t) => t.team_id === mine.current!.opponent_id)} week={week ?? 0} />}
            {mine && (
              <div className="stat-strip panel" style={{ borderRadius: "var(--r-lg)" }}>
                <div>
                  <span className="kicker">Power rank</span>
                  <span className="v">#{mine.rank}<small>of {teams.length}</small></span>
                </div>
                <div>
                  <span className="kicker">Proj. record</span>
                  <span className="v">{fmt0(mine.proj_wins)}–{fmt0(mine.proj_losses)}</span>
                </div>
                <div>
                  <span className="kicker">Playoffs</span>
                  <span className="v">{pct(mine.playoff_odds)}</span>
                </div>
                <div>
                  <span className="kicker">Title</span>
                  <span className="v">{pct(mine.title_odds)}</span>
                </div>
              </div>
            )}
            <Panel title="Power rankings" kicker="All-play win rate, rest of season" actions={<Link className="btn ghost sm" to="rankings">Full table <ArrowRight /></Link>} flush>
              <ol className="list" style={{ padding: "4px 16px 8px" }}>
                {teams.map((t, i) => (
                  <li key={t.team_id} className="rise" style={{ "--i": i, display: "grid", gridTemplateColumns: "40px minmax(0,1fr) 120px 52px", gap: 12, alignItems: "center" } as React.CSSProperties}>
                    <span className={`rank-num ${t.rank <= 3 ? "top" : ""}`}>{t.rank}</span>
                    <span style={{ minWidth: 0 }}>
                      <Link to={`team/${t.team_id}`} className="strong" style={{ textDecoration: "none" }}>
                        {t.name}
                      </Link>
                      {t.team_id === myTeamId && <span className="badge accent" style={{ marginLeft: 8 }}>You</span>}
                      <div className="tiny muted">
                        {record(t.wins, t.losses, t.ties)} · {isPoints ? `${fmt0(t.ppw)} pts/wk` : `${pct(t.power_score)} all-play`}
                      </div>
                    </span>
                    <Bar value={t.power_score} max={1} tick={0.5} accent={t.team_id === myTeamId} label={`All-play ${pct(t.power_score)}`} />
                    <span className="small num" style={{ textAlign: "right" }}>{pct(t.playoff_odds)}</span>
                  </li>
                ))}
              </ol>
              <div className="tiny muted" style={{ padding: "0 16px 12px" }}>Right column: playoff odds from 5,000 simulated seasons.</div>
            </Panel>
            {myTeamId != null && <QuickMoves teamId={myTeamId} />}
          </div>
          <div className="stack" style={{ gap: 18 }}>
            <ActivityPanel />
            <NewsPanel teamName={teamName} />
          </div>
        </div>
      )}
    </>
  );
}

function Scoreboard({ me, opp, week }: { me: TeamStrength; opp?: TeamStrength; week: number }) {
  const c = me.current!;
  return (
    <section className="scoreboard" aria-label="This week's matchup">
      <div className="row between">
        <span className="kicker">Week {week} · projected final</span>
        <span className="kicker">Win probability {pct(c.win_prob)}</span>
      </div>
      <div className="teams">
        <div className="side">
          <span className="tname">{me.name}</span>
          <span className="score">{fmt0(c.proj_score)}</span>
        </div>
        <span className="vs">VS</span>
        <div className="side right">
          <span className="tname">{opp?.name ?? "Opponent"}</span>
          <span className="score">{fmt0(c.opp_proj_score)}</span>
        </div>
      </div>
      <div className="wp">
        <div className="wpbar" role="img" aria-label={`Win probability ${pct(c.win_prob)}`}>
          <span style={{ width: `${c.win_prob * 100}%` }} />
        </div>
        <Link to={`matchups?a=${me.team_id}&b=${c.opponent_id}`} className="small" style={{ color: "inherit", opacity: 0.8 }}>
          Simulate this matchup in detail →
        </Link>
      </div>
    </section>
  );
}

function ClaimTeam() {
  const { league, teams } = useLeagueCtx();
  const qc = useQueryClient();
  const claim = useMutation({
    mutationFn: (team_id: number) => api(`/api/leagues/${league.id}/members/me/team`, { body: { team_id } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["league", league.id] }),
  });
  return (
    <div className="callout row wrap between" style={{ marginBottom: 18 }}>
      <span><strong>Which team is yours?</strong> Claim it for a personal dashboard, lineup advice and trade suggestions.</span>
      <select className="select" style={{ width: 240 }} defaultValue="" onChange={(e) => e.target.value && claim.mutate(Number(e.target.value))} aria-label="Claim a team">
        <option value="" disabled>Choose your team…</option>
        {teams.map((t) => (
          <option key={t.id} value={t.id} disabled={Boolean(t.claimed_by)}>
            {t.name}
            {t.claimed_by ? ` (${t.claimed_by})` : ""}
          </option>
        ))}
      </select>
      {claim.error && <ErrorBox error={claim.error} />}
    </div>
  );
}

function QuickMoves({ teamId }: { teamId: number }) {
  const { league, openPlayer } = useLeagueCtx();
  const { data, isLoading } = useTeamReport(league.id, teamId);
  return (
    <Panel title="Best moves right now" kicker="Add / drop, rest of season" actions={<Link className="btn ghost sm" to={`team/${teamId}?tab=improve`}>All suggestions <ArrowRight /></Link>}>
      {isLoading && <LoadingBlock rows={3} />}
      {data && data.moves.length === 0 && <Empty title="No upgrades on waivers">Your roster beats every available free agent at the moment.</Empty>}
      <ul className="list">
        {data?.moves.slice(0, 3).map((m) => (
          <li key={m.add + m.drop} className="row between wrap">
            <span>
              Add <button className="btn ghost sm" onClick={() => openPlayer(m.add)} style={{ padding: "0 4px" }}><strong>{m.add_name}</strong></button>
              , drop <button className="btn ghost sm" onClick={() => openPlayer(m.drop)} style={{ padding: "0 4px" }}>{m.drop_name}</button>
              <div className="tiny muted">{m.reasons.join(" · ")}</div>
            </span>
            <span className="num strong"><Delta value={m.delta_per_week} /> <span className="tiny muted">pts/wk</span></span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function ActivityPanel() {
  const { league } = useLeagueCtx();
  const { data } = useActivity(league.id);
  return (
    <Panel title="League feed" kicker="Live">
      {data && data.length === 0 && <p className="small muted">Nothing yet.</p>}
      <ul className="list">
        {data?.slice(0, 10).map((a) => (
          <li key={a.id} className={`feed-item ${a.kind}`}>
            <div>
              <div className="small">{a.message}</div>
              <div className="tiny faint">{relTime(a.created_at)}</div>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function NewsPanel({ teamName }: { teamName: (id: number | null) => string }) {
  const { league, openPlayer } = useLeagueCtx();
  const { data } = useNews(league.id);
  const items = (data ?? []).slice().sort((a, b) => Number(Boolean(b.players?.some((p) => p.fantasy_team_id))) - Number(Boolean(a.players?.some((p) => p.fantasy_team_id))));
  return (
    <Panel title="NBA news" kicker="Context only, not used in projections">
      <ul className="list">
        {items.slice(0, 8).map((n) => (
          <li key={n.id}>
            <div className="small strong">{n.headline}</div>
            <div className="row wrap tiny" style={{ gap: 6, marginTop: 4 }}>
              {n.players?.map((p) => (
                <button key={p.id} className="badge" style={{ border: 0, cursor: "pointer" }} onClick={() => openPlayer(p.id)}>
                  {p.name}{p.fantasy_team_id ? ` · ${teamName(p.fantasy_team_id)}` : " · FA"}
                </button>
              ))}
              <span className="faint">{relTime(n.published)}</span>
              {n.link && <a className="faint" href={n.link} target="_blank" rel="noreferrer" aria-label="Open on ESPN"><ExternalLink size={11} /></a>}
            </div>
          </li>
        ))}
      </ul>
      {items.length === 0 && <p className="small muted">No recent headlines.</p>}
      <p className="tiny muted" style={{ margin: "10px 0 0" }}>{items.length} headlines from ESPN.</p>
    </Panel>
  );
}
