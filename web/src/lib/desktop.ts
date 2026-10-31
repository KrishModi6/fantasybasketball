// The desktop app's window and release notes. Everything here does nothing on the website.

/**
 * Tags the page with the platform, which the title bar's CSS depends on (the window has no system title
 * bar: each page's top bar is one), and keeps the window's buttons in the theme's colors.
 */
export function setupDesktopChrome() {
  const cs = window.courtside;
  if (!cs) return;
  const root = document.documentElement;
  if (cs.platform) root.dataset.platform = cs.platform;
  const setTheme = cs.setTheme;
  if (!setTheme) return;
  const sync = () => void setTheme(root.dataset.theme === "light" ? "light" : "dark");
  sync();
  new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
}

/** Opens the app menu under `el` (Windows and Linux, which have no menu bar). */
export function openAppMenu(el: HTMLElement) {
  const r = el.getBoundingClientRect();
  void window.courtside?.openMenu?.(r.left, r.bottom);
}

const WHATS_NEW = "courtside:whats-new";
/** Opens the What's new dialog with every release's notes. */
export const openWhatsNew = () => window.dispatchEvent(new Event(WHATS_NEW));
export const onOpenWhatsNew = (callback: () => void) => {
  window.addEventListener(WHATS_NEW, callback);
  return () => window.removeEventListener(WHATS_NEW, callback);
};

/** "0.3.10" > "0.3.9": compares versions number by number. */
export function isNewer(a: string, b: string) {
  const pa = a.split(".").map(Number);
  const pb = b.split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0);
  }
  return false;
}

export interface ReleaseNote {
  version: string;
  body: string; // Markdown
}

/** The "What's new in 0.5.3" and "In 0.5.2" sections of desktop/release-notes.md, newest first. */
export function parseReleaseNotes(md: string): ReleaseNote[] {
  const notes: ReleaseNote[] = [];
  let current: { version: string; lines: string[] } | null = null;
  const close = () => current && notes.push({ version: current.version, body: current.lines.join("\n").trim() });
  for (const line of md.split(/\r?\n/)) {
    if (line.startsWith("## ")) {
      close();
      const version = /^## (?:What's new in|In) (\d+(?:\.\d+)+)\s*$/.exec(line)?.[1];
      current = version ? { version, lines: [] } : null; // install guides and the like end the notes
    } else if (current) {
      current.lines.push(line);
    }
  }
  close();
  return notes.sort((a, b) => (isNewer(a.version, b.version) ? -1 : isNewer(b.version, a.version) ? 1 : 0));
}
