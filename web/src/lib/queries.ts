import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type {
  AiStatus,
  Breakouts,
  ActivityItem,
  League,
  LeagueSummary,
  LineupDay,
  Member,
  ModelCard,
  NbaGames,
  NewsItem,
  PlayerDetail,
  PlayerRow,
  TeamDetail,
  TeamReport,
  TeamStrength,
  WeeklyPoints,
} from "./types";

const L = (id: string, ...rest: unknown[]) => ["league", id, ...rest];

export const useLeagues = () => useQuery({ queryKey: ["leagues"], queryFn: () => api<LeagueSummary[]>("/api/leagues") });

export const useLeague = (id: string) =>
  useQuery({
    queryKey: L(id, "meta"),
    queryFn: () => api<League>(`/api/leagues/${id}`),
    refetchInterval: (q) => (q.state.data?.sync.status === "running" ? 3000 : false),
  });

export const useRankings = (id: string, enabled = true) =>
  useQuery({
    queryKey: L(id, "rankings"),
    queryFn: () => api<{ teams: TeamStrength[]; scoring: string; as_of: { date: string | null } }>(`/api/leagues/${id}/rankings`),
    enabled,
  });

export const useWeekly = (id: string, enabled = true) =>
  useQuery({ queryKey: L(id, "weekly"), queryFn: () => api<WeeklyPoints>(`/api/leagues/${id}/weekly`), enabled });

export const useTeam = (id: string, teamId: number | null) =>
  useQuery({
    queryKey: L(id, "team", teamId),
    queryFn: () => api<TeamDetail>(`/api/leagues/${id}/teams/${teamId}`),
    enabled: teamId != null,
  });

export const useTeamReport = (id: string, teamId: number | null) =>
  useQuery({
    queryKey: L(id, "report", teamId),
    queryFn: () => api<TeamReport>(`/api/leagues/${id}/teams/${teamId}/report`),
    enabled: teamId != null,
  });

export const useLineup = (id: string, teamId: number | null, days = 7) =>
  useQuery({
    queryKey: L(id, "lineup", teamId, days),
    queryFn: () => api<{ days: LineupDay[] }>(`/api/leagues/${id}/teams/${teamId}/lineup?days=${days}`),
    enabled: teamId != null,
  });

export const usePlayers = (id: string, params: Record<string, string | number | undefined>) => {
  const qs = new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => [k, String(v)]),
  ).toString();
  return useQuery({
    queryKey: L(id, "players", qs),
    queryFn: () => api<{ players: PlayerRow[]; total: number }>(`/api/leagues/${id}/players?${qs}`),
    placeholderData: (prev) => prev,
  });
};

export const usePlayer = (id: string, pid: string | null) =>
  useQuery({
    queryKey: L(id, "player", pid),
    queryFn: () => api<PlayerDetail>(`/api/leagues/${id}/players/${pid}`),
    enabled: !!pid,
  });

export const useNews = (id: string) => useQuery({ queryKey: L(id, "news"), queryFn: () => api<NewsItem[]>(`/api/leagues/${id}/news`) });
export const useActivity = (id: string) =>
  useQuery({ queryKey: L(id, "activity"), queryFn: () => api<ActivityItem[]>(`/api/leagues/${id}/activity`) });
export const useMembers = (id: string) =>
  useQuery({ queryKey: L(id, "members"), queryFn: () => api<Member[]>(`/api/leagues/${id}/members`) });
export const GAMES_LIVE_MS = 30_000;
export const GAMES_IDLE_MS = 600_000;
const GAME_WINDOW_MS = 6 * 3600_000; // a game that tipped off longer ago than this is over, whatever the feed says

/** How soon to ask for the NBA games again: every 30 seconds while a game is on (it has tipped off and isn't
 *  final), otherwise every 10 minutes, or at the next tip-off if that comes sooner. */
export function gamesRefreshMs(feed: NbaGames | undefined, now: number = Date.now()): number {
  let next = GAMES_IDLE_MS;
  for (const d of feed?.days ?? []) {
    for (const g of d.games) {
      if (g.status === "final") continue;
      const tip = g.tip ? Date.parse(g.tip) : NaN;
      if (g.status === "live" || (tip <= now && now - tip < GAME_WINDOW_MS)) return GAMES_LIVE_MS;
      if (tip > now) next = Math.min(next, Math.max(tip - now + 5_000, GAMES_LIVE_MS));
    }
  }
  return next;
}

/** Tonight's NBA games with the game model's odds, the same for every league, and the score and live chance of the
 *  games that are on. Asked for again every 30 seconds while one is (see gamesRefreshMs). */
export const useNbaGames = () =>
  useQuery({ queryKey: ["nba-games"], queryFn: () => api<NbaGames>("/api/games"), staleTime: 20_000,
             refetchInterval: (q) => gamesRefreshMs(q.state.data) });
export const useModelCard = () => useQuery({ queryKey: ["model"], queryFn: () => api<ModelCard>("/api/model"), staleTime: 3600_000 });
/** Saves which team the signed-in member manages; every page then defaults to it. */
export function useClaimTeam(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (teamId: number | null) => api<{ team_id: number | null }>(`/api/leagues/${id}/members/me/team`, { body: { team_id: teamId } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: L(id) }),
  });
}

export const useAiStatus = () =>
  useQuery({ queryKey: ["ai-status"], queryFn: () => api<AiStatus>("/api/ai/status"), staleTime: 300_000 });

export const useBreakouts = (id: string, status: "all" | "fa" | "rostered" = "all") =>
  useQuery({
    queryKey: L(id, "breakouts", status),
    queryFn: () => api<Breakouts>(`/api/leagues/${id}/breakouts?status=${status}&limit=100`),
    placeholderData: (prev) => prev,
  });
