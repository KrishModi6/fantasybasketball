import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { injuryShort, signed } from "../lib/format";
import { GLOSSARY, type TermKey } from "../lib/glossary";

const TIP_WIDTH = 280;

/** A label with a dotted underline that shows its plain-English definition (lib/glossary) on hover or focus.
 *  With onClick it is the button itself (a sortable column header). The tip is portalled so table scrollers can't clip it. */
export function Term({ t, children, onClick }: { t: TermKey; children?: ReactNode; onClick?: () => void }) {
  const entry = GLOSSARY[t];
  const id = useId();
  const ref = useRef<HTMLElement | null>(null);
  const [at, setAt] = useState<CSSProperties | null>(null);
  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - TIP_WIDTH - 8));
    setAt(r.bottom + 130 > window.innerHeight ? { left, bottom: window.innerHeight - r.top + 6 } : { left, top: r.bottom + 6 });
  };
  const hide = () => setAt(null);
  useEffect(() => {
    if (!at) return;
    window.addEventListener("scroll", hide, true);
    return () => window.removeEventListener("scroll", hide, true);
  }, [at]);
  const props = {
    className: "term", onMouseEnter: show, onMouseLeave: hide, onFocus: show, onBlur: hide,
    onKeyDown: (e: { key: string }) => e.key === "Escape" && hide(), "aria-describedby": at ? id : undefined,
  };
  const label = children ?? entry.term;
  return (
    <>
      {onClick
        ? <button type="button" ref={(el) => { ref.current = el; }} onClick={onClick} {...props}>{label}</button>
        : <span ref={(el) => { ref.current = el; }} tabIndex={0} {...props}>{label}</span>}
      {at && createPortal(
        <span role="tooltip" id={id} className="term-tip" style={{ ...at, maxWidth: TIP_WIDTH }}><strong>{entry.term}{entry.term.endsWith(".") ? "" : "."}</strong> {entry.def}</span>,
        document.body,
      )}
    </>
  );
}

export function PageHead({ title, desc, actions }: { title: ReactNode; desc?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {desc && <p>{desc}</p>}
      </div>
      {actions && <div className="toolbar">{actions}</div>}
    </header>
  );
}

export function Panel({ title, note, actions, children, flush, foot, className = "" }: {
  title?: ReactNode;
  note?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  foot?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${flush ? "flush" : ""} ${className}`}>
      {(title || actions) && (
        <div className="panel-head">
          {title ? <h2 className="section">{title}</h2> : <span />}
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      {note && <p className="panel-note">{note}</p>}
      {children}
      {foot && <p className="panel-foot">{foot}</p>}
    </section>
  );
}

export function Kpis({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <section className="kpis" aria-label={label}>
      {children}
    </section>
  );
}

export function Kpi({ k, v, s, tone }: { k: ReactNode; v: ReactNode; s?: ReactNode; tone?: "up" | "down" | "warn" | "accent" }) {
  return (
    <div className="kpi">
      <span className="k">{k}</span>
      <span className={`v ${tone ?? ""}`}>{v}</span>
      {s != null && <span className="s">{s}</span>}
    </div>
  );
}

export function Notice({ children, level = "info" }: { children: ReactNode; level?: "info" | "warn" | "error" }) {
  return (
    <div className={`notice ${level === "info" ? "" : level}`} role={level === "error" ? "alert" : undefined}>
      {children}
    </div>
  );
}

export function Delta({ value, digits = 1, suffix = "" }: { value: number; digits?: number; suffix?: string }) {
  const cls = Math.abs(value) < 0.5 * 10 ** -digits ? "zero" : value > 0 ? "pos" : "neg";
  return (
    <span className={`delta ${cls}`}>
      {signed(value, digits)}
      {suffix}
    </span>
  );
}

export function Injury({ status }: { status: string }) {
  if (!status || status === "ACTIVE") return null;
  const short = injuryShort[status] ?? status.slice(0, 3);
  const mild = status === "DAY_TO_DAY" || status === "QUESTIONABLE";
  return (
    <abbr className={`inj ${mild ? "mild" : ""}`} title={status.replace(/_/g, " ").toLowerCase()}>
      {short}
    </abbr>
  );
}

/** Player name (clickable to open the inspector) with injury flag and an optional muted suffix. */
export function PlayerName({ name, injury, onOpen, sub }: { name: string; injury?: string; onOpen?: () => void; sub?: ReactNode }) {
  return (
    <span>
      {onOpen ? <button className="linkbtn" onClick={onOpen}>{name}</button> : name}
      {injury && <Injury status={injury} />}
      {sub && <span className="faint small"> {sub}</span>}
    </span>
  );
}

export function LoadingBlock({ rows = 6 }: { rows?: number }) {
  return (
    <div className="panel" aria-busy="true" aria-label="Loading">
      <div className="stack" style={{ gap: 6 }}>
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="skeleton" style={{ height: 14, width: `${96 - ((i * 17) % 35)}%` }} />
        ))}
      </div>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <Notice level="error">{msg}</Notice>;
}

export function Segmented<T extends string>({ value, onChange, options, label }: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode }[];
  label: string;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} aria-pressed={value === o.value} onClick={() => onChange(o.value)} type="button">
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function SubTabs<T extends string>({ value, onChange, tabs, label }: {
  value: T;
  onChange: (v: T) => void;
  tabs: { value: T; label: ReactNode }[];
  label: string;
}) {
  return (
    <div className="subtabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button key={t.value} role="tab" aria-selected={value === t.value} onClick={() => onChange(t.value)} type="button">
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Meter({ value, label }: { value: number; label?: string }) {
  const cls = value >= 0.7 ? "hi" : value < 0.55 ? "lo" : "";
  return (
    <span className={`meter ${cls}`} title={label}>
      <span className="track"><span style={{ width: `${Math.round(value * 100)}%` }} /></span>
      <span className="num">{Math.round(value * 100)}%</span>
    </span>
  );
}

export function Tag({ children, tone }: { children: ReactNode; tone?: "up" | "down" | "warn" | "accent" }) {
  return <span className={`tag ${tone ?? ""}`}>{children}</span>;
}

/** Modal dialog with Escape to close and focus restore. */
export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input,select,textarea,button")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus();
    };
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <h2>{title}</h2>
        {children}
      </div>
    </>
  );
}
