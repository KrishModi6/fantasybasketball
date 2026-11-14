import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultDay, GamesPanel, mainReason, nbaToday, pick } from "../components/GamesPanel";
import type { NbaGame, NbaGames } from "../lib/types";

const api = vi.hoisted(() => vi.fn());
vi.mock("../lib/api", () => ({ api, isLocalApp: () => true }));

const game = (over: Partial<NbaGame> = {}): NbaGame => ({
  id: "1", tip: "2026-11-14T00:00:00Z", status: "scheduled", neutral: false, home: "IND", away: "NYK", home_win: 0.33,
  home_score: 111, away_score: 116, home_range: [95, 126], away_range: [101, 132], home_pts: null, away_pts: null,
  factors: [{ factor: "Team strength (Elo)", points: -14.5 }, { factor: "Season efficiency", points: -9.8 },
            { factor: "Home court", points: 6.9 }],
  home_out: ["Tyrese Haliburton"], away_out: [], home_questionable: [], away_questionable: [],
  ...over,
});

const feed = (over: Partial<NbaGames> = {}): NbaGames => ({
  available: true, generated_at: new Date().toISOString(), data_through: "2026-11-12", injuries: true,
  backtest: { games: 7393, accuracy: 0.6704, elo_accuracy: 0.6522, home_accuracy: 0.5511 },
  days: [
    { date: "2026-11-13", games: [game(), game({ id: "2", home: "HOU", away: "UTA", home_win: 0.92, home_score: 126, away_score: 106,
                                                  factors: [{ factor: "Season efficiency", points: 8.4 }], home_out: [] })] },
    { date: "2026-11-14", games: [game({ id: "3", home: "BOS", away: "LAL", home_win: 0.61, home_out: [], away_out: ["LeBron James"] })] },
  ],
  ...over,
});

const NOW = new Date("2026-11-13T23:00:00Z"); // 6 PM Eastern on the 13th

