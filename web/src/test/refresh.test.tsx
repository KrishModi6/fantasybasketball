import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAutoRefresh, useLeagueSocket } from "../lib/realtime";
import { ToastProvider } from "../lib/toast";
import type { League } from "../lib/types";

const api = vi.hoisted(() => vi.fn());
vi.mock("../lib/api", () => ({ api, wsUrl: (path: string) => `ws://engine${path}` }));

class FakeSocket {
  static OPEN = 1;
  static last: FakeSocket | null = null;
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  constructor(public url: string) {
    FakeSocket.last = this;
  }
  send() {}
  close() {}
  push(ev: object) {
    act(() => this.onmessage?.({ data: JSON.stringify(ev) }));
  }
}

function show(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ToastProvider>{ui}</ToastProvider></QueryClientProvider>);
}

function Socket() {
  useLeagueSocket("L1");
  return null;
}

function Auto({ league }: { league: League }) {
  useAutoRefresh(league);
  return null;
}

beforeEach(() => {
  api.mockReset();
  vi.stubGlobal("WebSocket", FakeSocket);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("a failed refresh", () => {
  const failed = (reason: string) => ({
    type: "sync_failed", reason, error: "Couldn't reach ESPN: it took too long to answer.",
    retry_at: new Date(Date.now() + 5 * 60_000).toISOString(),
  });

  it("pops up only for the Refresh button, saying when it's tried again", () => {
    show(<Socket />);
    FakeSocket.last!.push(failed("scheduled"));
    FakeSocket.last!.push(failed("auto"));
    expect(screen.queryByText(/Couldn't reach ESPN/)).toBeNull();

    FakeSocket.last!.push(failed("manual"));
    expect(screen.getByText("Couldn't reach ESPN: it took too long to answer. Courtside will try again in 5 min.")).toBeInTheDocument();
  });

  it("comes from a refresh the page asked for on its own, marked as automatic", () => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    api.mockResolvedValue({ status: "started" });
    const league = {
      id: "L1", settings: null,
      sync: { status: "ok", error: null, interval_hours: 1, last_synced_at: new Date(Date.now() - 2 * 3600_000).toISOString() },
    } as unknown as League;
    show(<Auto league={league} />);
    expect(api).toHaveBeenCalledWith("/api/leagues/L1/sync?auto=true", { method: "POST" });
  });
});
