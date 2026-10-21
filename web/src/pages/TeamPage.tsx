import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AlertTriangle, ArrowRightLeft, CalendarDays, Lock } from "lucide-react";
import { RangeBar } from "../components/charts";
import { useLeagueCtx } from "../components/Layout";
import { Delta, Empty, ErrorBox, LoadingBlock, Meter, PageHead, Panel, PlayerCell, Tabs } from "../components/ui";
import { fmt0, fmt1, pct, record, shortDate } from "../lib/format";
import { useLineup, useTeam, useTeamReport } from "../lib/queries";
import type { PlayerRow, TeamDetail } from "../lib/types";

type Tab = "roster" | "lineup" | "improve" | "schedule";

export default function TeamPage() {
  const { teamId } = useParams();
  const { league, myTeamId, teams } = useLeagueCtx();
  const nav = useNavigate();
  const [search, setSearch] = useSearchParams();
  const tid = teamId ? Number(teamId) : myTeamId;
  const tab = (search.get("tab") as Tab) || "roster";
  const setTab = (t: Tab) => {
    const next = new URLSearchParams(search);
    next.set("tab", t);
    setSearch(next, { replace: true });
  };
  const { data, error, isLoading } = useTeam(league.id, tid);

  if (tid == null) {
    return (
      <>
        <PageHead kicker="My team" title="Pick your team" />
        <div className="panel"><Empty title="You haven't claimed a team">Claim yours from the Overview page, or browse any team below.</Empty></div>
        <div className="chips" style={{ marginTop: 16 }}>
          {teams.map((t) => <button key={t.id} className="chip" onClick={() => nav(`${t.id}`)}>{t.name}</button>)}
        </div>
      </>
    );
  }
  const isMine = tid === myTeamId;
  return (
    <>
      <PageHead
        kicker={isMine ? "Your team" : "Team profile"}
        title={data?.name ?? "…"}
        lede={data?.strength?.explanation.summary}
        actions={
          <select className="select" style={{ width: 220 }} value={tid} aria-label="Switch team"
            onChange={(e) => nav(`../team/${e.target.value}${tab !== "roster" ? `?tab=${tab}` : ""}`, { relative: "path" })}>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.name}{t.id === myTeamId ? " (you)" : ""}</option>)}
          </select>
        }
      />
      {error && <ErrorBox error={error} />}
      {isLoading && <LoadingBlock rows={10} />}
      {data && (
        <>
          <div className="stat-strip panel" style={{ marginBottom: 22, borderRadius: "var(--r-lg)" }}>
            <div><span className="kicker">Record</span><span className="v">{record(data.record.wins, data.record.losses, data.record.ties)}</span></div>
            <div><span className="kicker">Power rank</span><span className="v">#{data.strength?.rank ?? "–"}<small>{pct(data.strength?.power_score)} all-play</small></span></div>
            <div><span className="kicker">Proj. pts / week</span><span className="v">{fmt0(data.strength?.ppw)}<small>{data.strength ? <Delta value={data.strength.ppw_vs_avg} digits={0} /> : null}</small></span></div>
            <div><span className="kicker">Playoffs · title</span><span className="v">{pct(data.strength?.playoff_odds)}<small>{pct(data.strength?.title_odds)}</small></span></div>
          </div>
          <Tabs label="Team sections" value={tab} onChange={setTab} tabs={[
            { value: "roster", label: "Roster" },
            { value: "lineup", label: "Daily lineup" },
            { value: "improve", label: isMine ? "Improve my team" : "Improvements" },
            { value: "schedule", label: "Schedule" },
          ]} />
          {tab === "roster" && <Roster team={data} />}
          {tab === "lineup" && <Lineup teamId={tid} />}
          {tab === "improve" && <Improve teamId={tid} />}
          {tab === "schedule" && <Schedule team={data} />}
        </>
      )}
    </>
  );
}

