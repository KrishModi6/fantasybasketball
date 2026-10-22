import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, HashRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { LeagueLayout } from "./components/Layout";
import { LoadingBlock } from "./components/ui";
import { UpdateBanner } from "./components/UpdateBanner";
import { AuthProvider, useAuth } from "./lib/auth";
import { rememberedPath } from "./lib/nav";
import { useLeagues } from "./lib/queries";
import { ToastProvider } from "./lib/toast";
import { AuthPage } from "./pages/AuthPage";
import { JoinPage } from "./pages/JoinPage";
import { LeaguesPage } from "./pages/LeaguesPage";
import { OverviewPage } from "./pages/OverviewPage";

const RankingsPage = lazy(() => import("./pages/RankingsPage"));
const TeamPage = lazy(() => import("./pages/TeamPage"));
const PlayersPage = lazy(() => import("./pages/PlayersPage"));
const TradesPage = lazy(() => import("./pages/TradesPage"));
const MatchupsPage = lazy(() => import("./pages/MatchupsPage"));
const LeagueHubPage = lazy(() => import("./pages/LeagueHubPage"));
const MethodologyPage = lazy(() => import("./pages/MethodologyPage"));
const AssistantPage = lazy(() => import("./pages/AssistantPage"));

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="content"><LoadingBlock /></div>;
  if (!user) return <Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />;
  return <>{children}</>;
}

/** Start page: back to wherever you were last, else your only league, else the league list. */
function Home() {
  const { data, isLoading } = useLeagues();
  if (isLoading) return <div className="content"><LoadingBlock /></div>;
  const last = rememberedPath();
  const lastId = last?.match(/^\/l\/([^/?#]+)/)?.[1];
  if (last && lastId && data?.some((l) => l.id === lastId)) return <Navigate to={last} replace />;
  if (data?.length === 1) return <Navigate to={`/l/${data[0]!.id}`} replace />;
  return <LeaguesPage />;
}

const page = (el: ReactNode) => <Suspense fallback={<LoadingBlock rows={8} />}>{el}</Suspense>;

// The desktop app loads files from disk, where only hash routing survives reloads.
const Router = window.location.protocol === "file:" ? HashRouter : BrowserRouter;

export function App() {
  return (
    <Router>
      <ToastProvider>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<AuthPage />} />
            <Route path="/join/:code" element={<JoinPage />} />
            <Route path="/" element={<RequireAuth><Home /></RequireAuth>} />
            <Route path="/leagues" element={<RequireAuth><LeaguesPage /></RequireAuth>} />
            <Route path="/l/:leagueId" element={<RequireAuth><LeagueLayout /></RequireAuth>}>
              <Route index element={<OverviewPage />} />
              <Route path="rankings" element={page(<RankingsPage />)} />
              <Route path="team" element={page(<TeamPage />)} />
              <Route path="team/:teamId" element={page(<TeamPage />)} />
              <Route path="players" element={page(<PlayersPage />)} />
              <Route path="trades" element={page(<TradesPage />)} />
              <Route path="matchups" element={page(<MatchupsPage />)} />
              <Route path="assistant" element={page(<AssistantPage />)} />
              <Route path="league" element={page(<LeagueHubPage />)} />
              <Route path="methodology" element={page(<MethodologyPage />)} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          <UpdateBanner />
        </AuthProvider>
      </ToastProvider>
    </Router>
  );
}
