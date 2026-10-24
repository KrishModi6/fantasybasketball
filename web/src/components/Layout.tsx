import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useIsFetching, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, LogOut, Moon, RefreshCw, Sun } from "lucide-react";
import { api, isLocalApp } from "../lib/api";
import { useAuth } from "../lib/auth";
import { count, pct, record, relTime, shortDate, sourceLabel } from "../lib/format";
import { canGoBack, rememberPath } from "../lib/nav";
import { useLeague, useLeagues, useModelCard, useRankings } from "../lib/queries";
import { useAutoRefresh, useLeagueSocket, useLive, useRefreshOnSync } from "../lib/realtime";
import { useToast } from "../lib/toast";
import type { League, TeamRef } from "../lib/types";
import { InviteButton } from "./Invite";
import { PlayerDrawer } from "./PlayerDrawer";
import { VersionBadge } from "./VersionBadge";
import { ErrorBox, LoadingBlock, Notice } from "./ui";

export function BrandMark({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="12" cy="12" r="9.5" />
      <path d="M2.5 12h19M12 2.5v19M5.6 5c2.6 2.2 2.6 11.8 0 14M18.4 5c-2.6 2.2-2.6 11.8 0 14" />
    </svg>
  );
}

export function ThemeToggle() {
  const [light, setLight] = useState(() => document.documentElement.dataset.theme === "light");
  const flip = () => {
    const next = !light;
    if (next) document.documentElement.dataset.theme = "light";
    else delete document.documentElement.dataset.theme;
    try {
      localStorage.setItem("cs.ui-theme", next ? "light" : "dark");
    } catch {
      /* storage unavailable */
    }
    setLight(next);
  };
  return (
    <button className="btn ghost icon" onClick={flip} aria-label={`Switch to ${light ? "dark" : "light"} theme`} title="Theme">
      {light ? <Moon /> : <Sun />}
    </button>
  );
}

/** Steps back through in-app history; with none (e.g. just after launch), goes to `fallback` instead. */
export function BackButton({ fallback }: { fallback?: string }) {
  const nav = useNavigate();
  const { pathname } = useLocation(); // re-renders on every navigation, so the button state stays current
  const back = canGoBack();
  const target = !back && fallback && fallback.replace(/\/$/, "") !== pathname.replace(/\/$/, "") ? fallback : null;
  return (
    <button className="btn ghost icon" onClick={() => (back ? nav(-1) : target && nav(target))} disabled={!back && !target}
      aria-label="Back" title="Back">
      <ArrowLeft />
    </button>
  );
}

/** Thin progress line while any query is in flight. */
export function FetchLine() {
  const n = useIsFetching();
  return n > 0 ? <div className="loading-line" aria-hidden="true" /> : null;
}

interface LeagueCtx {
  league: League;
  myTeamId: number | null;
  teamName: (id: number | null | undefined) => string;
  teams: TeamRef[];
  openPlayer: (id: string) => void;
  live: boolean; // today's games are on, so scores refresh every few minutes
}
const Ctx = createContext<LeagueCtx | null>(null);
export const useLeagueCtx = () => {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLeagueCtx outside league layout");
  return v;
};

/**
 * How big the league is, for headers. Each desktop app keeps its own copy of the league, so league mates who
 * join with a code never become members on this computer: count teams there, members on a shared server.
 */
export const leagueSize = (league: League) =>
  isLocalApp() ? count(league.teams.length, "team") : count(league.member_count, "member");

function SyncStatus({ league }: { league: League }) {
  const qc = useQueryClient();
  const toast = useToast();
  const sync = useMutation({
    mutationFn: () => api<{ status: string; error?: string | null }>(`/api/leagues/${league.id}/sync`, { method: "POST" }),
    onSuccess: (r) => {
      // Hosts with inline syncing return the finished result; others report "started".
      if (r?.status === "error") toast(`Refresh failed: ${r.error ?? "unknown error"}`, "err");
      qc.invalidateQueries({ queryKey: ["league", league.id, "meta"] });
    },
    onError: (e: Error) => toast(e.message, "err"),
  });
  const s = sync.isPending ? "running" : league.sync.status;
  const label = s === "running" ? "Refreshing" : s === "error" ? "Refresh failed" : `Synced ${relTime(league.sync.last_synced_at)}`;
  return (
    <div className="sync" title={league.sync.error ?? `Auto-refresh every ${league.sync.interval_hours}h`}>
      <span className={`dot ${s === "running" ? "run" : s === "error" ? "err" : ""}`} aria-hidden="true" />
      <span className="hide-sm">{sourceLabel[league.source]} · </span>
      <span className="hide-sm">{label}</span>
      <button className="btn" onClick={() => sync.mutate()} disabled={s === "running" || sync.isPending} aria-label="Refresh league data">
        <RefreshCw /> <span className="hide-sm">Refresh</span>
      </button>
    </div>
  );
}

