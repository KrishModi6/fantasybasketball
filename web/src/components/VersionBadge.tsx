import { useEffect, useState } from "react";
import type { UpdateState } from "../lib/api";
import { relTime } from "../lib/format";

/** Desktop app only: the version you're on and whether it's the latest. Click to check now, or to get
 *  the new version when there is one. */
export function VersionBadge() {
  const desktop = window.courtside;
  const bridge = desktop?.updates;
  const [state, setState] = useState<UpdateState | null>(null);
  useEffect(() => {
    if (!bridge) return;
    void bridge.state().then(setState);
    return bridge.onChange(setState);
  }, [bridge]);
  if (!desktop?.version) return null;

  const status = state?.status ?? "idle";
  const newer = state?.version && ["available", "downloading", "ready"].includes(status) ? state.version : null;
  const checked = state?.checkedAt ? ` (checked ${relTime(state.checkedAt)})` : "";
  let label: string | null = null;
  let title = `You're on Courtside ${desktop.version}. Click to check for updates.`;
  if (status === "checking") label = "Checking…";
  else if (status === "current") {
    label = "Up to date";
    title = `You're on Courtside ${desktop.version}, the latest version${checked}. Click to check again.`;
  } else if (status === "error") {
    title = `You're on Courtside ${desktop.version}. The last update check failed${checked}. Click to try again.`;
  } else if (newer && status === "ready") {
    label = `${newer} ready`;
    title = `Courtside ${newer} is downloaded. Click to restart and install it.`;
  } else if (newer && status === "available") {
    label = `${newer} available`;
    title = `Courtside ${newer} is out (you have ${desktop.version}). Click to download it.`;
  } else if (newer) {
    label = `Downloading ${newer}`;
    title = `Downloading Courtside ${newer}…`;
  }
  const onClick = () => {
    if (!bridge) return;
    if (status === "ready") void bridge.install();
    else if (status === "available") void bridge.download();
    else if (status !== "downloading" && status !== "checking") void bridge.check();
  };
  const dot = newer ? "new" : status === "current" ? "" : status === "error" ? "err" : status === "checking" ? "run" : "idle";
  return (
    <button type="button" className={`btn ghost version-badge ${newer ? "new" : ""}`} onClick={onClick} title={title}
      aria-label={`Courtside version ${desktop.version}${label ? `, ${label}` : ""}`}>
      <span className={`dot ${dot}`} aria-hidden="true" />
      v{desktop.version}
      {label && <span className="hide-sm">· {label}</span>}
    </button>
  );
}
