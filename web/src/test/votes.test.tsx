import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SortTh, useSort } from "../components/ui";
import { LeagueVotes } from "../components/Votes";
import { ToastProvider } from "../lib/toast";
import type { TradeVotes, VoteTrade } from "../lib/types";

const api = vi.hoisted(() => vi.fn());
vi.mock("../lib/api", () => ({ api, isLocalApp: () => true }));
vi.mock("../components/Layout", () => ({
  useLeagueCtx: () => ({ league: { id: "L1", source: "espn", me: { role: "member", team_id: 1, is_admin: false } } }),
}));

const trade = (over: Partial<VoteTrade> = {}): VoteTrade => ({
  key: "k1", at: new Date().toISOString(), on_espn: true, post: null, status: "done", status_note: null,
  trade: { teams: [], moves: [], text: "Team 1 sends B Guy to Team 2 for A Guy" }, numbers: null, ruling: null,
  votes: { open: true, fair: 3, unfair: 1, mine: null }, ...over,
});

function show(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ToastProvider>{ui}</ToastProvider></QueryClientProvider>);
}

const serve = (data: TradeVotes) => api.mockImplementation(async (_path: string, opts?: { method?: string }) => (opts?.method ? {} : data));

beforeEach(() => api.mockReset());
afterEach(cleanup);

describe("the league vote", () => {
  it("shows every trade that went through with the count, and anyone can vote", async () => {
    serve({ reach: "league", trades: [trade(), trade({ key: "k2", on_espn: false, votes: { open: true, fair: 0, unfair: 0, mine: null } })] });
    show(<LeagueVotes />);
    expect(await screen.findByText("3 fair · 1 not fair")).toBeInTheDocument();
    expect(screen.getByText("No votes yet")).toBeInTheDocument();
    expect(screen.getByText("Happened on ESPN")).toBeInTheDocument();
    expect(screen.getByText(/Each app gets one vote/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Not fair" })[0]!);
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/leagues/L1/trades/votes/k1", { method: "PUT", body: { vote: "unfair" } }));
  });

  it("takes your vote back when you click it again", async () => {
    serve({ reach: "members", trades: [trade({ votes: { open: true, fair: 3, unfair: 1, mine: "fair" } })] });
    show(<LeagueVotes />);
    const fair = await screen.findByRole("button", { name: "Fair" });
    expect(fair).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/you voted fair/)).toBeInTheDocument();
    fireEvent.click(fair);
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/leagues/L1/trades/votes/k1", { method: "DELETE" }));
  });

  it("stays out of the way when there's nothing to vote on", async () => {
    serve({ reach: "league", trades: [] });
    show(<LeagueVotes />);
    await waitFor(() => expect(api).toHaveBeenCalled());
    expect(screen.queryByText("League vote")).toBeNull();
  });
});

function Sorted() {
  const rows = [{ name: "mid", fp: 30 }, { name: "top", fp: 44 }, { name: "none", fp: null }, { name: "low", fp: 12 }];
  const sorting = useSort<"fp">();
  return (
    <table>
      <thead><tr><SortTh dir={sorting.dir("fp")} onSort={() => sorting.toggle("fp")}>FP/G</SortTh></tr></thead>
      <tbody>{sorting.apply(rows, (r) => r.fp).map((r) => <tr key={r.name}><td>{r.name}</td></tr>)}</tbody>
    </table>
  );
}

describe("sorting a column", () => {
  it("goes highest first, then lowest first, then back", () => {
    render(<Sorted />);
    const order = () => screen.getAllByRole("cell").map((c) => c.textContent);
    const head = screen.getByRole("button", { name: /FP\/G/ });
    expect(order()).toEqual(["mid", "top", "none", "low"]);
    fireEvent.click(head);
    expect(order()).toEqual(["top", "mid", "low", "none"]);
    expect(screen.getByRole("columnheader")).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(head);
    expect(order()).toEqual(["low", "mid", "top", "none"]);
    expect(screen.getByRole("columnheader")).toHaveAttribute("aria-sort", "ascending");
    fireEvent.click(head);
    expect(order()).toEqual(["mid", "top", "none", "low"]);
  });
});
