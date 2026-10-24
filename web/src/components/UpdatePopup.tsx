import { useEffect, useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import type { UpdateState } from "../lib/api";

/**
 * Desktop app only: pops up as soon as a new version is out. Windows downloads it by itself and then
 * offers to restart into it; a Mac opens the download.
 */
export function UpdatePopup() {
  const bridge = window.courtside?.updates;
  const [state, setState] = useState<UpdateState | null>(null);
  // "Later" hides it until the update moves on (a finished download asks again) or the app restarts.
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!bridge) return;
    void bridge.state().then(setState);
    return bridge.onChange(setState);
  }, [bridge]);

  const status = state?.status;
  if (!bridge || !state?.version || (status !== "downloading" && status !== "ready" && status !== "available")) return null;
  if (dismissed === `${status}:${state.version}`) return null;

  const detail = status === "downloading"
    ? `Downloading it now${state.percent ? ` (${state.percent}%)` : ""}…`
    : status === "ready"
      ? "It's downloaded. Restart to update, or it installs the next time you close Courtside."
      : "Your leagues carry over when you install it.";
  return (
    <div className="update-popup" role="dialog" aria-labelledby="update-title" aria-describedby="update-detail">
      <strong id="update-title" className="update-popup-title">New update released</strong>
      <p id="update-detail" className="small">
        Courtside {state.version} is out{state.current ? ` (you have ${state.current})` : ""}. {detail}
      </p>
      {status === "downloading" && <div className="update-progress" aria-hidden="true"><span style={{ width: `${state.percent ?? 0}%` }} /></div>}
      <div className="row">
        {status === "available" && (
          <button className="btn primary" onClick={() => void bridge.download()}><Download /> Download now</button>
        )}
        {status === "ready" && (
          <button className="btn primary" disabled={busy} onClick={() => { setBusy(true); void bridge.install(); }}>
            <RefreshCw /> {busy ? "Restarting…" : "Update now"}
          </button>
        )}
        <button className="btn ghost" onClick={() => setDismissed(`${status}:${state.version}`)}>Later</button>
      </div>
    </div>
  );
}
