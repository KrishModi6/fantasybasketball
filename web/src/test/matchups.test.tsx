import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WeekResults, weekLabel } from "../pages/MatchupsPage";
import type { WeekResult } from "../lib/types";

vi.mock("../components/Layout", () => ({ useLeagueCtx: () => ({}) }));
afterEach(cleanup);

const row = (over: Partial<WeekResult>): WeekResult => ({
  home_id: 1, away_id: 2, home_name: "Hoop Dreams", away_name: "Team Tacos",
  home_score: 1412.5, away_score: 1358, winner: "HOME", consolation: false, ...over,
});

describe("past weeks on the Matchups page", () => {
  it("are in the week picker, marked final", () => {
    const week = { matchup_period: 3, start: "2026-11-02", end: "2026-11-08", is_current: false, is_playoff: false, is_final: true };
    expect(weekLabel(week)).toBe("Week 3 (Nov 2–Nov 8), final");
    expect(weekLabel({ ...week, matchup_period: 5, is_current: true, is_final: false })).toMatch(/, live$/);
  });

  it("show the final scores, the winner in bold and how your week went", () => {
    render(<WeekResults title="Week 3 results" points myTeamId={1} rows={[
      row({}),
      row({ home_id: 3, away_id: 4, home_name: "Bench Mob", away_name: "Paint Kings", home_score: 1200, away_score: 1250.5, winner: "AWAY" }),
    ]} />);
    expect(screen.getByText("You beat Team Tacos, 1,412.5 to 1,358.0.")).toBeInTheDocument();
    const mine = screen.getByText("Hoop Dreams (you)").closest("tr")!;
    expect(mine).toHaveClass("mine");
    expect(within(mine).getByText("1,412.5")).toHaveClass("strong");
    expect(within(mine).getByText("54.5")).toBeInTheDocument(); // the margin
    expect(screen.getByText("Paint Kings")).toHaveClass("strong");
    expect(screen.getByText("Bench Mob")).not.toHaveClass("strong");
  });

  it("say so when you lost or tied, and mark consolation games", () => {
    const { rerender } = render(<WeekResults title="Playoffs results" points myTeamId={2} rows={[row({ consolation: true })]} />);
    expect(screen.getByText("You lost to Hoop Dreams, 1,358.0 to 1,412.5.")).toBeInTheDocument();
    expect(screen.getByText(/consolation/)).toBeInTheDocument();
    rerender(<WeekResults title="Week 4 results" points={false} myTeamId={1} rows={[row({ home_score: 4, away_score: 4, winner: "TIE" })]} />);
    expect(screen.getByText("You tied Team Tacos, 4 to 4.")).toBeInTheDocument();
    expect(screen.getByText("Tie")).toBeInTheDocument();
  });
});
