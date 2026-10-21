import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftRight,
  BookOpen,
  ChevronsUpDown,
  Gauge,
  LayoutDashboard,
  LogOut,
  Moon,
  RefreshCw,
  Shirt,
  Swords,
  Sun,
  Trophy,
  Users,
} from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { initials, relTime, sourceLabel } from "../lib/format";
import { useLeague } from "../lib/queries";
import { useLeagueSocket } from "../lib/realtime";
import { useToast } from "../lib/toast";
import type { League, TeamRef } from "../lib/types";
import { PlayerDrawer } from "./PlayerDrawer";
import { ErrorBox, LoadingBlock } from "./ui";

export function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="12" fill="var(--ink)" />
      <circle cx="32" cy="32" r="19" fill="none" stroke="var(--accent)" strokeWidth="4" />
      <path d="M13 32h38M32 13v38" stroke="var(--accent)" strokeWidth="3" />
      <path d="M19.5 18.5c6 4.2 6 22.8 0 27M44.5 18.5c-6 4.2-6 22.8 0 27" fill="none" stroke="var(--accent)" strokeWidth="3" />
    </svg>
  );
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<string>(() => document.documentElement.dataset.theme ??
    (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  const flip = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("cs.theme", next);
    } catch {
      /* ignore */
    }
    setTheme(next);
  };
  return (
    <button className="btn ghost sm icon" onClick={flip} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
      {theme === "dark" ? <Sun /> : <Moon />}
    </button>
  );
}

interface LeagueCtx {
  league: League;
  myTeamId: number | null;
  teamName: (id: number | null | undefined) => string;
  teams: TeamRef[];
  openPlayer: (id: string) => void;
}
const Ctx = createContext<LeagueCtx | null>(null);
export const useLeagueCtx = () => {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLeagueCtx outside league layout");
  return v;
};

function SyncPill({ league }: { league: League }) {
  const qc = useQueryClient();
  const toast = useToast();
  const sync = useMutation({
    mutationFn: () => api(`/api/leagues/${league.id}/sync`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["league", league.id, "meta"] }),
    onError: (e: Error) => toast(e.message, "err"),
  });
  const s = league.sync.status;
  const cls = s === "running" ? "run" : s === "error" ? "err" : "";
  const label = s === "running" ? "Refreshing…" : s === "error" ? "Refresh failed" : `Updated ${relTime(league.sync.last_synced_at)}`;
  return (
    <button className={`sync-pill ${cls}`} onClick={() => sync.mutate()} disabled={s === "running" || sync.isPending}
      title={league.sync.error ?? `Data from ${sourceLabel[league.source]}. Auto-refreshes every ${league.sync.interval_hours}h. Click to refresh now.`}>
      <span className="dot" />
      <span className="label" aria-hidden="true">{label}</span>
      <span className="sr-only">{label}</span>
      {s !== "running" && <RefreshCw size={13} aria-hidden="true" />}
    </button>
  );
}

export function LeagueLayout() {
  const { leagueId = "" } = useParams();
  const { data: league, error, isLoading } = useLeague(leagueId);
  const { user, signOut } = useAuth();
  const nav = useNavigate();
  const [search, setSearch] = useSearchParams();
  useLeagueSocket(leagueId);

  const ctx = useMemo<LeagueCtx | null>(() => {
    if (!league) return null;
    const byId = new Map(league.teams.map((t) => [t.id, t]));
    return {
      league,
      myTeamId: league.me?.team_id ?? null,
      teams: league.teams,
      teamName: (id) => (id == null ? "Free agent" : byId.get(id)?.name ?? `Team ${id}`),
      openPlayer: (id) => {
        const next = new URLSearchParams(search);
        next.set("player", id);
        setSearch(next);
      },
    };
  }, [league, search, setSearch]);

  const playerId = search.get("player");
  const closePlayer = () => {
    const next = new URLSearchParams(search);
    next.delete("player");
    setSearch(next);
  };

  const base = `/l/${leagueId}`;
  const links: { to: string; label: string; icon: ReactNode; end?: boolean; mobile?: boolean }[] = [
    { to: base, label: "Overview", icon: <LayoutDashboard />, end: true, mobile: true },
    { to: `${base}/rankings`, label: "Power rankings", icon: <Trophy /> },
    { to: `${base}/team`, label: "My team", icon: <Shirt />, mobile: true },
    { to: `${base}/matchups`, label: "Matchups", icon: <Swords />, mobile: true },
    { to: `${base}/players`, label: "Players", icon: <Gauge />, mobile: true },
    { to: `${base}/trades`, label: "Trades", icon: <ArrowLeftRight />, mobile: true },
    { to: `${base}/league`, label: "League hub", icon: <Users /> },
  ];

  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className="sidebar" aria-label="Primary">
        <Link to="/" className="brand" aria-label="Courtside home">
          <BrandMark />
          <span className="brand-name">Courtside</span>
        </Link>
        <button className="league-switch" onClick={() => nav("/")} aria-label="Switch league">
          <span className="kicker">League</span>
          <span className="row between">
            <span className="name">{league?.name ?? "…"}</span>
            <ChevronsUpDown size={15} className="faint" />
          </span>
        </button>
        <nav className="nav">
          {links.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.end}>
              {l.icon}
              {l.label}
            </NavLink>
          ))}
          <div className="kicker nav-label">Reference</div>
          <NavLink to={`${base}/methodology`}>
            <BookOpen />
            Methodology
          </NavLink>
        </nav>
        <div className="sidebar-foot">
          <div className="user-row">
            <span className="avatar" aria-hidden="true">{initials(user?.display_name ?? "?")}</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="strong" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{user?.display_name}</div>
              <div className="tiny muted">{league?.me?.role === "owner" ? "League owner" : "Member"}</div>
            </div>
            <ThemeToggle />
            <button className="btn ghost sm icon" onClick={signOut} aria-label="Sign out">
              <LogOut />
            </button>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <Link to="/" className="show-sm" aria-label="All leagues">
            <BrandMark size={26} />
          </Link>
          <div className="crumbs">
            <span className="league">{league?.name}</span>
            {league && <span className="badge hide-sm">{sourceLabel[league.source]} · {league.season - 1}–{String(league.season).slice(2)}</span>}
          </div>
          <div className="spacer" />
          {league && <SyncPill league={league} />}
          <span className="show-sm"><ThemeToggle /></span>
        </header>
        <main id="main" className="content" tabIndex={-1}>
          {isLoading && <LoadingBlock rows={8} />}
          {error && <ErrorBox error={error} />}
          {ctx && (
            <Ctx.Provider value={ctx}>
              {!ctx.league.settings && ctx.league.sync.status !== "running" && (
                <div className="callout neg" style={{ marginBottom: 18 }}>
                  This league hasn't loaded any data yet{ctx.league.sync.error ? `: ${ctx.league.sync.error}` : "."} Use the refresh button above to try again.
                </div>
              )}
              <Outlet />
              {playerId && <PlayerDrawer playerId={playerId} onClose={closePlayer} />}
            </Ctx.Provider>
          )}
        </main>
      </div>

      <nav className="tabbar" aria-label="Primary">
        {links.filter((l) => l.mobile).map((l) => (
          <NavLink key={l.to} to={l.to} end={l.end}>
            {l.icon}
            {l.label === "Power rankings" ? "Rankings" : l.label === "My team" ? "Team" : l.label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
