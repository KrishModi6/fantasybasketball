import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { RangeBar } from "../components/charts";
import { useLeagueCtx } from "../components/Layout";
import { Delta, Empty, ErrorBox, Kpi, Kpis, LoadingBlock, Meter, PageHead, Panel, PlayerName, SubTabs, Tag, Term } from "../components/ui";
import { fmt0, fmt1, pct, projRecord, projRecordNote, record, shortDate } from "../lib/format";
import { useClaimTeam, useLineup, useTeam, useTeamReport } from "../lib/queries";
import { useToast } from "../lib/toast";
import type { TeamDetail } from "../lib/types";

type Tab = "roster" | "lineup" | "improve" | "schedule";

export default function TeamPage() {
  const { teamId } = useParams();
  const { league, myTeamId, teams, teamName } = useLeagueCtx();
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
  const isPoints = league.settings?.scoring.kind !== "categories";
  const toast = useToast();
  const claim = useClaimTeam(league.id);
  const claimTeam = (id: number) =>
    claim.mutate(id, { onSuccess: () => toast(`Saved. Courtside will open ${teamName(id)} as your team from now on.`) });

  const switcher = (
    <label className="field-inline"><span className="label">Team</span>
      <select className="select" value={tid ?? ""} aria-label="Team"
        onChange={(e) => nav(`/l/${league.id}/team/${e.target.value}${tab !== "roster" ? `?tab=${tab}` : ""}`)}>
        {tid == null && <option value="">Choose…</option>}
        {teams.map((t) => <option key={t.id} value={t.id}>{t.name}{t.id === myTeamId ? " (you)" : ""}</option>)}
      </select>
    </label>
  );

  if (tid == null) {
    return (
      <>
        <PageHead title="Which team is yours?"
          desc="Pick it once and Courtside remembers it: this tab, trades, matchups and the assistant all start from your team." />
        <Panel>
          <div className="form">
            <div className="field" style={{ maxWidth: 360 }}>
              <label htmlFor="my-team">Your team</label>
              <select id="my-team" className="select" defaultValue="" disabled={claim.isPending}
                onChange={(e) => e.target.value && claimTeam(Number(e.target.value))}>
                <option value="" disabled>Choose your team…</option>
                {teams.map((t) => (
                  <option key={t.id} value={t.id} disabled={Boolean(t.claimed_by)}>
                    {t.name}{t.owner_name ? ` (${t.owner_name})` : ""}{t.claimed_by ? `, claimed by ${t.claimed_by}` : ""}
                  </option>
                ))}
              </select>
            </div>
            {claim.error && <ErrorBox error={claim.error} />}
            <div className="small muted">Just looking around? {switcher}</div>
          </div>
        </Panel>
      </>
    );
  }
  const st = data?.strength;
  const viewed = teams.find((t) => t.id === tid);
  const makeMine = myTeamId == null && viewed && (
    <button className="btn primary" onClick={() => claimTeam(tid)} disabled={Boolean(viewed.claimed_by) || claim.isPending}
      title={viewed.claimed_by ? `${viewed.claimed_by} already manages this team` : undefined}>
      This is my team
    </button>
  );
  return (
    <>
      <PageHead title={data?.name ?? "Team"} desc={st?.explanation.summary} actions={<>{makeMine}{switcher}</>} />
      {claim.error && <ErrorBox error={claim.error} />}
      {error && <ErrorBox error={error} />}
      {isLoading && <LoadingBlock rows={10} />}
      {data && (
        <>
          <Kpis label="Team summary">
            <Kpi k="Record" v={record(data.record.wins, data.record.losses, data.record.ties)} s={`${fmt0(data.record.points_for)} points for`} />
            <Kpi k="Power rank" v={st ? `${st.rank} / ${teams.length}` : "–"} s={st ? <>{pct(st.power_score)} <Term t="power">power score</Term></> : undefined} />
            {isPoints && <Kpi k={<Term t="pts_wk_team">Points per week</Term>} v={fmt0(st?.ppw)} s={st ? <><Delta value={st.ppw_vs_avg} digits={0} /> <Term t="vs_avg">vs average</Term></> : undefined} />}
            <Kpi k={<Term t="proj_record">Projected record</Term>} v={st ? projRecord(st) : "–"} s={st ? projRecordNote(st) : undefined} />
            <Kpi k={<><Term t="playoffs" /> / <Term t="title">title</Term></>} v={st ? `${pct(st.playoff_odds)} / ${pct(st.title_odds)}` : "–"} />
            <Kpi k={<Term t="ros">Rest of season</Term>} v={fmt0(data.ros_total)} s={`points over ${fmt1(data.weeks_remaining)} weeks`} />
          </Kpis>
          <div className="stack" style={{ gap: 0 }}>
            <SubTabs label="Team sections" value={tab} onChange={setTab} tabs={[
              { value: "roster", label: "Roster" },
              { value: "lineup", label: "Daily lineup" },
              { value: "improve", label: "Improve" },
              { value: "schedule", label: "Schedule" },
            ]} />
            <div style={{ paddingTop: 10 }}>
              {tab === "roster" && <Roster team={data} isPoints={isPoints} />}
              {tab === "lineup" && <Lineup teamId={tid} />}
              {tab === "improve" && <Improve teamId={tid} />}
              {tab === "schedule" && <Schedule team={data} />}
            </div>
          </div>
        </>
      )}
    </>
  );
}

