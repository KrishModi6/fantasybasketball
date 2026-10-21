// Small, dependency-free charts. Colour comes from the theme tokens: accent for the
// primary series, --series-b for the comparison series, up/down for signed values.

export const SERIES_A = "var(--accent)";
export const SERIES_B = "var(--series-b)";

export function Bar({ value, max, tick, label }: { value: number; max: number; tick?: number; label?: string }) {
  const w = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  return (
    <div className="bar" role="img" aria-label={label}>
      <span style={{ width: `${w * 100}%` }} />
      {tick != null && max > 0 && <i className="tick" style={{ left: `${Math.min(1, tick / max) * 100}%` }} />}
    </div>
  );
}

/** Centred bar: negative grows left (down colour), positive grows right (up colour). */
export function Diverging({ value, max, label }: { value: number; max: number; label?: string }) {
  const w = max > 0 ? Math.min(1, Math.abs(value) / max) : 0;
  return (
    <div className="diverge" role="img" aria-label={label}>
      <div className="l">{value < 0 && <span style={{ width: `${w * 100}%` }} />}</div>
      <div className="r">{value > 0 && <span style={{ width: `${w * 100}%` }} />}</div>
    </div>
  );
}

export function Sparkline({ values, width = 140, height = 32, label }: { values: number[]; width?: number; height?: number; label?: string }) {
  if (values.length < 2) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * (width - 4) + 2, height - 3 - ((v - lo) / span) * (height - 6)]);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x!.toFixed(1)},${y!.toFixed(1)}`).join(" ");
  return (
    <svg width={width} height={height} role="img" aria-label={label}>
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

/** Game-by-game fantasy points as columns, with the projection as a dashed reference line. */
export function GameColumns({ values, projection, height = 70 }: { values: number[]; projection: number; height?: number }) {
  if (!values.length) return null;
  const max = Math.max(projection * 1.6, ...values, 1);
  const w = 100 / values.length;
  const projY = height - (projection / max) * height;
  return (
    <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" width="100%" height={height} role="img"
      aria-label={`Last ${values.length} games in fantasy points; projection ${projection.toFixed(1)}`} style={{ display: "block" }}>
      {[0.25, 0.5, 0.75].map((f) => (
        <line key={f} x1="0" x2="100" y1={height * f} y2={height * f} stroke="var(--grid)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      ))}
      {values.map((v, i) => {
        const h = (Math.max(v, 0) / max) * height;
        return <rect key={i} x={i * w + w * 0.15} width={w * 0.7} y={height - h} height={h}
          fill={v >= projection ? "var(--accent)" : "var(--accent-dim)"} />;
      })}
      <line x1="0" x2="100" y1={projY} y2={projY} stroke="var(--series-b)" strokeWidth="1" strokeDasharray="3 2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Projected weekly score as mean ± 1 sd on a shared scale. */
export function RangeBar({ mean, sd, lo, hi }: { mean: number; sd: number; lo: number; hi: number }) {
  const span = hi - lo || 1;
  const x = (v: number) => `${Math.max(0, Math.min(100, ((v - lo) / span) * 100))}%`;
  return (
    <div className="range" aria-hidden="true">
      <span className="band" style={{ left: x(mean - sd), width: `calc(${x(mean + sd)} - ${x(mean - sd)})` }} />
      <span className="dot" style={{ left: `calc(${x(mean)} - 1px)` }} />
    </div>
  );
}

/** Overlaid histograms on shared bins, each with its mean marked. Axis labels are HTML so they stay legible on phones. */
export function Distribution({ bins, series, height = 150, label }: {
  bins: number[];
  series: { hist: number[]; color: string; mean: number; name: string }[];
  height?: number;
  label: string;
}) {
  const W = 600;
  const max = Math.max(1, ...series.flatMap((s) => s.hist));
  const lo = bins[0] ?? 0;
  const hi = bins[bins.length - 1] ?? 1;
  const x = (v: number) => ((v - lo) / (hi - lo || 1)) * W;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => lo + f * (hi - lo));
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img" aria-label={label} style={{ display: "block" }}>
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1="0" x2={W} y1={height * f} y2={height * f} stroke="var(--grid)" vectorEffect="non-scaling-stroke" />
        ))}
        {series.map((s) => {
          const pts = s.hist.map((c, i) => {
            const cx = (x(bins[i]!) + x(bins[i + 1]!)) / 2;
            return `${cx.toFixed(1)},${(height - (c / max) * (height - 6)).toFixed(1)}`;
          });
          const first = x(bins[0]!).toFixed(1);
          const last = x(bins[bins.length - 1]!).toFixed(1);
          return (
            <g key={s.name}>
              <polygon points={`${first},${height} ${pts.join(" ")} ${last},${height}`} fill={s.color} fillOpacity="0.14" />
              <polyline points={pts.join(" ")} fill="none" stroke={s.color} strokeWidth="1.5" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
              <line x1={x(s.mean)} x2={x(s.mean)} y1={2} y2={height} stroke={s.color} strokeWidth="1" strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />
            </g>
          );
        })}
        <line x1="0" x2={W} y1={height - 0.5} y2={height - 0.5} stroke="var(--border)" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="row between tiny muted num" aria-hidden="true" style={{ marginTop: 3 }}>
        {ticks.map((t, i) => <span key={i}>{Math.round(t)}</span>)}
      </div>
    </div>
  );
}

/** Winning-margin histogram: bins where team A wins in the A colour, B wins in the B colour. */
export function MarginChart({ bins, hist, height = 90, label }: { bins: number[]; hist: number[]; height?: number; label: string }) {
  const W = 600;
  const max = Math.max(1, ...hist);
  const lo = bins[0] ?? -1;
  const hi = bins[bins.length - 1] ?? 1;
  const x = (v: number) => ((v - lo) / (hi - lo || 1)) * W;
  const zero = lo < 0 && hi > 0;
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img" aria-label={label} style={{ display: "block" }}>
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1="0" x2={W} y1={height * f} y2={height * f} stroke="var(--grid)" vectorEffect="non-scaling-stroke" />
        ))}
        {hist.map((c, i) => {
          const x0 = x(bins[i]!);
          const x1 = x(bins[i + 1]!);
          const mid = (bins[i]! + bins[i + 1]!) / 2;
          const h = (c / max) * (height - 4);
          return <rect key={i} x={x0 + 0.5} width={Math.max(0, x1 - x0 - 1)} y={height - h} height={h}
            fill={mid >= 0 ? SERIES_A : SERIES_B} fillOpacity={0.85} />;
        })}
        {zero && <line x1={x(0)} x2={x(0)} y1={0} y2={height} stroke="var(--muted)" strokeWidth="1" vectorEffect="non-scaling-stroke" />}
      </svg>
      <div className="tiny muted num" aria-hidden="true" style={{ position: "relative", height: 16, marginTop: 3 }}>
        <span style={{ position: "absolute", left: 0 }}>{Math.round(lo)}</span>
        {zero && <span style={{ position: "absolute", left: `${(x(0) / W) * 100}%`, transform: "translateX(-50%)" }}>0</span>}
        <span style={{ position: "absolute", right: 0 }}>{hi > 0 ? "+" : ""}{Math.round(hi)}</span>
      </div>
    </div>
  );
}
