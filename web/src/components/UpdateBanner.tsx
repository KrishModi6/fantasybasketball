import { useEffect, useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import type { UpdateState } from "../lib/api";

/** Desktop app only: a new version downloading, ready to install, or (on a Mac) ready to download. */
export function UpdateBanner() {
  const bridge = window.courtside?.updates;
  const [state, setState] = useState<UpdateState | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!bridge) return;
    void bridge.state().then(setState);
    return bridge.onChange(setState);
  }, [bridge]);

  if (!bridge || !state?.version || dismissed === `${state.status}:${state.version}`) return null;
  const later = (
    <button className="btn ghost" onClick={() => setDismissed(`${state.status}:${state.version}`)}>Later</button>
  );

  if (state.status === "downloading") {
    return (
      <div className="update-banner" role="status">
        <span>Downloading Courtside {state.version}… {state.percent ? `${state.percent}%` : ""}</span>
        {later}
      </div>
    );
  }
  if (state.status === "ready") {
    return (
      <div className="update-banner" role="status">
        <span>
          <strong>Courtside {state.version} is ready.</strong> Restart to update, or it installs the next time you close the app.
        </span>
        <button className="btn primary" disabled={busy} onClick={() => { setBusy(true); void bridge.install(); }}>
          <RefreshCw /> {busy ? "Restarting…" : "Restart now"}
        </button>
        {later}
      </div>
    );
  }
  if (state.status === "available") {
    return (
      <div className="update-banner" role="status">
        <span><strong>Courtside {state.version} is out.</strong> You have {state.current}. Your leagues carry over when you install it.</span>
        <button className="btn primary" onClick={() => void bridge.download()}><Download /> Download</button>
        {later}
      </div>
    );
  }
  return null;
}