function Roster({ team }: { team: TeamDetail }) {
  const { openPlayer, league } = useLeagueCtx();
  const isPoints = league.settings?.scoring.kind !== "categories";
  const weeks = Math.max(1, team.weeks_remaining);
  return (
    <section className="panel">
      <div className="table-wrap">
        <table className="data">
          <caption className="sr-only">Roster with projections</caption>
          <thead>
            <tr>
              <th>Slot</th>
              <th>Player</th>
              <th className="n">{isPoints ? "FP/G" : "Value/G"}</th>
              <th className="n hide-sm">Season</th>
              <th className="n hide-sm">Last 10</th>
              <th className="n">MPG</th>
              <th className="n">Next 7</th>
              <th className="n">Starts/wk</th>
              <th className="n">Pts/wk</th>
              <th className="n hide-sm">Over repl.</th>
            </tr>
          </thead>
          <tbody>
            {team.roster.map((p, i) => (
              <tr key={p.id} className="rise" style={{ "--i": i } as React.CSSProperties}>
                <td><span className="badge">{p.slot}</span></td>
                <td className="player">
                  <PlayerCell name={p.name} team={p.pro_team} positions={p.positions} injury={p.injury_status} onOpen={() => openPlayer(p.id)}
                    sub={p.games_out > 0 ? <span className="neg">out ~{fmt0(p.games_out)} g</span> : undefined} />
                </td>
                <td className="n strong">{fmt1(isPoints ? p.fp_pg : p.value_pg)}</td>
                <td className="n hide-sm">{fmt1(p.season_fp_pg)}</td>
                <td className="n hide-sm"><Trend p={p} /></td>
                <td className="n">{fmt1(p.mpg)}</td>
                <td className="n">{fmt1(p.games_next7)}</td>
                <td className="n">{fmt1((p.exp_starts ?? 0) / weeks)}</td>
                <td className="n strong">{fmt0(p.pts_per_week)}</td>
                <td className="n hide-sm"><Delta value={p.vorp} digits={0} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="tiny muted" style={{ padding: "8px 16px 12px", margin: 0 }}>
        Starts and points per week come from simulating every remaining day with the optimal lineup, so they reflect positions, schedule overlap and injuries.
      </p>
    </section>
  );
}

function Trend({ p }: { p: PlayerRow }) {
  if (p.recent_fp_pg == null) return <span className="faint">–</span>;
  const d = p.recent_fp_pg - p.fp_pg;
  return <span title={`Last 10 games: ${fmt1(p.recent_fp_pg)}`}>{fmt1(p.recent_fp_pg)} <Delta value={d} /></span>;
}

function Lineup({ teamId }: { teamId: number }) {
  const { league, openPlayer } = useLeagueCtx();
  const { data, isLoading, error } = useLineup(league.id, teamId, 7);
  const [idx, setIdx] = useState(0);
  if (isLoading) return <LoadingBlock rows={8} />;
  if (error) return <ErrorBox error={error} />;
  const days = data?.days ?? [];
  if (!days.length) return <Empty title="No games left in the fantasy season" icon={<CalendarDays />} />;
  const day = days[Math.min(idx, days.length - 1)]!;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="chips" role="tablist" aria-label="Day">
        {days.map((d, i) => (
          <button key={d.period} className="chip" role="tab" aria-selected={i === idx} aria-pressed={i === idx} onClick={() => setIdx(i)}>
            {d.date ? shortDate(d.date, true) : `Day ${d.period}`} · {fmt0(d.expected)}
          </button>
        ))}
      </div>
      <div className="grid grid-main" style={{ alignItems: "start" }}>
        <Panel title="Start" kicker={`Optimal lineup · ${fmt0(day.expected)} projected`} flush>
          {day.starters.length === 0 && <Empty title="Nobody plays today" />}
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Slot</th><th>Player</th><th className="n">Proj</th><th>Confidence</th></tr>
              </thead>
              <tbody>
                {day.starters.map((s) => (
                  <tr key={s.player_id}>
                    <td><span className="badge ink">{s.slot}</span></td>
                    <td className="player">
                      <PlayerCell name={s.name} onOpen={() => openPlayer(s.player_id)} sub={s.alternative ? `over ${s.alternative}` : "no eligible alternative"} />
                    </td>
                    <td className="n strong">{fmt1(s.projected)}</td>
                    <td>{s.alternative ? <Meter value={s.confidence} label={`Probability ${s.name} outscores ${s.alternative}`} /> : <span className="small muted row" style={{ gap: 4 }}><Lock size={12} /> locked in</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <div className="stack" style={{ gap: 18 }}>
          <Panel title="Bench" kicker="Playing, but not starting">
            {day.bench.length === 0 && <p className="small muted" style={{ margin: 0 }}>No one with a game is left on the bench.</p>}
            <ul className="list">
              {day.bench.map((b) => (
                <li key={b.player_id} className="row between">
                  <button className="btn ghost sm" style={{ padding: 0 }} onClick={() => openPlayer(b.player_id)}>{b.name}</button>
                  <span className="num small">{fmt1(b.projected)}</span>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel title="No game" kicker="Off day">
            <p className="small" style={{ margin: 0 }}>{day.no_game.map((n) => n.name).join(", ") || "Everyone plays."}</p>
          </Panel>
          <p className="tiny muted" style={{ margin: 0 }}>
            Confidence is the chance the starter outscores the best eligible bench alternative that day, from each player's projected mean and game-to-game spread.
            Lineups lock individually at tip-off, so check injury news before games start.
          </p>
        </div>
      </div>
    </div>
  );
}

function Improve({ teamId }: { teamId: number }) {
  const { league, openPlayer } = useLeagueCtx();
  const { data, isLoading, error } = useTeamReport(league.id, teamId);
  if (isLoading) return <LoadingBlock rows={8} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  return (
    <div className="grid grid-main" style={{ alignItems: "start" }}>
      <div className="stack" style={{ gap: 18 }}>
        <Panel title="Add / drop" kicker="Ranked by rest-of-season gain, lineup-adjusted" flush>
          {data.moves.length === 0 ? (
            <Empty title="No free agent improves this roster">Every candidate was simulated against your lineup; none adds points over the rest of the season.</Empty>
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr><th>Add</th><th>Drop</th><th className="n">Pts/wk</th><th className="n hide-sm">This week</th><th className="n">ROS</th></tr>
                </thead>
                <tbody>
                  {data.moves.map((m) => (
                    <tr key={m.add + m.drop}>
                      <td className="player">
                        <PlayerCell name={m.add_name} onOpen={() => openPlayer(m.add)} sub={m.on_waivers ? "waivers" : m.reasons.join(" · ")} />
                      </td>
                      <td><button className="btn ghost sm" style={{ padding: 0 }} onClick={() => openPlayer(m.drop)}>{m.drop_name}</button></td>
                      <td className="n strong"><Delta value={m.delta_per_week} /></td>
                      <td className="n hide-sm"><Delta value={m.delta_this_week} /></td>
                      <td className="n"><Delta value={m.delta_ros} digits={0} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Stream this week" kicker={`${data.streaming.days_left} days left in the matchup${data.streaming.acquisition_limit ? ` · ${data.streaming.acquisition_limit} adds per matchup` : ""}`} flush>
          {data.streaming.moves.length === 0 ? (
            <Empty title="No streaming gains this week" />
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Add</th><th>For</th><th className="n">Games</th><th className="n">This week</th><th className="n hide-sm">ROS cost</th></tr></thead>
                <tbody>
                  {data.streaming.moves.map((m) => (
                    <tr key={m.add}>
                      <td><button className="btn ghost sm" style={{ padding: 0 }} onClick={() => openPlayer(m.add)}><strong>{m.add_name}</strong></button></td>
                      <td className="small muted">{m.drop_name}</td>
                      <td className="n">{fmt1(m.games_this_week)}</td>
                      <td className="n strong"><Delta value={m.delta_this_week} /></td>
                      <td className="n hide-sm"><Delta value={-m.ros_cost} digits={0} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
      <Panel title="Weak spots" kicker="Ranked by points at stake">
        {data.weaknesses.length === 0 && <p className="small muted" style={{ margin: 0 }}>No structural weaknesses found.</p>}
        <ul className="list">
          {data.weaknesses.map((w, i) => (
            <li key={i} className="stack" style={{ gap: 3 }}>
              <span className="row strong" style={{ gap: 7 }}>
                {w.kind === "bench_overflow" ? <ArrowRightLeft size={15} className="faint" /> : <AlertTriangle size={15} style={{ color: "var(--warn)" }} />}
                {w.player_id ? <button className="btn ghost sm" style={{ padding: 0, height: 20, fontWeight: 650 }} onClick={() => openPlayer(w.player_id!)}>{w.title}</button> : w.title}
              </span>
              <span className="small muted">{w.detail}</span>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}

function Schedule({ team }: { team: TeamDetail }) {
  const { teamName } = useLeagueCtx();
  const vals = team.upcoming.flatMap((u) => [u.proj_mean - u.proj_sd, u.proj_mean + u.proj_sd]);
  const lo = Math.min(...vals) * 0.95;
  const hi = Math.max(...vals) * 1.03;
  return (
    <Panel title="Upcoming matchups" kicker="Projected score, ±1 standard deviation" flush>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>Week</th><th>Dates</th><th>Opponent</th><th className="n">Proj</th><th style={{ width: "34%" }} className="hide-sm">Range</th></tr></thead>
          <tbody>
            {team.upcoming.map((u) => (
              <tr key={u.matchup_period}>
                <td><strong>{u.playoffs ? "Playoffs" : `Wk ${u.matchup_period}`}</strong></td>
                <td className="small">{shortDate(u.start)} – {shortDate(u.end)}</td>
                <td>{u.opponent_id ? teamName(u.opponent_id) : u.playoffs ? "Bracket TBD" : "—"}</td>
                <td className="n strong">{fmt0(u.proj_mean)} <span className="tiny muted">±{fmt0(u.proj_sd)}</span></td>
                <td className="hide-sm"><RangeBar mean={u.proj_mean} sd={u.proj_sd} lo={lo} hi={hi} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="tiny muted" style={{ padding: "8px 16px 12px", margin: 0 }}>
        Partial weeks only count remaining days. Two-week playoff rounds show their combined total.
      </p>
    </Panel>
  );
}
