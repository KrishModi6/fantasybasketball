import { useEffect, useState } from "react";
import { isNewer, onOpenWhatsNew, parseReleaseNotes, type ReleaseNote } from "../lib/desktop";
import { rememberedPath } from "../lib/nav";
import { Markdown } from "./Markdown";
import { Dialog } from "./ui";

const SEEN_KEY = "cs.seen-version"; // the newest version this computer has opened
const RELEASES_URL = "https://github.com/KrishModi6/fantasybasketball/releases";

function readSeen(): string | null {
  try {
    return localStorage.getItem(SEEN_KEY);
  } catch {
    return null;
  }
}

/**
 * Desktop app only: the first time Courtside opens on a new version, shows what changed in every version
 * since the last one opened here. Help > What's new in Courtside, or the version in the status bar, shows
 * every release's notes. The notes are desktop/release-notes.md, the same text as the GitHub release.
 */
export function WhatsNew() {
  const desktop = window.courtside;
  const current = desktop?.version;
  // The notes to show at launch: those newer than `since`. null: this version's only, for someone who used
  // Courtside before it remembered versions. undefined: none (no update, or a brand-new copy).
  const [since] = useState<string | null | undefined>(() => {
    if (!current) return undefined;
    const seen = readSeen();
    if (seen) return isNewer(current, seen) ? seen : undefined;
    return rememberedPath() ? null : undefined;
  });
  const [view, setView] = useState<"update" | "all" | null>(since === undefined ? null : "update");
  const [notes, setNotes] = useState<ReleaseNote[] | null>(null);

  useEffect(() => {
    const seen = readSeen();
    if (!current || (seen && !isNewer(current, seen))) return;
    try {
      localStorage.setItem(SEEN_KEY, current);
    } catch {
      /* storage unavailable: the notes show again next launch */
    }
  }, [current]);

  useEffect(() => {
    const load = desktop?.releaseNotes;
    if (!load) return;
    let live = true;
    load().then(
      (md) => live && setNotes(parseReleaseNotes(md)),
      () => live && setNotes([]),
    );
    return () => {
      live = false;
    };
  }, [desktop]);

  useEffect(() => {
    const showAll = () => setView("all");
    const offMenu = desktop?.onWhatsNew?.(showAll);
    const offApp = onOpenWhatsNew(showAll);
    return () => {
      offMenu?.();
      offApp();
    };
  }, [desktop]);

  if (!current || !view || !notes) return null;
  const shown = view === "all"
    ? notes
    : notes.filter((n) => !isNewer(n.version, current) && (since ? isNewer(n.version, since) : n.version === current));
  if (view === "update" && !shown.length) return null;

  const close = () => setView(null);
  const headings = view === "all" || shown.length > 1;
  return (
    <Dialog title={view === "all" ? "What's new in Courtside" : `What's new in Courtside ${current}`} onClose={close}
      className="whats-new">
      {view === "update" && since && <p className="small muted whats-new-since">You updated from {since}. Here's everything since then.</p>}
      <div className="whats-new-body">
        {shown.map((n) => (
          <section key={n.version} aria-label={`Courtside ${n.version}`}>
            {headings && <h3 className="whats-new-version">{n.version}{n.version === current ? " (yours)" : ""}</h3>}
            <Markdown text={n.body} />
          </section>
        ))}
        {!shown.length && (
          <p className="small">
            Couldn't read the release notes. They're on <a href={RELEASES_URL} target="_blank" rel="noreferrer">GitHub</a>.
          </p>
        )}
      </div>
      <div className="row">
        <button className="btn primary" onClick={close}>Got it</button>
        {view === "update" && notes.length > shown.length && (
          <button className="btn ghost" onClick={() => setView("all")}>Earlier versions</button>
        )}
      </div>
    </Dialog>
  );
}
