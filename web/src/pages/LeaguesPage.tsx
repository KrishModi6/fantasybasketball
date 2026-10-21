import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, FileUp, FlaskConical, KeyRound, LogOut, Plug, Ticket } from "lucide-react";
import { BrandMark, ThemeToggle } from "../components/Layout";
import { Empty, ErrorBox, LoadingBlock, PageHead } from "../components/ui";
import { api, serverUrl } from "../lib/api";
import { useAuth } from "../lib/auth";
import { relTime, sourceLabel } from "../lib/format";
import { useLeagues } from "../lib/queries";
import type { League } from "../lib/types";

type Mode = "espn" | "file" | "demo" | "join" | "flaim";

export function LeaguesPage() {
  const { user, signOut } = useAuth();
  const { data, isLoading, error } = useLeagues();
  const [mode, setMode] = useState<Mode | null>(null);
  const options: { mode: Mode; icon: JSX.Element; title: string; text: string }[] = [
    { mode: "espn", icon: <Plug />, title: "Connect ESPN league", text: "Live rosters, scoring settings and matchups, refreshed automatically." },
    { mode: "file", icon: <FileUp />, title: "Import a file", text: "CSV or JSON rosters from any platform. Stats still update live." },
    { mode: "join", icon: <Ticket />, title: "Join with a code", text: "Your commissioner shared an invite link or code." },
    { mode: "demo", icon: <FlaskConical />, title: "Try a demo league", text: "Ten teams drafted from this season's real player pool." },
  ];
  return (
    <div style={{ minHeight: "100vh" }}>
      <header className="topbar">
        <Link to="/" className="brand" style={{ padding: 0 }}>
          <BrandMark size={26} />
          <span className="brand-name" style={{ fontSize: 20 }}>Courtside</span>
        </Link>
        <div className="spacer" />
        <span className="small muted hide-sm">{user?.display_name}</span>
        <ThemeToggle />
        <button className="btn ghost sm" onClick={signOut}><LogOut size={15} /> Sign out</button>
      </header>
      <main className="content" style={{ maxWidth: 1080 }}>
        <PageHead kicker="Your leagues" title="Pick a league" lede="Each league is a shared hub: everyone you invite sees the same rankings, projections and trade board, with their own team front and center." />
        <div className="grid grid-main" style={{ alignItems: "start" }}>
          <section className="stack" aria-label="Leagues">
            {isLoading && <LoadingBlock rows={4} />}
            {error && <ErrorBox error={error} />}
            {data && data.length === 0 && (
              <div className="panel"><Empty title="No leagues yet">Connect your ESPN league, import a roster file, or start with the demo league to explore.</Empty></div>
            )}
            {data?.map((l, i) => (
              <Link key={l.id} to={`/l/${l.id}`} className="league-card rise" style={{ "--i": i } as React.CSSProperties}>
                <div className="stack" style={{ gap: 3 }}>
                  <span className="h2">{l.name}</span>
                  <span className="small muted">
                    {sourceLabel[l.source]} · {l.season - 1}–{String(l.season).slice(2)} · {l.member_count} member{l.member_count === 1 ? "" : "s"} ·{" "}
                    {l.role === "owner" ? "you own this league" : "member"} · updated {relTime(l.last_synced_at)}
                  </span>
                </div>
                <ArrowRight size={18} />
              </Link>
            ))}
          </section>
          <aside className="stack" aria-label="Add a league">
            <div className="kicker">Add a league</div>
            {options.map((o) => (
              <button key={o.mode} className="option-card" aria-pressed={mode === o.mode} onClick={() => setMode(mode === o.mode ? null : o.mode)}>
                <span className="row">{o.icon}<span className="h3">{o.title}</span></span>
                <span className="small muted">{o.text}</span>
              </button>
            ))}
            <button className="btn ghost sm" style={{ justifySelf: "start" }} onClick={() => setMode("flaim")}>
              <KeyRound size={14} /> Connect through Flaim (beta)
            </button>
          </aside>
        </div>
        {mode && (
          <section className="panel" style={{ marginTop: 22 }}>
            <div className="panel-body">
              {mode === "espn" && <EspnForm />}
              {mode === "file" && <FileForm />}
              {mode === "demo" && <DemoForm />}
              {mode === "join" && <JoinForm />}
              {mode === "flaim" && <FlaimForm />}
            </div>
          </section>
        )}
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
    <form className="stack" onSubmit={submit}>
      <h2 className="h2">Connect an ESPN league</h2>
      <div className="grid grid-2">
        <div className="field">
          <label htmlFor="lid">League ID or league URL</label>
          <input id="lid" className="input" required value={leagueId} onChange={(e) => setLeagueId(e.target.value)} placeholder="https://fantasy.espn.com/basketball/league?leagueId=123456" />
          <span className="hint">It's the <span className="mono">leagueId</span> number in your league's address bar.</span>
        </div>
        <div className="field">
          <label htmlFor="season">Season</label>
          <select id="season" className="select" value={season} onChange={(e) => setSeason(Number(e.target.value))}>
            <option value={2027}>2026–27</option>
            <option value={2026}>2025–26</option>
          </select>
        </div>
      </div>
      <details>
        <summary className="strong" style={{ cursor: "pointer" }}>Private league? Add your ESPN cookies</summary>
        <div className="stack" style={{ marginTop: 12 }}>
          <p className="small muted" style={{ margin: 0 }}>
            ESPN only shares private leagues with a signed-in browser. On fantasy.espn.com open your browser's developer tools → Application → Cookies and copy the values of
            <span className="mono"> espn_s2</span> and <span className="mono">SWID</span>. They're encrypted at rest and only used to read your league.
          </p>
          <div className="grid grid-2">
            <div className="field">
              <label htmlFor="s2">espn_s2</label>
              <input id="s2" className="input mono" value={s2} onChange={(e) => setS2(e.target.value)} autoComplete="off" spellCheck={false} />
            </div>
            <div className="field">
              <label htmlFor="swid">SWID</label>
              <input id="swid" className="input mono" value={swid} onChange={(e) => setSwid(e.target.value)} placeholder="{XXXXXXXX-XXXX-…}" autoComplete="off" spellCheck={false} />
            </div>
          </div>
        </div>
      </details>
      {create.error && <ErrorBox error={create.error} />}
      <div className="row">
        <button className="btn" type="submit" disabled={create.isPending}>{create.isPending ? "Reading league from ESPN…" : "Connect league"}</button>
      </div>
    </form>
  );
}

