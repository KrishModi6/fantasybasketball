import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { wsUrl } from "./api";
import { useToast } from "./toast";

/** Subscribe to a league's live events and refresh the affected queries. Reconnects with backoff. */
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
          case "override_changed":
          case "settings_changed":
            qc.invalidateQueries({ queryKey: key });
            if (ev.type === "sync_completed" && ev.reason !== "scheduled") toast("League data refreshed.");
            if (ev.type === "sync_completed" && ev.reason === "scheduled") toast("New stats and injury news are in.");
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
        retry = Math.min(retry + 1, 6);
        timer = setTimeout(connect, 1000 * 2 ** retry);
      };
    };
    connect();
    return () => {
      closed = true;
      clearInterval(ping);
      clearTimeout(timer);
      ws?.close();
    };
  }, [leagueId, qc, toast]);
}
