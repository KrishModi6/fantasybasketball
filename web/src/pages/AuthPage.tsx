import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { AppMenuButton, BrandMark, ThemeToggle } from "../components/Layout";
import { Segmented } from "../components/ui";
import { isDesktop, serverUrl, setServerUrl } from "../lib/api";
import { useAuth } from "../lib/auth";

export function AuthForm({ onDone }: { onDone?: () => void }) {
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
    <form onSubmit={submit} className="panel form">
      <Segmented label="Account" value={mode} onChange={(v) => { setMode(v); setError(null); }}
        options={[{ value: "signin", label: "Sign in" }, { value: "register", label: "Create account" }]} />
      {mode === "register" && (
        <div className="field">
          <label htmlFor="name">Display name</label>
          <input id="name" className="input" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} autoComplete="nickname" />
          <span className="hint">Shown to other members of your leagues.</span>
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
      <button className="btn primary lg" type="submit" disabled={busy}>
        {busy ? "Working…" : mode === "signin" ? "Sign in" : "Create account"}
      </button>
    </form>
  );
}

function ServerSetting() {
  const [value, setValue] = useState(serverUrl());
  return (
    <div className="panel form">
      <div className="field">
        <label htmlFor="srv">Server address</label>
        <div className="row">
          <input id="srv" className="input wide" placeholder="https://courtside.example.com" value={value} onChange={(e) => setValue(e.target.value)} />
          <button type="button" className="btn" onClick={() => { setServerUrl(value || null); window.location.reload(); }}>Save</button>
        </div>
        <span className="hint">Currently {serverUrl() || "this website"}. Everyone in a league must use the same server.</span>
      </div>
    </div>
  );
}

export function StandaloneTop() {
  return (
    <header className="topbar">
      <AppMenuButton />
      <Link to="/" className="brand"><BrandMark /> Courtside</Link>
      <span className="spacer" />
      <ThemeToggle />
    </header>
  );
}

export function AuthPage() {
  const { user } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [showServer, setShowServer] = useState(isDesktop());
  const from = (loc.state as { from?: string } | null)?.from ?? "/";
  if (user) return <Navigate to={from} replace />;
  return (
    <div className="standalone">
      <StandaloneTop />
      <main className="center">
        <div className="auth-box">
          <div className="intro">
            <h1>Sign in to Courtside</h1>
            <p>Projections, power rankings, matchup simulation and trade analysis for your fantasy basketball league.</p>
          </div>
          <AuthForm onDone={() => nav(from, { replace: true })} />
          {showServer ? <ServerSetting /> : (
            <button className="linkbtn small muted" style={{ justifySelf: "start" }} onClick={() => setShowServer(true)}>Use a different server</button>
          )}
        </div>
      </main>
    </div>
  );
}
