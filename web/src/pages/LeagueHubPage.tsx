import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Link2, Trash2 } from "lucide-react";
import { useLeagueCtx } from "../components/Layout";
import { Dialog, ErrorBox, LoadingBlock, PageHead, Panel } from "../components/ui";
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
      <PageHead kicker="League hub" title={league.name}
        lede={`${league.member_count} member${league.member_count === 1 ? "" : "s"} · ${sourceLabel[league.source]} · data refreshed ${relTime(league.sync.last_synced_at)}`} />
      <div className="grid grid-main" style={{ alignItems: "start" }}>
        <div className="stack" style={{ gap: 18 }}>
          <Standings />
          <Rules />
        </div>
        <div className="stack" style={{ gap: 18 }}>
          {isOwner ? <Invites /> : <Panel title="Invite friends"><p className="small muted" style={{ margin: 0 }}>Ask the league owner for an invite link.</p></Panel>}
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
    <Panel title="Standings" kicker="Head-to-head record" flush>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr><th className="n">#</th><th>Team</th><th className="hide-sm">Manager</th><th className="n">W–L</th><th className="n">PF</th><th className="n hide-sm">PA</th></tr>
          </thead>
          <tbody>
            {rows.map((t, i) => (
              <tr key={t.id} className={t.id === myTeamId ? "mine" : ""}>
                <td className="n muted">{i + 1}</td>
                <td className="player">
                  <div className="player-cell">
                    <Link className="nm" to={`../team/${t.id}`} relative="path" style={{ textDecoration: "none" }}>{t.name}</Link>
                    <span className="pos-tag">{t.owner_name ?? "—"}</span>
                  </div>
                </td>
                <td className="hide-sm small">{t.claimed_by ?? <span className="faint">Unclaimed</span>}</td>
                <td className="n strong">{record(t.wins, t.losses, t.ties)}</td>
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
      toast("Scoring updated. Every projection and ranking now uses the new weights.");
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["league", league.id] });
    },
    onError: (e: Error) => toast(e.message, "err"),
  });
  if (!s) return null;
  const statOrder = ["PTS", "REB", "AST", "STL", "BLK", "TO", "3PM", "FGM", "FGA", "FTM", "FTA", "DD", "TD", "OREB", "DREB", "3PA", "PF"];
  const shown = editing ? statOrder : statOrder.filter((k) => s.scoring.points[k]);
  return (
    <Panel title="Scoring & rules" kicker={s.scoring.kind === "points" ? "Head-to-head points" : "Head-to-head categories"}
      actions={editable && s.scoring.kind === "points" && !editing ? <button className="btn secondary sm" onClick={() => setEditing(true)}>Edit scoring</button> : undefined}>
      <div className="grid grid-2" style={{ alignItems: "start" }}>
        {s.scoring.kind === "points" ? (
          <table className="data" aria-label="Points per stat">
            <thead><tr><th>Stat</th><th className="n">Points</th></tr></thead>
            <tbody>
              {shown.map((k) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td className="n">
                    {editing ? (
                      <input className="input" style={{ width: 72, height: 30, textAlign: "right" }} type="number" step="0.5" aria-label={`${k} points`}
                        value={points[k] ?? 0} onChange={(e) => setPoints({ ...points, [k]: Number(e.target.value) })} />
                    ) : (
                      <span className={`strong ${s.scoring.points[k]! < 0 ? "neg" : ""}`}>{s.scoring.points[k]! > 0 ? "+" : ""}{s.scoring.points[k]}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="chips">{s.scoring.categories.map((c) => <span key={c} className="badge">{c}</span>)}</div>
        )}
        <dl className="kv">
          <dt>Lineup slots</dt>
          <dd>{Object.entries(s.roster_slots).map(([k, v]) => (v > 1 ? `${k}×${v}` : k)).join(" · ")}</dd>
          <dt>Lineup changes</dt>
          <dd>{s.lineup_lock === "daily" ? "Daily, lock at tip-off" : "Weekly"}</dd>
          <dt>Regular season</dt>
          <dd>{s.regular_season_matchups} weekly matchups</dd>
          <dt>Playoffs</dt>
          <dd>{s.playoff_teams} teams · {s.playoff_round_weeks.join("+")} week rounds</dd>
          <dt>Adds per matchup</dt>
          <dd>{s.acquisition_limit_per_matchup ?? "No limit"}</dd>
          <dt>Trade deadline</dt>
          <dd>{s.trade_deadline ? shortDate(s.trade_deadline) : "—"}</dd>
        </dl>
      </div>
      {editing && (
        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn" onClick={() => save.mutate()} disabled={save.isPending}>Save scoring</button>
          <button className="btn ghost" onClick={() => { setEditing(false); setPoints(s.scoring.points); }}>Cancel</button>
        </div>
      )}
      {league.source === "espn" && <p className="tiny muted" style={{ margin: "12px 0 0" }}>Mirrored from ESPN on every refresh.</p>}
    </Panel>
  );
}

function Invites() {
  const { league } = useLeagueCtx();
  const qc = useQueryClient();
  const toast = useToast();
  const [copied, setCopied] = useState<string | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ["league", league.id, "invites"], queryFn: () => api<Invite[]>(`/api/leagues/${league.id}/invites`) });
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
      setTimeout(() => setCopied(null), 1600);
    } catch {
      toast(`Copy failed. The code is ${inv.code}.`, "err");
    }
  };
  return (
    <Panel title="Invite your league" kicker="Owner"
      actions={<button className="btn sm" onClick={() => create.mutate()} disabled={create.isPending}><Link2 /> New link</button>}>
      <p className="small muted" style={{ marginTop: 0 }}>Anyone with the link can join after creating an account and pick their team. Links last 30 days; revoke one any time.</p>
      {isLoading && <LoadingBlock rows={2} />}
      {data?.length === 0 && <p className="small">No active invites yet.</p>}
      <ul className="list">
        {data?.map((inv) => (
          <li key={inv.code} className="row between wrap">
            <div className="stack" style={{ gap: 2 }}>
              <span className="mono strong" style={{ fontSize: 16, letterSpacing: "0.08em" }}>{inv.code}</span>
              <span className="tiny muted">{inv.uses} joined · expires {shortDate(inv.expires_at)}</span>
            </div>
            <div className="row">
              <button className="btn secondary sm" onClick={() => copy(inv)}>{copied === inv.code ? <Check /> : <Copy />} {copied === inv.code ? "Copied" : "Copy link"}</button>
              <button className="btn ghost sm icon" aria-label={`Revoke invite ${inv.code}`} onClick={() => revoke.mutate(inv.code)}><Trash2 /></button>
            </div>
          </li>
        ))}
      </ul>
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
    <Panel title="Members" kicker={`${data?.length ?? "…"} people`}>
      <ul className="list">
        {data?.map((m) => (
          <li key={m.user_id} className="row between wrap">
            <div className="stack" style={{ gap: 1 }}>
              <span className="strong">{m.display_name} {m.role === "owner" && <span className="badge accent">Owner</span>}</span>
              <span className="tiny muted">joined {relTime(m.joined_at)}</span>
            </div>
            {isOwner || m.user_id === user?.id ? (
              <select className="select" style={{ width: 190, height: 32 }} value={m.team_id ?? ""} aria-label={`Team for ${m.display_name}`}
                onChange={(e) => assign.mutate({ uid: m.user_id, team: e.target.value === "" ? null : Number(e.target.value) })}>
                <option value="">No team</option>
                {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            ) : (
              <span className="small">{m.team_id ? teamName(m.team_id) : <span className="faint">No team</span>}</span>
            )}
          </li>
        ))}
      </ul>
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
      toast("League settings saved.");
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
    <Panel title="Data & sync" kicker="Owner">
      <div className="stack">
        {league.sync.error && <ErrorBox error={new Error(league.sync.error)} />}
        <div className="field">
          <label htmlFor="iv">Refresh every</label>
          <select id="iv" className="select" value={interval} onChange={(e) => setInterval(Number(e.target.value))}>
            {[2, 4, 6, 8, 12, 24].map((h) => <option key={h} value={h}>{h} hours</option>)}
          </select>
          <span className="hint">Stats, injuries and rosters refresh automatically; anyone can also refresh manually.</span>
        </div>
        {league.source === "espn" && (
          <>
            <div className="small">ESPN league <span className="mono">{league.connector.espn_league_id}</span> · {league.connector.has_cookies ? "private (cookies saved)" : "public"}</div>
            <div className="grid grid-2">
              <div className="field"><label htmlFor="s2u">New espn_s2</label><input id="s2u" className="input mono" value={s2} onChange={(e) => setS2(e.target.value)} autoComplete="off" /></div>
              <div className="field"><label htmlFor="swu">New SWID</label><input id="swu" className="input mono" value={swid} onChange={(e) => setSwid(e.target.value)} autoComplete="off" /></div>
            </div>
          </>
        )}
        <div className="row between">
          <button className="btn" onClick={() => save.mutate()} disabled={save.isPending}>Save</button>
          <button className="btn danger" onClick={() => setConfirm(true)}><Trash2 /> Delete league</button>
        </div>
      </div>
      {confirm && (
        <Dialog title="Delete this league?" onClose={() => setConfirm(false)}>
          <p className="small">This removes the league hub for every member, including shared trades and history. Your ESPN league isn't affected.</p>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button className="btn ghost" onClick={() => setConfirm(false)}>Cancel</button>
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
  return (
    <div className="row" style={{ justifyContent: "flex-end" }}>
      <button className="btn ghost sm" onClick={() => leave.mutate()}>Leave league</button>
    </div>
  );
}
