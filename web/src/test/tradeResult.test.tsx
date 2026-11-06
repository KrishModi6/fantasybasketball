import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { TradeResult } from "../pages/TradesPage";
import type { TradeEval, TradeSide } from "../lib/types";

vi.mock("../components/Layout", () => ({
  useLeagueCtx: () => ({ openPlayer: () => {}, teamName: (id: number) => `Team ${id}` }),
}));

const side = (team_id: number, verdict: TradeSide["verdict"]): TradeSide => ({
  team_id,
  team_name: `Team ${team_id}`,
  gives: [],
  receives: [],
  auto_drops: [],
  auto_adds: [],
  open_spot_adds: [],
  counted_as_pickups: [],
  return_drops: [],
  before_ros: 1000,
  after_ros: 1000,
  delta_ros: 0,
  delta_pct: 0,
  delta_per_week: 0,
  delta_vs_roster_per_week: 0,
  delta_this_week: 0,
  delta_playoffs: 0,
  raw_value_delta: 0,
  before_playoff_odds: null,
  after_playoff_odds: null,
  before_title_odds: null,
  after_title_odds: null,
  verdict,
});

const trade = (fairness: TradeEval["fairness"], verdict: TradeSide["verdict"]): TradeEval => ({
  sides: [side(1, verdict), side(2, verdict)],
  players: [],
  math: [],
  summary: "The summary.",
  fairness,
});

describe("TradeResult", () => {
  afterEach(cleanup);

  it("says why a balanced trade is balanced", () => {
    const reason = "A Guy projects 1.4 FP/G more than B Guy, with about the same games left.";
    render(<TradeResult ev={trade({ label: "balanced", partner_accept_likelihood: 0.5, reason }, "neutral")} />);
    expect(screen.getByText(/A Guy projects 1\.4 FP\/G more than B Guy/)).toHaveTextContent("The summary.");
  });

  it("adds nothing to any other trade", () => {
    render(<TradeResult ev={trade({ label: "favors Team 2", partner_accept_likelihood: 0.9, reason: "" }, "advantageous")} />);
    expect(screen.getByText(/The summary\./)).not.toHaveTextContent("projects");
  });
});
