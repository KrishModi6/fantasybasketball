import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLeagueCtx } from "../components/Layout";
import { Dialog, ErrorBox, Notice, PageHead, Panel } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fmt0, record, relTime, shortDate, sourceLabel } from "../lib/format";
import { useMembers } from "../lib/queries";
import { useToast } from "../lib/toast";
import type { Invite } from "../lib/types";

export default function LeagueHubPage() {
  const { league } = useLeagueCtx();
  const isOwner = league.me?.role === "owner";
  return (
    <>
      <PageHead title="League"
        desc={`${league.member_count} member${league.member_count === 1 ? "" : "s"} · ${sourceLabel[league.source]} · synced ${relTime(league.sync.last_synced_at)}`} />
      <div className="grid cols-main" style={{ alignItems: "start" }}>
        <div className="stack">
          <Standings />
          <Rules />
        </div>
        <div className="stack">
          {isOwner ? <Invites /> : <Panel title="Invites"><p className="small muted">Only the league owner can create invite links.</p></Panel>}
          <Members />
          {isOwner && <OwnerSettings />}
          <Leave />
        </div>
      </div>
    </>
  );
}

function Standings() {
  const { league, myTeamId } = useLeagueCtx();
  const rows = [...league.teams].sort((a, b) => b.wins + b.ties / 2 - (a.wins + a.ties / 2) || b.points_for - a.points_for);
  return (
    <Panel title="Standings" flush>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th className="n">#</th><th>Team</th><th className="hide-sm">Owner (ESPN)</th><th className="hide-sm">Courtside member</th><th className="n">W–L</th><th className="n">PF</th><th className="n hide-sm">PA</th></tr></thead>
          <tbody>
            {rows.map((t, i) => (
              <tr key={t.id} className={t.id === myTeamId ? "mine" : ""}>
                <td className="n muted">{i + 1}</td>
                <td><Link to={`../team/${t.id}`} relative="path" style={{ color: "var(--text)" }}>{t.name}</Link></td>
                <td className="hide-sm muted">{t.owner_name ?? "–"}</td>
                <td className="hide-sm">{t.claimed_by ?? <span className="faint">unclaimed</span>}</td>
                <td className="n">{record(t.wins, t.losses, t.ties)}</td>
                <td className="n">{fmt0(t.points_for)}</td>
                <td className="n hide-sm">{fmt0(t.points_against)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

const STAT_ORDER = ["PTS", "REB", "AST", "STL", "BLK", "TO", "3PM", "FGM", "FGA", "FTM", "FTA", "DD", "TD", "OREB", "DREB", "3PA", "PF"];

function Rules() {
  const { league } = useLeagueCtx();
  const qc = useQueryClient();
  const toast = useToast();
  const s = league.settings;
  const editable = league.me?.role === "owner" && league.source !== "espn";
  const [editing, setEditing] = useState(false);
  const [points, setPoints] = useState<Record<string, number>>(s?.scoring.points ?? {});
  const save = useMutation({
    mutationFn: () => api(`/api/leagues/${league.id}/settings`, { method: "PUT", body: { kind: "points", points } }),
    onSuccess: () => {
      toast("Scoring updated; projections and rankings recalculated.");
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["league", league.id] });
    },
    onError: (e: Error) => toast(e.message, "err"),
  });
  if (!s) return null;
  const shown = editing ? STAT_ORDER : STAT_ORDER.filter((k) => s.scoring.points[k]);
  return (
    <Panel title="Scoring and rules"
      actions={editable && s.scoring.kind === "points" && !editing ? <button className="btn" onClick={() => setEditing(true)}>Edit scoring</button> : undefined}>
      <div className="grid cols-2" style={{ alignItems: "start" }}>
        {s.scoring.kind === "points" ? (
          <div className="table-wrap">
            <table className="data" aria-label="Points per stat">
              <thead><tr><th>Stat</th><th className="n">Points</th></tr></thead>
              <tbody>
                {shown.map((k) => (
                  <tr key={k}>
                    <td>{k}</td>
                    <td className="n">
                      {editing ? (
                        <input className="input" style={{ width: 64, textAlign: "right" }} type="number" step="0.5" aria-label={`${k} points`}
                          value={points[k] ?? 0} onChange={(e) => setPoints({ ...points, [k]: Number(e.target.value) })} />
                      ) : (
                        <span className={s.scoring.points[k]! < 0 ? "down" : ""}>{s.scoring.points[k]! > 0 ? "+" : ""}{s.scoring.points[k]}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <dl className="kv"><dt>Categories</dt><dd>{s.scoring.categories.join(", ")}</dd></dl>
        )}
        <dl className="kv">
          <dt>Scoring</dt><dd>{s.scoring.kind === "points" ? "Head-to-head points" : "Head-to-head categories"}</dd>
          <dt>Lineup slots</dt><dd>{Object.entries(s.roster_slots).map(([k, v]) => (v > 1 ? `${k}×${v}` : k)).join(", ")}</dd>
          <dt>Lineup changes</dt><dd>{s.lineup_lock === "daily" ? "Daily, lock at tip-off" : "Weekly"}</dd>
          <dt>Regular season</dt><dd>{s.regular_season_matchups} weekly matchups</dd>
          <dt>Playoffs</dt><dd>{s.playoff_teams} teams, {s.playoff_round_weeks.join(" + ")} week rounds</dd>
          <dt>Adds per matchup</dt><dd>{s.acquisition_limit_per_matchup ?? "No limit"}</dd>
          <dt>Trade deadline</dt><dd>{s.trade_deadline ? shortDate(s.trade_deadline) : "–"}</dd>
        </dl>
      </div>
      {editing && (
        <div className="toolbar" style={{ marginTop: 10 }}>
          <button className="btn primary" onClick={() => save.mutate()} disabled={save.isPending}>Save scoring</button>
          <button className="btn" onClick={() => { setEditing(false); setPoints(s.scoring.points); }}>Cancel</button>
        </div>
      )}
      {league.source === "espn" && <p className="panel-foot">Mirrored from ESPN on every refresh.</p>}
    </Panel>
  );
}

function Invites() {
  const { league } = useLeagueCtx();
  const qc = useQueryClient();
  const toast = useToast();
  const [copied, setCopied] = useState<string | null>(null);
  const { data } = useQuery({ queryKey: ["league", league.id, "invites"], queryFn: () => api<Invite[]>(`/api/leagues/${league.id}/invites`) });
  const create = useMutation({
    mutationFn: () => api<Invite>(`/api/leagues/${league.id}/invites`, { body: { expires_in_days: 30 } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["league", league.id, "invites"] }),
    onError: (e: Error) => toast(e.message, "err"),
  });
  const revoke = useMutation({
    mutationFn: (code: string) => api(`/api/leagues/${league.id}/invites/${code}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["league", league.id, "invites"] }),
  });
  const link = (inv: Invite) => (window.location.protocol.startsWith("http") ? `${window.location.origin}/join/${inv.code}` : inv.url);
  const copy = async (inv: Invite) => {
    try {
      await navigator.clipboard.writeText(link(inv));
      setCopied(inv.code);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      toast(`Copy failed. The code is ${inv.code}.`, "err");
    }
  };
  return (
    <Panel title="Invites" flush note="Anyone with a link can create an account, join and claim a team. Links expire after 30 days."
      actions={<button className="btn primary" onClick={() => create.mutate()} disabled={create.isPending}>New invite</button>}>
      {data && data.length === 0 ? <p className="small muted" style={{ padding: "0 10px 10px" }}>No active invites.</p> : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Code</th><th className="n">Joined</th><th>Expires</th><th /></tr></thead>
            <tbody>
              {data?.map((inv) => (
                <tr key={inv.code}>
                  <td className="mono">{inv.code}</td>
                  <td className="n">{inv.uses}</td>
                  <td className="muted">{shortDate(inv.expires_at)}</td>
                  <td className="n">
                    <div className="row" style={{ justifyContent: "flex-end" }}>
                      <button className="btn" onClick={() => copy(inv)}>{copied === inv.code ? "Copied" : "Copy link"}</button>
                      <button className="btn danger" onClick={() => revoke.mutate(inv.code)} aria-label={`Revoke ${inv.code}`}>Revoke</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function Members() {
  const { league, teams, teamName } = useLeagueCtx();
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useMembers(league.id);
  const isOwner = league.me?.role === "owner";
  const assign = useMutation({
    mutationFn: ({ uid, team }: { uid: string; team: number | null }) =>
      uid === user?.id
        ? api(`/api/leagues/${league.id}/members/me/team`, { body: { team_id: team } })
        : api(`/api/leagues/${league.id}/members/${uid}`, { method: "PATCH", body: { team_id: team } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["league", league.id] }),
    onError: (e: Error) => toast(e.message, "err"),
  });
  return (
    <Panel title="Members" flush>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>Name</th><th>Role</th><th>Team</th></tr></thead>
          <tbody>
            {data?.map((m) => (
              <tr key={m.user_id}>
                <td>{m.display_name}<div className="tiny muted">joined {relTime(m.joined_at)}</div></td>
                <td className="muted">{m.role === "owner" ? "Owner" : "Member"}</td>
                <td>
                  {isOwner || m.user_id === user?.id ? (
                    <select className="select" value={m.team_id ?? ""} aria-label={`Team for ${m.display_name}`}
                      onChange={(e) => assign.mutate({ uid: m.user_id, team: e.target.value === "" ? null : Number(e.target.value) })}>
                      <option value="">None</option>
                      {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
                  ) : m.team_id ? teamName(m.team_id) : <span className="faint">None</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function OwnerSettings() {
  const { league } = useLeagueCtx();
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const [interval, setInterval] = useState(league.sync.interval_hours);
  const [s2, setS2] = useState("");
  const [swid, setSwid] = useState("");
  const [confirm, setConfirm] = useState(false);
  const save = useMutation({
    mutationFn: () => api(`/api/leagues/${league.id}`, {
      method: "PATCH",
      body: {
        sync_interval_hours: interval,
        ...(league.source === "espn" && (s2 || swid) ? { espn: { league_id: league.connector.espn_league_id, espn_s2: s2 || null, swid: swid || null } } : {}),
      },
    }),
    onSuccess: () => {
      toast("Settings saved.");
      setS2("");
      setSwid("");
      qc.invalidateQueries({ queryKey: ["league", league.id, "meta"] });
    },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const del = useMutation({
    mutationFn: () => api(`/api/leagues/${league.id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["leagues"] });
      nav("/");
    },
  });
  return (
    <Panel title="Data and sync">
      <div className="form">
        {league.sync.error && <ErrorBox error={new Error(league.sync.error)} />}
        <div className="field">
          <label htmlFor="iv">Automatic refresh</label>
          <select id="iv" className="select" value={interval} onChange={(e) => setInterval(Number(e.target.value))}>
            {[2, 4, 6, 8, 12, 24].map((h) => <option key={h} value={h}>Every {h} hours</option>)}
          </select>
        </div>
        {league.source === "espn" && (
          <>
            <p className="small muted">ESPN league <span className="mono">{league.connector.espn_league_id}</span>, {league.connector.has_cookies ? "private (cookies stored)" : "public"}.</p>
            <div className="form-grid">
              <div className="field"><label htmlFor="s2u">Replace espn_s2</label><input id="s2u" className="input mono" value={s2} onChange={(e) => setS2(e.target.value)} autoComplete="off" /></div>
              <div className="field"><label htmlFor="swu">Replace SWID</label><input id="swu" className="input mono" value={swid} onChange={(e) => setSwid(e.target.value)} autoComplete="off" /></div>
            </div>
          </>
        )}
        <div className="row between">
          <button className="btn primary" onClick={() => save.mutate()} disabled={save.isPending}>Save</button>
          <button className="btn danger" onClick={() => setConfirm(true)}>Delete league</button>
        </div>
      </div>
      {confirm && (
        <Dialog title="Delete this league?" onClose={() => setConfirm(false)}>
          <Notice level="warn">Removes the league for every member, including posted trades and history. The ESPN league is not affected.</Notice>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button className="btn" onClick={() => setConfirm(false)}>Cancel</button>
            <button className="btn danger" onClick={() => del.mutate()} disabled={del.isPending}>Delete league</button>
          </div>
        </Dialog>
      )}
    </Panel>
  );
}

function Leave() {
  const { league } = useLeagueCtx();
  const { user } = useAuth();
  const qc = useQueryClient();
  const nav = useNavigate();
  const leave = useMutation({
    mutationFn: () => api(`/api/leagues/${league.id}/members/${user!.id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["leagues"] });
      nav("/");
    },
  });
  if (league.me?.role === "owner") return null;
  return <div><button className="btn danger" onClick={() => leave.mutate()}>Leave league</button></div>;
}
