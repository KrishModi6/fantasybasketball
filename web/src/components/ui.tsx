import { useEffect, useRef, type ReactNode } from "react";
import { AlertTriangle, Inbox } from "lucide-react";
import { injuryShort, signed } from "../lib/format";

export function PageHead({ kicker, title, lede, actions }: { kicker?: ReactNode; title: ReactNode; lede?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-head">
      <div className="titles">
        {kicker && <div className="kicker accent">{kicker}</div>}
        <h1 className="h1">{title}</h1>
        {lede && <p className="lede">{lede}</p>}
      </div>
      {actions && <div className="row wrap">{actions}</div>}
    </header>
  );
}

export function Panel({ title, kicker, actions, children, flush, className = "" }: {
  title?: ReactNode;
  kicker?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <div className="panel-head">
          <div className="stack" style={{ gap: 2 }}>
            {kicker && <div className="kicker">{kicker}</div>}
            {title && <h2 className="h2">{title}</h2>}
          </div>
          {actions}
        </div>
      )}
      <div className={flush ? "panel-flush" : "panel-body"}>{children}</div>
    </section>
  );
}

export function Delta({ value, digits = 1, suffix = "", invert = false }: { value: number; digits?: number; suffix?: string; invert?: boolean }) {
  const v = invert ? -value : value;
  const cls = Math.abs(value) < 0.5 * 10 ** -digits ? "flat" : v > 0 ? "up" : "down";
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
    <abbr className={`inj ${mild ? "dtd" : ""}`} title={status.replace(/_/g, " ").toLowerCase()}>
      {short}
    </abbr>
  );
}

export function PlayerCell({ name, team, positions, injury, onOpen, sub }: {
  name: string;
  team?: string;
  positions?: string[];
  injury?: string;
  onOpen?: () => void;
  sub?: ReactNode;
}) {
  return (
    <div className="player-cell">
      <span>
        {onOpen ? (
          <button className="nm" onClick={onOpen}>
            {name}
          </button>
        ) : (
          <span className="nm">{name}</span>
        )}
        {injury && <Injury status={injury} />}
      </span>
      <span className="pos-tag">
        {team}
        {positions?.length ? ` · ${positions.join(", ")}` : ""}
        {sub ? <> · {sub}</> : null}
      </span>
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="row" role="status" aria-live="polite">
      <div className="spinner" />
      <span className="muted small">{label}…</span>
    </div>
  );
}

export function Skeleton({ h = 16, w = "100%", style }: { h?: number; w?: number | string; style?: React.CSSProperties }) {
  return <div className="skeleton" style={{ height: h, width: w, ...style }} aria-hidden="true" />;
}

export function LoadingBlock({ rows = 6 }: { rows?: number }) {
  return (
    <div className="stack" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} h={i === 0 ? 28 : 18} w={i === 0 ? "40%" : `${92 - ((i * 13) % 30)}%`} />
      ))}
    </div>
  );
}

export function Empty({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="empty">
      {icon ?? <Inbox />}
      <div className="strong" style={{ color: "var(--ink)" }}>
        {title}
      </div>
      {children && <div className="small" style={{ maxWidth: 420 }}>{children}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="callout neg row" role="alert" style={{ alignItems: "flex-start" }}>
      <AlertTriangle size={17} style={{ flex: "none", marginTop: 2, color: "var(--neg)" }} />
      <span>{msg}</span>
    </div>
  );
}

export function Segmented<T extends string>({ value, onChange, options, label }: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode }[];
  label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} aria-pressed={value === o.value} onClick={() => onChange(o.value)} type="button">
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, tabs, label }: {
  value: T;
  onChange: (v: T) => void;
  tabs: { value: T; label: ReactNode }[];
  label: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
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
      <span className="track">
        <span style={{ width: `${Math.round(value * 100)}%` }} />
      </span>
      <span className="small num">{Math.round(value * 100)}%</span>
    </span>
  );
}

/** Modal dialog with focus trap-lite and Escape to close. */
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
        <h2 className="h2" style={{ marginBottom: 14 }}>
          {title}
        </h2>
        {children}
      </div>
    </>
  );
}
