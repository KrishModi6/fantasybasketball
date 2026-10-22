import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, wsUrl } from "./api";
import { useToast } from "./toast";
import type { League } from "./types";

const POLL_MS = 60_000;
const STALE_MS = 30 * 60_000; // refresh league data older than this while someone is looking at it
const AUTO_RETRY_MS = 10 * 60_000;

/**
 * Refresh the league when it's opened, or looked at again, with data more than 30 minutes old, so stats
 * don't wait for the next scheduled refresh. Skipped after a failed refresh (the Refresh button retries).
 */
export function useAutoRefresh(league: League | undefined) {
  const qc = useQueryClient();
  const lastTry = useRef(0);
  const id = league?.id;
  const status = league?.sync.status;
  const synced = league?.sync.last_synced_at;
  useEffect(() => {
    if (!id) return;
    const check = () => {
      if (document.visibilityState !== "visible" || status !== "ok") return;
      if (Date.now() - (synced ? Date.parse(synced) : 0) < STALE_MS || Date.now() - lastTry.current < AUTO_RETRY_MS) return;
      lastTry.current = Date.now();
      api(`/api/leagues/${id}/sync`, { method: "POST" })
        .then(() => qc.invalidateQueries({ queryKey: ["league", id, "meta"] }))
        .catch(() => undefined); // e.g. someone else refreshed moments ago
    };
    check();
    const timer = setInterval(check, 5 * 60_000);
    document.addEventListener("visibilitychange", check);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", check);
    };
  }, [id, status, synced, qc]);
}

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
          case "roster_moves": {
            qc.invalidateQueries({ queryKey: [...key, "activity"] });
            qc.invalidateQueries({ queryKey: [...key, "board"] });
            const more = Number(ev.count) > 1 ? ` (+${Number(ev.count) - 1} more in the league feed)` : "";
            toast(`${String(ev.first)}${more}`);
            break;
          }
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
