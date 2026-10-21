import { useEffect, useRef, type ReactNode } from "react";
import { injuryShort, signed } from "../lib/format";

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