function show(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

// Braces matter: a hook that returns a function has it run as cleanup, and mockReset() returns the mock.
beforeEach(() => {
  api.mockReset();
});
afterEach(cleanup);

describe("the NBA games panel", () => {
  it("shows each of today's games with the pick, the predicted score and why", async () => {
    api.mockResolvedValue(feed());
    show(<GamesPanel now={NOW} />);
    const row = (await screen.findByText("IND")).closest("li")!;
    expect(api).toHaveBeenCalledWith("/api/games");
    expect(within(row).getByText("NYK 67%")).toBeInTheDocument(); // the away team is the pick
    expect(within(row).getByText(/Predicted NYK 116, IND 111/)).toBeInTheDocument();
    expect(within(row).getByText(/team strength/)).toBeInTheDocument(); // the biggest thing in the pick's favour
    expect(within(row).getByText(/IND out: Haliburton/)).toBeInTheDocument();
    expect(within(row).getByRole("img", { name: "NYK 67%, IND 33%" })).toBeInTheDocument();
    const second = screen.getByText("HOU").closest("li")!;
    expect(within(second).getByText("HOU 92%")).toBeInTheDocument();
    expect(within(second).getByText(/season efficiency/)).toBeInTheDocument();
    expect(screen.queryByText("BOS")).toBeNull(); // tomorrow's game waits behind its button
    expect(screen.getByText(/picked 67% of winners in 7,393 test games/)).toBeInTheDocument();
    expect(screen.getByText(/players ESPN lists as out counted out/)).toBeInTheDocument();
  });

  it("switches to tomorrow", async () => {
    api.mockResolvedValue(feed());
    show(<GamesPanel now={NOW} />);
    fireEvent.click(await screen.findByRole("button", { name: "Tomorrow" }));
    const row = screen.getByText("BOS").closest("li")!;
    expect(within(row).getByText("BOS 61%")).toBeInTheDocument();
    expect(within(row).getByText(/LAL out: James/)).toBeInTheDocument();
    expect(screen.queryByText("HOU")).toBeNull();
  });

  it("shows the score once a game has started and whether the pick was right", async () => {
    api.mockResolvedValue(feed({ days: [{ date: "2026-11-13", games: [
      game({ status: "final", home_pts: 104, away_pts: 117 }),
      game({ id: "2", home: "ORL", away: "MIL", home_win: 0.78, status: "final", home_pts: 99, away_pts: 105, home_out: [] }),
      game({ id: "3", home: "MIA", away: "CLE", home_win: 0.55, status: "live", home_pts: 55, away_pts: 48, home_out: [] }),
    ] }] }));
    show(<GamesPanel now={NOW} />);
    const first = (await screen.findByText("IND")).closest("li")!;
    expect(within(first).getByText(/Final 117–104/)).toBeInTheDocument();
    expect(within(first).getByText("right")).toBeInTheDocument();
    expect(within(screen.getByText("ORL").closest("li")!).getByText("wrong")).toBeInTheDocument();
    const live = screen.getByText("MIA").closest("li")!;
    expect(within(live).getByText(/Live 48–55/)).toBeInTheDocument();
    expect(within(live).queryByText(/right|wrong/)).toBeNull();
    expect(screen.getByText("Today")).toBeInTheDocument(); // one day with games: a label, no buttons
  });

  it("opens on the next day with games when today has none", async () => {
    api.mockResolvedValue(feed({ days: [{ date: "2026-11-13", games: [] }, { date: "2026-11-14", games: [game({ id: "3", home: "BOS", away: "LAL", home_win: 0.61 })] }] }));
    show(<GamesPanel now={NOW} />);
    expect(await screen.findByText("BOS")).toBeInTheDocument();
    expect(screen.getByText("Tomorrow")).toBeInTheDocument();
  });

  it("says when the injury report couldn't be read", async () => {
    api.mockResolvedValue(feed({ injuries: false }));
    show(<GamesPanel now={NOW} />);
    expect(await screen.findByText(/injury report couldn't be read, so nobody is counted out/)).toBeInTheDocument();
  });

  it.each([
    ["there are no predictions", { available: false } as NbaGames],
    ["no day is listed", feed({ days: [] })],
    ["the only games are in the past", feed({ days: [{ date: "2026-11-12", games: [game()] }] })],
  ])("shows nothing when %s", async (_why, answer) => {
    api.mockResolvedValue(answer);
    const { container } = show(<GamesPanel now={NOW} />);
    await vi.waitFor(() => expect(api).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
  });

  it("shows nothing when the request fails", async () => {
    api.mockImplementation(async () => {
      throw new Error("offline");
    });
    const { container } = show(<GamesPanel now={NOW} />);
    await vi.waitFor(() => expect(api).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
  });
});

describe("reading a game", () => {
  it("picks the likelier team and checks it against the final score", () => {
    const p = pick(game());
    expect([p.team, p.right]).toEqual(["NYK", null]);
    expect(p.chance).toBeCloseTo(0.67);
    expect(pick(game({ home_win: 0.5 })).team).toBe("IND");
    expect(pick(game({ status: "final", home_pts: 120, away_pts: 110 })).right).toBe(false); // picked NYK, IND won
    expect(pick(game({ status: "live", home_pts: 120, away_pts: 110 })).right).toBeNull();
  });

  it("names the evidence that most favours the pick", () => {
    expect(mainReason(game())).toBe("team strength"); // NYK is picked: the factors against the home team
    expect(mainReason(game({ home_win: 0.7, factors: [{ factor: "Injuries and availability", points: -8 }, { factor: "Home court", points: 6.9 }] }))).toBe("home court");
    expect(mainReason(game({ factors: [{ factor: "Home court", points: 6.9 }] }))).toBeNull(); // nothing favours NYK
  });

  it("goes by the NBA's calendar day", () => {
    expect(nbaToday(new Date("2026-11-14T03:30:00Z"))).toBe("2026-11-13"); // 10:30 PM Eastern, still the 13th
    expect(nbaToday(new Date("2026-11-14T06:00:00Z"))).toBe("2026-11-14");
    expect(defaultDay(feed(), "2026-11-13")).toBe("2026-11-13");
    expect(defaultDay(feed(), "2026-11-14")).toBe("2026-11-14");
    expect(defaultDay(feed(), "2026-11-15")).toBeNull();
  });
});
