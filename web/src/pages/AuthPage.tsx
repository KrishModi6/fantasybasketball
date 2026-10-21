import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { BrandMark, ThemeToggle } from "../components/Layout";
import { Segmented } from "../components/ui";
import { isDesktop, serverUrl, setServerUrl } from "../lib/api";
import { useAuth } from "../lib/auth";

function CourtArt() {
  return (
    <svg className="court" viewBox="0 0 400 400" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="20" y="20" width="360" height="360" />
      <circle cx="200" cy="20" r="60" />
      <rect x="140" y="20" width="120" height="150" />
      <circle cx="200" cy="170" r="60" />
      <path d="M50 20v90a150 150 0 0 0 300 0V20" />
      <circle cx="200" cy="380" r="60" />
    </svg>
  );
}

export function AuthForm({ onDone, compact = false }: { onDone?: () => void; compact?: boolean }) {
  const { signIn, register } = useAuth();
  const [mode, setMode] = useState<"signin" | "register">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "signin") await signIn(email, password);
      else await register(email, password, name);
      onDone?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="stack" style={{ gap: 16 }}>
      {!compact && (
        <div className="stack" style={{ gap: 6 }}>
          <div className="kicker accent">{mode === "signin" ? "Welcome back" : "New here"}</div>
          <h1 className="h1">{mode === "signin" ? "Sign in" : "Create account"}</h1>
        </div>
      )}
      <Segmented label="Account" value={mode} onChange={(v) => { setMode(v); setError(null); }}
        options={[{ value: "signin", label: "Sign in" }, { value: "register", label: "Create account" }]} />
      {mode === "register" && (
        <div className="field">
          <label htmlFor="name">Display name</label>
          <input id="name" className="input" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} autoComplete="nickname" />
          <span className="hint">What your league-mates will see.</span>
        </div>
      )}
      <div className="field">
        <label htmlFor="email">Email</label>
        <input id="email" className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
      </div>
      <div className="field">
        <label htmlFor="password">Password</label>
        <input id="password" className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
          minLength={mode === "register" ? 8 : undefined} autoComplete={mode === "signin" ? "current-password" : "new-password"} />
        {mode === "register" && <span className="hint">At least 8 characters.</span>}
      </div>
      {error && <div className="error-text" role="alert">{error}</div>}
      <button className="btn" type="submit" disabled={busy} style={{ height: 42 }}>
        {busy ? "Working…" : mode === "signin" ? "Sign in" : "Create account"}
      </button>
    </form>
  );
}

function ServerSetting() {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(serverUrl());
  const current = serverUrl() || "this website";
  if (!editing) {
    return (
      <p className="tiny muted" style={{ margin: 0 }}>
        Server: <span className="mono">{current}</span>{" "}
        <button type="button" className="btn ghost sm" onClick={() => setEditing(true)}>Change</button>
      </p>
    );
  }
  return (
    <div className="row">
      <input className="input" aria-label="Server address" placeholder="https://courtside.example.com" value={value} onChange={(e) => setValue(e.target.value)} />
      <button type="button" className="btn secondary" onClick={() => { setServerUrl(value || null); window.location.reload(); }}>Save</button>
    </div>
  );
}

export function AuthPage() {
  const { user } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const from = (loc.state as { from?: string } | null)?.from ?? "/";
  if (user) return <Navigate to={from} replace />;
  return (
    <div className="auth">
      <section className="auth-art">
        <CourtArt />
        <div className="row" style={{ position: "relative" }}>
          <BrandMark size={34} />
          <span className="brand-name" style={{ fontSize: 26 }}>Courtside</span>
        </div>
        <h2 className="headline" style={{ position: "relative" }}>
          Run the <em>numbers</em>
          <br />
          before you
          <br />
          run the trade.
        </h2>
        <div className="ticker" aria-label="What Courtside does">
          <div><b>01</b><span>Projections built from stats first: a 3-season baseline, ESPN, and a backtested ML ensemble.</span><span /></div>
          <div><b>02</b><span>Trades judged on your real daily lineups, not on summed player values.</span><span /></div>
          <div><b>03</b><span>One hub for the whole league. Invite everyone with a link.</span><span /></div>
        </div>
      </section>
      <section className="auth-form">
        <div className="stack" style={{ width: "min(380px, 100%)", gap: 22 }}>
          <div className="row between">
            <span />
            <ThemeToggle />
          </div>
          <AuthForm onDone={() => nav(from, { replace: true })} />
          {(isDesktop() || serverUrl()) && <ServerSetting />}
          {!isDesktop() && !serverUrl() && (
            <details className="tiny muted">
              <summary style={{ cursor: "pointer" }}>Connecting to a different server?</summary>
              <div style={{ marginTop: 8 }}><ServerSetting /></div>
            </details>
          )}
        </div>
      </section>
    </div>
  );
}
