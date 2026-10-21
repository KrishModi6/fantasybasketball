import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { wsUrl } from "./api";
import { useToast } from "./toast";

const POLL_MS = 60_000;

/**
 * When a league's data is re-synced (by anyone, or by the server's cron), refresh every query for it.
 * Keyed on last_synced_at so it works with or without the WebSocket.
 */
export function useRefreshOnSync(leagueId: string | undefined, lastSynced: string | null | undefined) {
  const qc = useQueryClient();
  const toast = useToast();
  const prev = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (!leagueId || lastSynced === undefined) return;
    if (prev.current !== undefined && lastSynced && lastSynced !== prev.current) {
      qc.invalidateQueries({ queryKey: ["league", leagueId], predicate: (q) => q.queryKey[2] !== "meta" });
      toast("League data refreshed.");
    }
    prev.current = lastSynced;
  }, [leagueId, lastSynced, qc, toast]);
}

/**
 * Subscribe to a league's live events and refresh the affected queries. Reconnects with backoff.
 * If the host has push disabled (close code 4403), poll the league status and feed instead.
 */
export function useLeagueSocket(leagueId: string | undefined) {
  const qc = useQueryClient();
  const toast = useToast();

  useEffect(() => {
    if (!leagueId) return;
    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 0;
    let ping: ReturnType<typeof setInterval> | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let poll: ReturnType<typeof setInterval> | undefined;

    const startPolling = () => {
      poll = setInterval(() => {
        if (document.visibilityState !== "visible") return;
        qc.invalidateQueries({ queryKey: ["league", leagueId, "meta"] });
        qc.invalidateQueries({ queryKey: ["league", leagueId, "activity"] });
        qc.invalidateQueries({ queryKey: ["league", leagueId, "board"] });
      }, POLL_MS);
    };

    const connect = () => {
      try {
        ws = new WebSocket(wsUrl(`/api/ws/leagues/${leagueId}`));
      } catch {
        return;
      }
      ws.onopen = () => {
        retry = 0;
        ping = setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send("ping"), 25000);
      };
      ws.onmessage = (e) => {
        let ev: { type: string; [k: string]: unknown };
        try {
          ev = JSON.parse(e.data);
        } catch {
          return;
        }
        const key = ["league", leagueId];
        switch (ev.type) {
          case "sync_started":
            qc.invalidateQueries({ queryKey: [...key, "meta"] });
            break;
          case "sync_completed":
            // The meta refetch moves last_synced_at; useRefreshOnSync refreshes the rest and notifies.
            qc.invalidateQueries({ queryKey: [...key, "meta"] });
            break;
          case "override_changed":
          case "settings_changed":
            qc.invalidateQueries({ queryKey: key });
            break;
          case "sync_failed":
            qc.invalidateQueries({ queryKey: [...key, "meta"] });
            toast(`Refresh failed: ${String(ev.error ?? "unknown error")}`, "err");
            break;
          case "trade_shared":
            qc.invalidateQueries({ queryKey: [...key, "board"] });
            qc.invalidateQueries({ queryKey: [...key, "activity"] });
            toast(`${String(ev.author)} shared a trade idea.`);
            break;
          case "trade_removed":
            qc.invalidateQueries({ queryKey: [...key, "board"] });
            break;
          case "member_joined":
            toast(`${String(ev.name)} joined the league.`);
            qc.invalidateQueries({ queryKey: key });
            break;
          case "member_updated":
            qc.invalidateQueries({ queryKey: [...key, "meta"] });
            qc.invalidateQueries({ queryKey: [...key, "members"] });
            break;
        }
      };
      ws.onclose = (e) => {
        clearInterval(ping);
        if (closed || e.code === 4401) return;
        if (e.code === 4403) {
          startPolling();
          return;
        }
        retry = Math.min(retry + 1, 6);
        timer = setTimeout(connect, 1000 * 2 ** retry);
      };
    };
    connect();
    return () => {
      closed = true;
      clearInterval(ping);
      clearTimeout(timer);
      clearInterval(poll);
      ws?.close();
    };
  }, [leagueId, qc, toast]);
}