function DemoForm() {
  const create = useCreate();
  const [teams, setTeams] = useState(10);
  return (
    <div className="stack">
      <h2 className="h2">Demo league</h2>
      <p className="small muted" style={{ margin: 0 }}>
        A league using ESPN's default points scoring (the same weights as most ESPN points leagues), drafted by ADP from the live 2026–27 player pool and NBA schedule. Invite friends to it to try the hub features.
      </p>
      <div className="row wrap">
        <label className="label" htmlFor="tc">Teams</label>
        <select id="tc" className="select" style={{ width: 90 }} value={teams} onChange={(e) => setTeams(Number(e.target.value))}>
          {[8, 10, 12].map((n) => <option key={n}>{n}</option>)}
        </select>
        <button className="btn" onClick={() => create.mutate({ source: "demo", team_count: teams, name: "Demo League" })} disabled={create.isPending}>
          {create.isPending ? "Drafting…" : "Create demo league"}
        </button>
      </div>
      {create.error && <ErrorBox error={create.error} />}
    </div>
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
    <form className="stack" onSubmit={(e) => { e.preventDefault(); if (file) upload.mutate(); }}>
      <h2 className="h2">Import rosters from a file</h2>
      <p className="small muted" style={{ margin: 0 }}>
        CSV with <span className="mono">team, player</span> columns (optional <span className="mono">owner, slot</span>), or JSON with a <span className="mono">teams</span> list. Players are matched to ESPN's
        pool by name; stats, injuries and schedule keep refreshing. Default ESPN points scoring is applied and can be edited afterwards.{" "}
        <a href={`${serverUrl()}/api/import/example.csv`} target="_blank" rel="noreferrer">Example CSV</a>
      </p>
      <div className="grid grid-2">
        <div className="field">
          <label htmlFor="f">File</label>
          <input id="f" className="input" type="file" accept=".csv,.json" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={{ paddingTop: 6 }} />
        </div>
        <div className="field">
          <label htmlFor="fn">League name</label>
          <input id="fn" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="My league" />
        </div>
      </div>
      {upload.error && <ErrorBox error={upload.error} />}
      <div><button className="btn" disabled={!file || upload.isPending}>{upload.isPending ? "Importing…" : "Import league"}</button></div>
    </form>
  );
}

function JoinForm() {
  const nav = useNavigate();
  const [code, setCode] = useState("");
  return (
    <form className="row wrap" onSubmit={(e) => { e.preventDefault(); const c = code.trim().split("/").pop(); if (c) nav(`/join/${c.toUpperCase()}`); }}>
      <div className="field" style={{ flex: 1, minWidth: 220 }}>
        <label htmlFor="code">Invite code or link</label>
        <input id="code" className="input mono" value={code} onChange={(e) => setCode(e.target.value)} placeholder="e.g. 7KQ2MZ4P" required />
      </div>
      <button className="btn" style={{ alignSelf: "flex-end" }}>Continue</button>
    </form>
  );
}

function FlaimForm() {
  const create = useCreate();
  const [token, setToken] = useState("");
  const [platform, setPlatform] = useState("espn");
  const [leagueId, setLeagueId] = useState("");
  return (
    <form className="stack" onSubmit={(e) => { e.preventDefault(); create.mutate({ source: "flaim", flaim: { flaim_token: token, platform, league_id: leagueId } }); }}>
      <div className="row"><h2 className="h2">Flaim connector</h2><span className="badge warn">Unverified</span></div>
      <p className="small muted" style={{ margin: 0 }}>
        Flaim exposes ESPN, Yahoo and Sleeper leagues through an MCP server meant for AI assistants. Its output format isn't published, so this adapter
        maps rosters defensively. You'll need a Flaim access token from Flaim's sign-in flow. For ESPN leagues the direct ESPN connector is more reliable.
      </p>
      <div className="grid grid-3">
        <div className="field">
          <label htmlFor="ft">Flaim access token</label>
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
      <div><button className="btn" disabled={create.isPending}>{create.isPending ? "Connecting…" : "Connect via Flaim"}</button></div>
    </form>
  );
}
