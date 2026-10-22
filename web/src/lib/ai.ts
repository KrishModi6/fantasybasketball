// Shared helpers for the AI assistant, kept out of the lazily loaded page so other pages can link to it.

export const TOOL_LABELS: Record<string, string> = {
  league_overview: "Standings",
  team_roster: "Roster",
  free_agents: "Free agents",
  waiver_recommendations: "Waiver simulation",
  player_details: "Player projection",
  evaluate_trade: "Trade evaluator",
  trade_ideas: "Trade finder",
  simulate_matchup: "Matchup simulation",
  league_news: "News",
};

/** Link to the assistant that asks a question (or runs a preset) as soon as the page opens. */
export function askPath(leagueId: string, q: { prompt?: string; preset?: string }): string {
  const params = new URLSearchParams();
  if (q.preset) params.set("preset", q.preset);
  else if (q.prompt) params.set("ask", q.prompt);
  return `/l/${leagueId}/assistant?${params.toString()}`;
}
