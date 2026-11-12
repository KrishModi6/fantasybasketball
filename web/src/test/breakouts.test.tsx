import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { leagueTabs } from "../lib/nav";
import type { BreakoutRow, Breakouts } from "../lib/types";
import BreakoutsPage from "../pages/BreakoutsPage";

const api = vi.hoisted(() => vi.fn());
const opened = vi.hoisted(() => vi.fn());

vi.mock("../lib/api", () => ({ api, isLocalApp: () => true }));
vi.mock("../components/Layout", () => ({
  useLeagueCtx: () => ({ league: { id: "L1" }, openPlayer: opened, teamName: (id: number) => `Team ${id}` }),
}));

const player = (over: Partial<BreakoutRow> = {}): BreakoutRow => ({
  id: "1", rank: 1, name: "Young Guard", pro_team: "BKN", positions: ["PG"], injury_status: "ACTIVE", fantasy_team_id: null,
  on_waivers: false, age: 20.6, years: 2, probability: 0.558, last_fp_pg: 21.8, last_mpg: 25.1, breakout_line: 26.8,
  espn_fp_pg: 29.4, proj_fp_pg: 30.2, season_fp_pg: 31.4, season_mpg: 31.0, games_played: 11, on_pace: true,
  reasons: [{ factor: "ESPN's projection", effect: 0.61, direction: "up" }, { factor: "Usage", effect: -0.12, direction: "down" }],
  ...over,
});

const body = (over: Partial<Breakouts> = {}): Breakouts => ({
  season: 2027, total: 451, on_pace: 1,
  players: [player(), player({ id: "2", rank: 2, name: "Old Center", age: 33.2, probability: 0.021, fantasy_team_id: 3,
                               season_fp_pg: null, games_played: 0, on_pace: false, reasons: [] })],
  model: {
    definition: { min_prior_games: 20, min_games: 30, min_gain: 5, min_level: 20, scoring: "ESPN default H2H points" },
    base_rate: 0.113, backtest_seasons: [2022, 2023, 2024, 2025, 2026], auc: 0.752, top_n: 25, top_hits: 9,
    espn_auc: 0.656, espn_top_hits: 9, auc_same_players: 0.751, top_hits_same_players: 9.75, brier: 0.0875,
    brier_base_rate: 0.0959,
  },
  ...over,
});

function show(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

beforeEach(() => api.mockReset());
afterEach(cleanup);

describe("the Breakouts page", () => {
  it("lists the candidates with their chance, their line and how the season is going", async () => {
    api.mockResolvedValue(body());
    show(<BreakoutsPage />);
    const first = (await screen.findByText("Young Guard")).closest("tr")!;
    expect(api).toHaveBeenCalledWith("/api/leagues/L1/breakouts?status=all&limit=100");
    expect(within(first).getByText("56%")).toBeInTheDocument();
    expect(within(first).getByText("on pace")).toBeInTheDocument();
    expect(within(first).getByText("26.8")).toBeInTheDocument(); // the line he has to reach
    expect(within(first).getByText("31.4")).toHaveClass("strong"); // and is past
    expect(within(first).getByText(/↑ espn's projection/)).toBeInTheDocument();
    expect(within(first).getByText(/↓ usage/)).toBeInTheDocument();
    expect(within(first).getByText("FA")).toBeInTheDocument();
    const second = screen.getByText("Old Center").closest("tr")!;
    expect(within(second).getByText("2%")).toBeInTheDocument();
    expect(within(second).getByText("Team 3")).toBeInTheDocument();
    expect(within(second).queryByText("on pace")).toBeNull();
  });

  it("says what a breakout is and how the model did on seasons it hadn't seen", async () => {
    api.mockResolvedValue(body());
    show(<BreakoutsPage />);
    await screen.findByText("Young Guard");
    expect(screen.getByText("+5 FP/G")).toBeInTheDocument();
    expect(screen.getByText("to 20+ FP/G over 30+ games")).toBeInTheDocument();
    expect(screen.getByText("11%")).toBeInTheDocument();
    expect(screen.getByText("9.0 of 25")).toBeInTheDocument();
    expect(screen.getByText("of its top 25 broke out, 2021-26")).toBeInTheDocument();
    expect(screen.getByText("On pace in 2026-27")).toBeInTheDocument();
  });

  it("filters to free agents and opens a player", async () => {
    api.mockResolvedValue(body());
    show(<BreakoutsPage />);
    fireEvent.click(await screen.findByText("Young Guard"));
    expect(opened).toHaveBeenCalledWith("1");
    fireEvent.click(screen.getByRole("button", { name: "Free agents" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/leagues/L1/breakouts?status=fa&limit=100"));
    await waitFor(() => expect(screen.queryByText("Fantasy team")).toBeNull());
  });

  it("says so when this copy has no model, or nobody qualifies", async () => {
    api.mockResolvedValue(body({ model: null, players: [], total: 0, on_pace: 0 }));
    show(<BreakoutsPage />);
    expect(await screen.findByText(/no breakout model/)).toBeInTheDocument();
    cleanup();
    api.mockResolvedValue(body({ players: [], total: 0, on_pace: 0 }));
    show(<BreakoutsPage />);
    expect(await screen.findByText("No candidates here")).toBeInTheDocument();
  });

  it("has its own tab beside Players", () => {
    const tabs = leagueTabs("/l/1", false);
    const at = tabs.findIndex((t) => t.label === "Players");
    expect(tabs[at + 1]).toEqual({ to: "/l/1/breakouts", label: "Breakouts" });
  });
});
