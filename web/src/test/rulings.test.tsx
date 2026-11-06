import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LeagueRulings } from "../components/Rulings";
import { leagueTabs } from "../lib/nav";
import { ToastProvider } from "../lib/toast";
import type { ReviewTrade, Ruling, RulingNumbers, RulingSide, TradeReview } from "../lib/types";
import AdminPage from "../pages/AdminPage";
import { Board } from "../pages/TradesPage";

const ctx = vi.hoisted(() => ({ isAdmin: true }));
const api = vi.hoisted(() => vi.fn());

vi.mock("../lib/api", () => ({ api }));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ user: { id: "u1" } }) }));
vi.mock("../components/Layout", () => ({
  useLeagueCtx: () => ({
    league: { id: "L1", source: "espn", me: { role: "owner", team_id: 1, is_admin: ctx.isAdmin } },
    openPlayer: () => {},
    teamName: (id: number) => `Team ${id}`,
  }),
}));

const side = (team_id: number, delta: number, verdict: RulingSide["verdict"]): RulingSide => ({
  team_id,
  team_name: `Team ${team_id}`,
  delta_per_week: delta,
  delta_pct: 0.02,
  before_playoff_odds: 0.5,
  after_playoff_odds: 0.62,
  before_title_odds: 0.1,
  after_title_odds: 0.14,
  verdict,
});

const numbers = (over: Partial<RulingNumbers> = {}): RulingNumbers => ({
  sides: [side(1, 8.4, "advantageous"), side(2, -6.2, "disadvantageous")],
  fairness: { label: "favors Team 1", reason: "A Guy projects far more than B Guy." },
  summary: "The summary.",
  completed: false,
  ...over,
});

const trade = { teams: [{ id: 1, name: "Team 1" }, { id: 2, name: "Team 2" }], moves: [], text: "Team 1 sends B Guy to Team 2 for A Guy" };

const ruling = (over: Partial<Ruling> = {}): Ruling => ({
  key: "k1", ruling: "unfair", note: "too lopsided", trade, numbers: numbers(), decided_at: new Date().toISOString(), pending: false, ...over,
});

const row = (over: Partial<ReviewTrade> = {}): ReviewTrade => ({
  key: "k1", at: new Date().toISOString(), on_espn: false, post: { id: 1, author: "Sam", note: "thoughts?" },
  status: "open", status_note: null, trade, numbers: numbers(), ruling: null, ...over,
});

/** Answers each api call from the review and whatever a test adds. */
function serve(review: TradeReview | Error, rulings: Ruling[] = []) {
  api.mockImplementation(async (path: string, opts?: { method?: string }) => {
    if (opts?.method) return {};
    if (path.endsWith("/trades/review")) {
      if (review instanceof Error) throw review;
      return review;
    }
    if (path.endsWith("/trades/rulings")) return rulings;
    return [];
  });
}

function show(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ToastProvider>{ui}</ToastProvider></QueryClientProvider>);
}

