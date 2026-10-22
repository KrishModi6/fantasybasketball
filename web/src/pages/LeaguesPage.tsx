import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { BrandMark, FetchLine, ThemeToggle } from "../components/Layout";
import { Empty, ErrorBox, LoadingBlock, Panel, SubTabs, Tag } from "../components/ui";
import { api, isLocalApp, serverUrl } from "../lib/api";
import { useAuth } from "../lib/auth";
import { relTime, sourceLabel } from "../lib/format";
import { useLeagues } from "../lib/queries";
import type { League } from "../lib/types";

type Mode = "espn" | "file" | "join" | "demo" | "flaim";

export function LeaguesPage() {
  const { user, signOut } = useAuth();
  const { data, isLoading, error } = useLeagues();
  const nav = useNavigate();
  const [mode, setMode] = useState<Mode>("espn");
  return (
    <div className="standalone">
      <FetchLine />
      <header className="topbar">
        <span className="brand"><BrandMark /> Courtside</span>
        <span className="small muted">Leagues</span>
        <span className="spacer" />
        <ThemeToggle />
        {!isLocalApp() && (
          <>
            <span className="small muted hide-sm">{user?.display_name}</span>
            <button className="btn ghost icon" onClick={signOut} aria-label="Sign out" title="Sign out"><LogOut /></button>
          </>
        )}
      </header>
      <main className="page-narrow">
        <Panel title="Your leagues" flush>
          {isLoading && <div style={{ padding: 10 }}><LoadingBlock rows={3} /></div>}
          {error && <div style={{ padding: 10 }}><ErrorBox error={error} /></div>}
          {data && data.length === 0 && <Empty title="No leagues yet">Connect an ESPN league, import rosters from a file, or create a demo league below.</Empty>}
          {data && data.length > 0 && (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr><th>League</th><th>Source</th><th>Season</th><th>Role</th><th className="n">Members</th><th>Data</th></tr>
                </thead>
                <tbody>
                  {data.map((l) => (
                    <tr key={l.id} className="clickable" onClick={() => nav(`/l/${l.id}`)}>
                      <td><button className="linkbtn strong" onClick={() => nav(`/l/${l.id}`)}>{l.name}</button></td>
                      <td>{sourceLabel[l.source]}</td>
                      <td>{l.season - 1}–{String(l.season).slice(2)}</td>
                      <td>{l.role === "owner" ? "Owner" : "Member"}</td>
                      <td className="n">{l.member_count}</td>
                      <td className={l.sync_status === "error" ? "down" : "muted"}>
                        {l.sync_status === "error" ? "Refresh failed" : `Synced ${relTime(l.last_synced_at)}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <section className="stack" style={{ gap: 0 }} aria-label="Add a league">
          <SubTabs label="Add a league" value={mode} onChange={setMode} tabs={[
            { value: "espn", label: "Connect ESPN" },
            { value: "file", label: "Import file" },
            { value: "join", label: "Join with invite" },
            { value: "demo", label: "Demo league" },
            { value: "flaim", label: "Flaim (beta)" },
          ]} />
          <div className="panel" style={{ borderTop: 0, borderRadius: "0 0 3px 3px" }}>
            {mode === "espn" && <EspnForm />}
            {mode === "file" && <FileForm />}
            {mode === "join" && <JoinForm />}
            {mode === "demo" && <DemoForm />}
            {mode === "flaim" && <FlaimForm />}
          </div>
        </section>
      </main>
    </div>
  );
}

function useCreate() {
  const qc = useQueryClient();
  const nav = useNavigate();
  return useMutation({
    mutationFn: (body: unknown) => api<League>("/api/leagues", { body }),
    onSuccess: (lg) => {
      qc.invalidateQueries({ queryKey: ["leagues"] });
      nav(`/l/${lg.id}`);
    },
  });
}

function EspnForm() {
  const create = useCreate();
  const [leagueId, setLeagueId] = useState("");
  const [season, setSeason] = useState(2027);
  const [s2, setS2] = useState("");
  const [swid, setSwid] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const id = leagueId.match(/leagueId=(\d+)/)?.[1] ?? leagueId.replace(/\D/g, "");
    create.mutate({ source: "espn", season, espn: { league_id: id, espn_s2: s2 || null, swid: swid || null } });
  };
  return (
    <form className="form" onSubmit={submit}>
      <p className="small muted">Reads rosters, scoring settings, lineup slots, schedule and matchups from ESPN, and refreshes them automatically.</p>
      <div className="form-grid">
        <div className="field" style={{ gridColumn: "span 2" }}>
          <label htmlFor="lid">League ID or league URL</label>
          <input id="lid" className="input" required value={leagueId} onChange={(e) => setLeagueId(e.target.value)} placeholder="https://fantasy.espn.com/basketball/league?leagueId=123456" />
        </div>
        <div className="field">
          <label htmlFor="season">Season</label>
          <select id="season" className="select" value={season} onChange={(e) => setSeason(Number(e.target.value))}>
            <option value={2027}>2026–27</option>
            <option value={2026}>2025–26</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="s2">espn_s2 (private leagues)</label>
          <input id="s2" className="input mono" value={s2} onChange={(e) => setS2(e.target.value)} autoComplete="off" spellCheck={false} />
        </div>
        <div className="field">
          <label htmlFor="swid">SWID (private leagues)</label>
          <input id="swid" className="input mono" value={swid} onChange={(e) => setSwid(e.target.value)} placeholder="{XXXXXXXX-…}" autoComplete="off" spellCheck={false} />
        </div>
      </div>
      <p className="tiny faint">
        Private leagues: sign in at fantasy.espn.com, open developer tools → Application → Cookies, and copy espn_s2 and SWID. They are encrypted at rest and used only to read the league.
      </p>
      {create.error && <ErrorBox error={create.error} />}
      <div><button className="btn primary" type="submit" disabled={create.isPending}>{create.isPending ? "Reading league…" : "Connect league"}</button></div>
    </form>
  );
}

function FileForm() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("file", file!);
      fd.append("name", name || file!.name.replace(/\.(csv|json)$/i, ""));
      return api<League>("/api/leagues/import", { form: fd });
    },
    onSuccess: (lg) => {
      qc.invalidateQueries({ queryKey: ["leagues"] });
      nav(`/l/${lg.id}`);
    },
  });
  return (
    <form className="form" onSubmit={(e) => { e.preventDefault(); if (file) upload.mutate(); }}>
      <p className="small muted">
        CSV with <span className="mono">team, player</span> columns (optional <span className="mono">owner, slot</span>) or a JSON roster file, from any platform.
        Players are matched to ESPN's pool by name; stats, injuries and the schedule keep refreshing.{" "}
        <a href={`${serverUrl()}/api/import/example.csv`} target="_blank" rel="noreferrer">Example CSV</a>
      </p>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="f">File</label>
          <input id="f" type="file" accept=".csv,.json" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>
        <div className="field">
          <label htmlFor="fn">League name</label>
          <input id="fn" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="My league" />
        </div>
      </div>
      {upload.error && <ErrorBox error={upload.error} />}
      <div><button className="btn primary" disabled={!file || upload.isPending}>{upload.isPending ? "Importing…" : "Import league"}</button></div>
    </form>
  );
}

function JoinForm() {
  const nav = useNavigate();
  const [code, setCode] = useState("");
  return (
    <form className="form" onSubmit={(e) => { e.preventDefault(); const c = code.trim().split("/").pop(); if (c) nav(`/join/${c.toUpperCase()}`); }}>
      <div className="field" style={{ maxWidth: 360 }}>
        <label htmlFor="code">Invite code or link</label>
        <input id="code" className="input mono" value={code} onChange={(e) => setCode(e.target.value)} placeholder="7KQ2MZ4P" required />
      </div>
      <div><button className="btn primary">Continue</button></div>
    </form>
  );
}

function DemoForm() {
  const create = useCreate();
  const [teams, setTeams] = useState(10);
  return (
    <div className="form">
      <p className="small muted">
        A points league with ESPN's default scoring, drafted by ADP from the live 2026–27 player pool and NBA schedule. Useful for trying the tools or inviting friends to test.
      </p>
      <div className="toolbar">
        <label className="field-inline" htmlFor="tc"><span className="label">Teams</span>
          <select id="tc" className="select" value={teams} onChange={(e) => setTeams(Number(e.target.value))}>
            {[8, 10, 12].map((n) => <option key={n}>{n}</option>)}
          </select>
        </label>
        <button className="btn primary" onClick={() => create.mutate({ source: "demo", team_count: teams, name: "Demo League" })} disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create demo league"}
        </button>
      </div>
      {create.error && <ErrorBox error={create.error} />}
    </div>
  );
}

function FlaimForm() {
  const create = useCreate();
  const [token, setToken] = useState("");
  const [platform, setPlatform] = useState("espn");
  const [leagueId, setLeagueId] = useState("");
  return (
    <form className="form" onSubmit={(e) => { e.preventDefault(); create.mutate({ source: "flaim", flaim: { flaim_token: token, platform, league_id: leagueId } }); }}>
      <p className="small muted">
        <Tag tone="warn">Unverified</Tag>{" "}
        Flaim exposes ESPN, Yahoo and Sleeper leagues through an MCP server built for AI assistants; its output format is unpublished, so rosters are mapped
        defensively. Requires a Flaim access token. For ESPN leagues the direct connector is more reliable.
      </p>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="ft">Access token</label>
          <input id="ft" className="input mono" value={token} onChange={(e) => setToken(e.target.value)} required minLength={10} autoComplete="off" />
        </div>
        <div className="field">
          <label htmlFor="fp">Platform</label>
          <select id="fp" className="select" value={platform} onChange={(e) => setPlatform(e.target.value)}>
            <option value="espn">ESPN</option>
            <option value="yahoo">Yahoo</option>
            <option value="sleeper">Sleeper</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="fl">League ID</label>
          <input id="fl" className="input" value={leagueId} onChange={(e) => setLeagueId(e.target.value)} required />
        </div>
      </div>
      {create.error && <ErrorBox error={create.error} />}
      <div><button className="btn primary" disabled={create.isPending}>{create.isPending ? "Connecting…" : "Connect"}</button></div>
    </form>
  );
}