function Roster({ team, isPoints }: { team: TeamDetail; isPoints: boolean }) {
  const { openPlayer, live } = useLeagueCtx();
  const weeks = Math.max(1, team.weeks_remaining);
  // Points so far today: shown while games are on, and afterwards until tomorrow's first refresh.
  const today = isPoints && (live || team.roster.some((p) => p.today_fp != null));
  return (
    <Panel flush foot="Starts and points per week come from simulating every remaining day with the optimal lineup, so they account for positions, schedule overlap and injuries.">
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Slot</th><th>Player</th><th className="hide-sm">Team</th><th className="hide-sm">Pos</th>
              {today && <th className="n"><Term t="today" /></th>}
              <th className="n">{isPoints ? <Term t="fp_pg" /> : <Term t="value_pg" />}</th><th className="n hide-sm"><Term t="fp_sd" /></th><th className="n hide-sm"><Term t="season" /></th><th className="n hide-sm"><Term t="last10" /></th>
              <th className="n"><Term t="mpg" /></th><th className="n"><Term t="next7" /></th><th className="n"><Term t="starts_wk" /></th><th className="n"><Term t="pts_wk_player" /></th><th className="n hide-sm"><Term t="over_repl" /></th>
            </tr>
          </thead>
          <tbody>
            {team.roster.map((p) => (
              <tr key={p.id}>
                <td className="muted">{p.slot}</td>
                <td><PlayerName name={p.name} injury={p.injury_status} onOpen={() => openPlayer(p.id)} sub={p.games_out > 0 ? `out ~${fmt0(p.games_out)}g` : undefined} /></td>
                <td className="hide-sm muted">{p.pro_team}</td>
                <td className="hide-sm muted">{p.positions.join(", ")}</td>
                {today && (
                  <td className="n">
                    {p.today_fp != null ? <span title={`${fmt0(p.today_min)} minutes`}>{fmt1(p.today_fp)}</span> : <span className="faint">–</span>}
                  </td>
                )}
                <td className="n strong">{fmt1(isPoints ? p.fp_pg : p.value_pg)}</td>
                <td className="n hide-sm muted">{fmt0(p.fp_sd)}</td>
                <td className="n hide-sm">{fmt1(p.season_fp_pg)}</td>
                <td className="n hide-sm">{p.recent_fp_pg != null ? <span>{fmt1(p.recent_fp_pg)} <Delta value={p.recent_fp_pg - p.fp_pg} /></span> : <span className="faint">–</span>}</td>
                <td className="n">{fmt1(p.mpg)}</td>
                <td className="n">{fmt1(p.games_next7)}</td>
                <td className="n">{fmt1((p.exp_starts ?? 0) / weeks)}</td>
                <td className="n">{fmt0(p.pts_per_week)}</td>
                <td className="n hide-sm"><Delta value={p.vorp} digits={0} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Lineup({ teamId }: { teamId: number }) {
  const { league, openPlayer } = useLeagueCtx();
  const { data, isLoading, error } = useLineup(league.id, teamId, 7);
  const [idx, setIdx] = useState(0);
  if (isLoading) return <LoadingBlock rows={8} />;
  if (error) return <ErrorBox error={error} />;
  const days = data?.days ?? [];
  if (!days.length) return <Panel><Empty title="No games left in the fantasy season" /></Panel>;
  const day = days[Math.min(idx, days.length - 1)]!;
  return (
    <div className="stack">
      <div className="toolbar">
        <span className="label">Day</span>
        <div className="seg" role="group" aria-label="Day">
          {days.map((d, i) => (
            <button key={d.period} aria-pressed={i === idx} onClick={() => setIdx(i)}>
              {d.date ? shortDate(d.date, true) : `Day ${d.period}`}
            </button>
          ))}
        </div>
        <span className="small muted">Projected {fmt0(day.expected)} points</span>
      </div>
      <div className="grid cols-main" style={{ alignItems: "start" }}>
        <Panel title="Starting lineup" flush
          foot="Confidence: chance the starter outscores the best eligible bench player for that slot, from both players' projected means and game-to-game spread. Lineups lock at each game's tip-off.">
          {day.starters.length === 0 ? <Empty title="Nobody on this roster plays today" /> : (
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Slot</th><th>Player</th><th className="n">Proj</th><th><Term t="confidence" /></th><th className="hide-sm">Next best</th></tr></thead>
                <tbody>
                  {day.starters.map((s) => (
                    <tr key={s.player_id}>
                      <td className="muted">{s.slot}</td>
                      <td><PlayerName name={s.name} onOpen={() => openPlayer(s.player_id)} /></td>
                      <td className="n">{fmt1(s.projected)}</td>
                      <td>{s.alternative ? <Meter value={s.confidence} label={`Chance ${s.name} outscores ${s.alternative}`} /> : <span className="muted small">no alternative</span>}</td>
                      <td className="hide-sm muted">{s.alternative ?? "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <div className="stack">
          <Panel title="Bench (playing)" flush>
            {day.bench.length === 0 ? <p className="small muted" style={{ padding: "0 10px 10px" }}>Everyone with a game is starting.</p> : (
              <div className="table-wrap">
                <table className="data">
                  <tbody>
                    {day.bench.map((b) => (
                      <tr key={b.player_id}><td><PlayerName name={b.name} onOpen={() => openPlayer(b.player_id)} /></td><td className="n">{fmt1(b.projected)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
          <Panel title="No game">
            <p className="small">{day.no_game.map((n) => n.name).join(", ") || "Everyone plays."}</p>
          </Panel>
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
    <div className="grid cols-main" style={{ alignItems: "start" }}>
      <div className="stack">
        <Panel title="Add / drop" flush note="Every candidate is simulated in your lineup for the rest of the season; only real gains are listed.">
          {data.moves.length === 0 ? <Empty title="No free agent improves this roster" /> : (
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Add</th><th>Drop</th><th className="hide-sm">Why</th><th className="n"><Term t="move_wk">Pts/wk</Term></th><th className="n hide-sm"><Term t="this_week" /></th><th className="n"><Term t="move_ros">Rest of season</Term></th></tr></thead>
                <tbody>
                  {data.moves.map((m) => (
                    <tr key={m.add + m.drop}>
                      <td><PlayerName name={m.add_name} onOpen={() => openPlayer(m.add)} sub={m.on_waivers ? "waivers" : undefined} /></td>
                      <td><PlayerName name={m.drop_name} onOpen={() => openPlayer(m.drop)} /></td>
                      <td className="hide-sm muted">{m.reasons.join(", ")}</td>
                      <td className="n"><Delta value={m.delta_per_week} /></td>
                      <td className="n hide-sm"><Delta value={m.delta_this_week} /></td>
                      <td className="n"><Delta value={m.delta_ros} digits={0} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Streaming this week" flush
          note={`${data.streaming.days_left} days left in the matchup${data.streaming.acquisition_limit ? `, ${data.streaming.acquisition_limit} adds allowed per matchup` : ""}.`}>
          {data.streaming.moves.length === 0 ? <Empty title="No streaming gains this week" /> : (
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Add</th><th>Drop</th><th className="n">Games</th><th className="n"><Term t="this_week" /></th><th className="n hide-sm"><Term t="ros_cost" /></th></tr></thead>
                <tbody>
                  {data.streaming.moves.map((m) => (
                    <tr key={m.add}>
                      <td><PlayerName name={m.add_name} onOpen={() => openPlayer(m.add)} /></td>
                      <td className="muted">{m.drop_name}</td>
                      <td className="n">{fmt1(m.games_this_week)}</td>
                      <td className="n"><Delta value={m.delta_this_week} /></td>
                      <td className="n hide-sm"><Delta value={-m.ros_cost} digits={0} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
      <Panel title="Weak spots" flush note="Ranked by points at stake per week.">
        {data.weaknesses.length === 0 ? <Empty title="No structural weaknesses found" /> : (
          <div className="table-wrap">
            <table className="data">
              <tbody>
                {data.weaknesses.map((w, i) => (
                  <tr key={i}>
                    <td className="wrap">
                      <div>{w.player_id ? <button className="linkbtn" onClick={() => openPlayer(w.player_id!)}>{w.title}</button> : w.title}</div>
                      <div className="small muted">{w.detail}</div>
                    </td>
                    <td className="n" style={{ verticalAlign: "top" }}><Tag tone={w.kind === "injury" ? "down" : "warn"}>{w.kind.replace("_", " ")}</Tag></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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
    <Panel flush foot="Partial weeks count only the days left. Two-week playoff rounds show the combined total. Bands are ±1 standard deviation.">
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>Week</th><th>Dates</th><th>Opponent</th><th className="n">Projected</th><th className="n"><Term t="week_sd" /></th><th className="hide-sm" style={{ width: "34%" }}>Range</th></tr></thead>
          <tbody>
            {team.upcoming.map((u) => (
              <tr key={u.matchup_period}>
                <td>{u.playoffs ? "Playoffs" : `Week ${u.matchup_period}`}</td>
                <td className="muted">{shortDate(u.start)} – {shortDate(u.end)}</td>
                <td>{u.opponent_id ? teamName(u.opponent_id) : u.playoffs ? <span className="muted">Bracket</span> : "–"}</td>
                <td className="n">{fmt0(u.proj_mean)}</td>
                <td className="n muted">{fmt0(u.proj_sd)}</td>
                <td className="hide-sm"><RangeBar mean={u.proj_mean} sd={u.proj_sd} lo={lo} hi={hi} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
