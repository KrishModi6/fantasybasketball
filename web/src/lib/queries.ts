import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type {
  AiStatus,
  ActivityItem,
  League,
  LeagueSummary,
  LineupDay,
  Member,
  ModelCard,
  NewsItem,
  PlayerDetail,
  PlayerRow,
  TeamDetail,
  TeamReport,
  TeamStrength,
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
export const useModelCard = () => useQuery({ queryKey: ["model"], queryFn: () => api<ModelCard>("/api/model"), staleTime: 3600_000 });
export const useAiStatus = () =>
  useQuery({ queryKey: ["ai-status"], queryFn: () => api<AiStatus>("/api/ai/status"), staleTime: 300_000 });