function LeagueSwitch({ current }: { current: League | undefined }) {
  const { data } = useLeagues();
  const nav = useNavigate();
  return (
    <select className="select league-select" aria-label="League" value={current?.id ?? ""}
      onChange={(e) => nav(e.target.value ? `/l/${e.target.value}` : "/leagues")}>
      {data?.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
      <option value="">All leagues / add a league…</option>
    </select>
  );
}

function Dock({ league, myTeamId }: { league: League; myTeamId: number | null }) {
  const { data } = useRankings(league.id, Boolean(league.settings));
  const nav = useNavigate();
  const teamId = useLocation().pathname.match(/\/team\/(\d+)/)?.[1];
  const rows = data?.teams ?? [];
  return (
    <aside className="dock" aria-label="League table">
      <div className="dock-title"><span>League</span><span className="tiny">by power score</span></div>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr><th className="n">#</th><th>Team</th><th className="n">W–L</th><th className="n">PO%</th></tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.team_id} className={`clickable ${String(t.team_id) === teamId ? "selected" : ""} ${t.team_id === myTeamId ? "mine" : ""}`}
                onClick={() => nav(`/l/${league.id}/team/${t.team_id}`)}>
                <td className="n muted">{t.rank}</td>
                <td style={{ maxWidth: 130, overflow: "hidden", textOverflow: "ellipsis" }} title={t.name}>{t.name}</td>
                <td className="n">{record(t.wins, t.losses, t.ties)}</td>
                <td className="n">{pct(t.playoff_odds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </aside>
  );
}

export function LeagueLayout() {
  const { leagueId = "" } = useParams();
  const { data: league, error, isLoading } = useLeague(leagueId);
  const { data: model } = useModelCard();
  const { user, signOut } = useAuth();
  const [search, setSearch] = useSearchParams();
  const loc = useLocation();
  useEffect(() => rememberPath(loc.pathname, loc.search), [loc.pathname, loc.search]);
  useLeagueSocket(leagueId);
  const live = useLive(league);
  useRefreshOnSync(leagueId, league ? league.sync.last_synced_at : undefined, live);
  useAutoRefresh(league, live);

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
      live,
    };
  }, [league, search, setSearch, live]);

  const playerId = search.get("player");
  const closePlayer = () => {
    const next = new URLSearchParams(search);
    next.delete("player");
    setSearch(next);
  };

  const base = `/l/${leagueId}`;
  const tabs = [
    { to: base, label: "Overview", end: true },
    { to: `${base}/rankings`, label: "Rankings" },
    { to: `${base}/team`, label: "Team" },
    { to: `${base}/matchups`, label: "Matchups" },
    { to: `${base}/players`, label: "Players" },
    { to: `${base}/trades`, label: "Trades" },
    { to: `${base}/assistant`, label: "Assistant" },
    { to: `${base}/league`, label: "League" },
  ];
  const s = league?.settings;

  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>
      <FetchLine />
      <header className="topbar">
        <BackButton fallback={base} />
        <Link to={base} className="brand" aria-label="League home" title="League home"><BrandMark /> <span className="hide-sm">Courtside</span></Link>
        <LeagueSwitch current={league} />
        {league && <span className="small muted hide-sm">{league.season - 1}–{String(league.season).slice(2)}{s ? ` · Week ${s.current_matchup_period}` : ""}</span>}
        <span className="spacer" />
        {league && <SyncStatus league={league} />}
        {league && <InviteButton league={league} />}
        <VersionBadge />
        <ThemeToggle />
        {!isLocalApp() && (
          <>
            <span className="small muted hide-sm">{user?.display_name}</span>
            <button className="btn ghost icon" onClick={signOut} aria-label="Sign out" title="Sign out"><LogOut /></button>
          </>
        )}
      </header>

      <nav className="tabstrip" aria-label="Sections">
        {tabs.map((t) => <NavLink key={t.to} to={t.to} end={t.end}>{t.label}</NavLink>)}
        <NavLink to={`${base}/methodology`} className="end">Methodology</NavLink>
      </nav>

      <div className="workspace">
        {ctx ? <Dock league={ctx.league} myTeamId={ctx.myTeamId} /> : <aside className="dock" />}
        <main id="main" className="main" tabIndex={-1}>
          <div className="content">
            {isLoading && <LoadingBlock rows={8} />}
            {error && <ErrorBox error={error} />}
            {ctx && (
              <Ctx.Provider value={ctx}>
                {!ctx.league.settings && ctx.league.sync.status !== "running" && (
                  <Notice level="error">
                    No data loaded for this league yet{ctx.league.sync.error ? `: ${ctx.league.sync.error}` : "."} Use Refresh to try again.
                  </Notice>
                )}
                <Outlet />
                {playerId && <PlayerDrawer playerId={playerId} onClose={closePlayer} />}
              </Ctx.Provider>
            )}
          </div>
        </main>
      </div>

      <footer className="statusbar">
        <span>{league ? `${league.name} · ${leagueSize(league)}` : "Loading"}</span>
        {s && <span>Week {s.current_matchup_period}{s.today ? ` · ${shortDate(s.today, true)}` : ""}</span>}
        {live && (
          <span className="live-label" title="Games are on: scores and today's points refresh from ESPN every few minutes while Courtside is open.">
            <span className="dot live" aria-hidden="true" />Live
          </span>
        )}
        {league && <span>{sourceLabel[league.source]} data, synced {relTime(league.sync.last_synced_at)}</span>}
        {model?.trained && <span>Model trained through {model.trained_through ? `${model.trained_through - 1}–${String(model.trained_through).slice(2)}` : "–"}</span>}
        <span>{league?.me?.role === "owner" ? "League owner" : "Member"}</span>
        {window.courtside?.version && <span>Courtside {window.courtside.version}</span>}
      </footer>
    </div>
  );
}