describe("the admin dashboard", () => {
  beforeEach(() => {
    ctx.isAdmin = true;
    api.mockReset();
  });
  afterEach(cleanup);

  it("turns away anyone but the admin, without asking the server", () => {
    ctx.isAdmin = false;
    show(<AdminPage />);
    expect(screen.getByText(/Only the league's admin can rule on trades/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Fair" })).not.toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });

  it("shows Courtside's numbers and verdict beside each trade", async () => {
    serve({ reach: "league", trades: [row()] });
    show(<AdminPage />);
    expect(await screen.findByText(trade.text)).toBeInTheDocument();
    expect(screen.getByText("Needs a ruling")).toBeInTheDocument();
    expect(screen.queryByText("Ruled")).not.toBeInTheDocument();
    const [, first, second] = screen.getAllByRole("row");
    expect(within(first!).getByText("+8.4")).toBeInTheDocument();
    expect(within(first!).getByText("advantageous")).toBeInTheDocument();
    expect(within(first!).getAllByText("50% → 62%").length).toBe(1);
    expect(within(first!).getByText("10% → 14%")).toBeInTheDocument();
    expect(within(second!).getByText("−6.2")).toBeInTheDocument();
    expect(within(second!).getByText("disadvantageous")).toBeInTheDocument();
    expect(screen.getByText("favors Team 1")).toBeInTheDocument();
    expect(screen.getByText(/A Guy projects far more than B Guy/)).toBeInTheDocument();
    expect(screen.getByText(/posted by Sam/)).toBeInTheDocument();
  });

  it("says a ruling can't change anything on ESPN, and who it reaches", async () => {
    serve({ reach: "league", trades: [row()] });
    const { unmount } = show(<AdminPage />);
    const notice = await screen.findByText(/everyone's Courtside app/);
    expect(notice).toHaveTextContent("can't block or undo anything on ESPN");
    expect(notice).toHaveTextContent("a real veto still happens on ESPN");
    unmount();

    serve({ reach: "this_copy", trades: [row()] });
    show(<AdminPage />);
    expect(await screen.findByText(/Your rulings stay on this computer/)).toBeInTheDocument();
  });

  it("sends the call and the note", async () => {
    serve({ reach: "league", trades: [row()] });
    show(<AdminPage />);
    fireEvent.change(await screen.findByLabelText(/Your note on/), { target: { value: "  too lopsided " } });
    fireEvent.click(screen.getByRole("button", { name: "Not fair" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/leagues/L1/trades/rulings/k1", { method: "PUT", body: { ruling: "unfair", note: "too lopsided" } }));
    expect(await screen.findByText("Ruling saved.")).toBeInTheDocument();
  });

  it("files a ruled trade under Ruled, where it can be changed or withdrawn", async () => {
    serve({ reach: "league", trades: [row({ ruling: ruling() })] });
    show(<AdminPage />);
    expect(await screen.findByText("Ruled")).toBeInTheDocument();
    expect(screen.queryByText("Needs a ruling")).not.toBeInTheDocument();
    expect(screen.getByText("Admin: Not fair")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not fair" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Fair" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByLabelText(/Your note on/)).toHaveValue("too lopsided");
    fireEvent.click(screen.getByRole("button", { name: "Withdraw" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/leagues/L1/trades/rulings/k1", { method: "DELETE" }));
  });

  it("shows why the server refused", async () => {
    serve({ reach: "league", trades: [row()] });
    show(<AdminPage />);
    await screen.findByText(trade.text);
    api.mockImplementationOnce(async () => {
      throw new Error("Someone else is already this league's admin, so this copy can't rule on its trades");
    });
    fireEvent.click(screen.getByRole("button", { name: "Fair" }));
    expect(await screen.findByText(/Someone else is already this league's admin/)).toBeInTheDocument();
    expect(screen.queryByText("Ruling saved.")).not.toBeInTheDocument();
  });

  it("shows a refusal from loading the page too", async () => {
    serve(new Error("Only the league's admin can do that"));
    show(<AdminPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Only the league's admin can do that");
  });

  it("says where a finished trade's numbers come from, on that trade only", async () => {
    serve({ reach: "league", trades: [
      row({ key: "done", on_espn: true, post: null, status: "done", numbers: numbers({ completed: true }), trade: { ...trade, text: "The finished one" } }),
    ] });
    const { unmount } = show(<AdminPage />);
    expect(await screen.findByText("Happened on ESPN")).toBeInTheDocument();
    expect(screen.getByText(/these numbers come from undoing it on today's rosters/)).toBeInTheDocument();
    unmount();

    serve({ reach: "league", trades: [row()] });
    show(<AdminPage />);
    await screen.findByText(trade.text);
    expect(screen.queryByText(/undoing it/)).not.toBeInTheDocument();
  });

  it("explains a trade with no numbers left, and one that's gone from the lists", async () => {
    serve({ reach: "league", trades: [
      row({ key: "a", post: null, on_espn: true, status: "stale", status_note: "A Guy has moved since this trade.", numbers: null }),
      row({ key: "b", post: null, status: "gone", numbers: null, ruling: ruling({ key: "b" }), trade: { ...trade, text: "The old one" } }),
    ] });
    show(<AdminPage />);
    expect(await screen.findByText("A Guy has moved since this trade.")).toBeInTheDocument();
    expect(screen.getByText("Courtside has no numbers for this trade.")).toBeInTheDocument();
    expect(screen.getByText("No longer listed")).toBeInTheDocument();
    expect(screen.getByText("These are Courtside's numbers from when you ruled.")).toBeInTheDocument();
  });

  it("copes with a trade scored before these numbers existed", async () => {
    const old: RulingSide = { team_id: 1, team_name: null, delta_per_week: null, delta_pct: null, before_playoff_odds: null,
      after_playoff_odds: null, before_title_odds: null, after_title_odds: null, verdict: null };
    serve({ reach: "league", trades: [row({ numbers: numbers({ sides: [old], fairness: { label: "", reason: "" }, summary: "" }) })] });
    show(<AdminPage />);
    expect(await screen.findByText("Team 1")).toBeInTheDocument();
    expect(screen.getByText("no label")).toBeInTheDocument();
  });

  it("says when a ruling hasn't gone out yet", async () => {
    serve({ reach: "league", trades: [row({ ruling: ruling({ pending: true }) })] });
    show(<AdminPage />);
    expect(await screen.findByText(/goes out to the rest of the league/)).toBeInTheDocument();
  });

  it("points to where else trades come from when there are none", async () => {
    serve({ reach: "league", trades: [] });
    show(<AdminPage />);
    expect(await screen.findByText("No trades to rule on yet")).toBeInTheDocument();
    expect(screen.getByText(/analyze it under Trades and post it to the board/)).toBeInTheDocument();
    expect(screen.getByText(/once they go through on ESPN/)).toBeInTheDocument();
  });
});

describe("the Admin tab", () => {
  it("is only there for the admin", () => {
    const labels = (admin: boolean) => leagueTabs("/l/1", admin).map((t) => t.label);
    expect(labels(true)).toContain("Admin");
    expect(labels(false)).not.toContain("Admin");
    expect(labels(false)).toContain("League");
    expect(leagueTabs("/l/1", true).at(-1)?.to).toBe("/l/1/admin");
  });
});

describe("what everyone else sees", () => {
  beforeEach(() => {
    api.mockReset();
  });
  afterEach(cleanup);

  it("labels a ruling as the admin's, apart from Courtside's numbers", async () => {
    serve({ reach: "league", trades: [] }, [ruling()]);
    show(<LeagueRulings />);
    expect(await screen.findByText("League admin's rulings")).toBeInTheDocument();
    expect(screen.getByText("Admin: Not fair")).toBeInTheDocument();
    expect(screen.getByText("too lopsided")).toBeInTheDocument();
    expect(screen.getByText(/They don't change Courtside's numbers/)).toBeInTheDocument();
    expect(screen.getByText("Courtside's numbers when the admin ruled")).toBeInTheDocument();
    expect(screen.getByText("Courtside's verdict")).toBeInTheDocument();
    expect(screen.getByText("advantageous")).toBeInTheDocument();
    expect(screen.queryByText(/undoing it/)).not.toBeInTheDocument();
  });

  it("carries the undo label with a finished trade's numbers", async () => {
    serve({ reach: "league", trades: [] }, [ruling({ numbers: numbers({ completed: true }) })]);
    show(<LeagueRulings />);
    expect(await screen.findByText(/these numbers come from undoing it on today's rosters/)).toBeInTheDocument();
  });

  it("shows nothing until there's a ruling", async () => {
    serve({ reach: "league", trades: [] }, []);
    const { container } = show(<LeagueRulings />);
    await waitFor(() => expect(api).toHaveBeenCalled());
    expect(container.querySelector(".panel")).toBeNull();
  });

  it("tags a board post with the ruling and leaves its numbers alone", async () => {
    const evaluation = {
      sides: [{ delta_per_week: 8.4 }, { delta_per_week: -6.2 }], players: [], status: "open", as_of: new Date().toISOString(),
    };
    const post = { id: 1, author: "Sam", author_id: "u2", team_a: 1, team_b: 2, a_gives: [], b_gives: [], teams: [1, 2],
      moves: [], note: "", evaluation, created_at: new Date().toISOString() };
    api.mockImplementation(async (path: string) => (path.endsWith("/board") ? [{ ...post, ruling: "fair" }, { ...post, id: 2, ruling: null }] : []));
    show(<Board onLoad={() => {}} />);
    expect(await screen.findByText("Admin: Fair")).toBeInTheDocument();
    expect(screen.getAllByText("Admin: Fair")).toHaveLength(1); // the post with no ruling has none
    expect(screen.getAllByText("+8.4")).toHaveLength(2);
    expect(screen.getAllByText("−6.2")).toHaveLength(2);
  });
});
