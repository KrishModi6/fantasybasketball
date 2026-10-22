import { ErrorBox, LoadingBlock, Notice, PageHead, Panel } from "../components/ui";
import { fmt0, fmt1 } from "../lib/format";
import { useModelCard } from "../lib/queries";

const METHOD_LABEL: Record<string, string> = {
  naive: "Last season repeated",
  sps: "Statistical baseline (SPS)",
  espn: "ESPN preseason projection",
  ml: "LightGBM model alone",
  ensemble: "Courtside ensemble",
};

const TARGET_LABEL: Record<string, string> = {
  mpg: "Minutes", avail: "Games played", REB: "Rebounds", AST: "Assists", STL: "Steals", BLK: "Blocks", TO: "Turnovers",
  FGM: "FG made", FGMI: "FG missed", FTM: "FT made", FTMI: "FT missed", "3PM": "3s made", "3PMI": "3s missed",
  DD: "Double-doubles", TD: "Triple-doubles", PF: "Fouls",
};

const REFERENCES = [
  ["Basketball-Reference, Simple Projection System", "Weighted seasons (Courtside: last two at 6/2, SPS itself: three at 6/3/1), 1,000 minutes of league-average regression, aging curve.", "https://www.basketball-reference.com/about/projections.html"],
  ["T. Tango, Marcel the Monkey Forecasting System", "The weighted-history, regression and age template SPS is built on.", "https://www.tangotiger.net/archives/stud0346.shtml"],
  ["B. Efron & C. Morris (1975), JASA 70(350)", "Shrinkage toward a prior; basis for the in-season Gamma–Poisson update.", null],
  ["J. M. Bates & C. W. J. Granger (1969), Operational Research Quarterly 20(4)", "Combining forecasts.", null],
  ["D. H. Wolpert (1992), Neural Networks 5(2)", "Stacked generalization: blend weights from out-of-fold predictions.", null],
  ["G. Ke et al. (2017), NeurIPS", "LightGBM, the residual model's learner.", null],
  ["S. Lundberg et al. (2020), Nature Machine Intelligence 2", "TreeSHAP attributions shown for each player.", null],
  ["Z. Rosenof (2023), arXiv:2307.02188", "G-scores for category leagues.", "https://arxiv.org/abs/2307.02188"],
  ["H. W. Kuhn (1955); J. Edmonds (1971)", "Exact daily lineup optimisation.", null],
  ["J. Kubatko, D. Oliver, K. Pelton & D. Rosenbaum (2007), JQAS 3(3)", "Per-minute rates as the unit of box-score production.", null],
] as const;

type Row = { fppg_mae: number; fppg_rmse: number; total_mae: number; total_spearman: number };
const seasonLabel = (y: number) => `${y - 1}–${String(y).slice(2)}`;

function BacktestTable({ rows }: { rows: Record<string, Row> }) {
  const best = (k: keyof Row, lowIsGood = true) => {
    const vals = Object.values(rows).map((r) => r[k]);
    return lowIsGood ? Math.min(...vals) : Math.max(...vals);
  };
  const cell = (r: Row, k: keyof Row, digits: number, lowIsGood = true) => (
    <td className={`n ${r[k] === best(k, lowIsGood) ? "strong accent" : ""}`}>{digits ? r[k].toFixed(digits) : fmt0(r[k])}</td>
  );
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr><th>Method</th><th className="n">FP/G MAE</th><th className="n">FP/G RMSE</th><th className="n">Season FP MAE</th><th className="n">Rank correlation</th></tr>
        </thead>
        <tbody>
          {Object.entries(rows).map(([k, r]) => (
            <tr key={k}>
              <td className={k === "ensemble" ? "strong" : ""}>{METHOD_LABEL[k] ?? k}</td>
              {cell(r, "fppg_mae", 2)}
              {cell(r, "fppg_rmse", 2)}
              {cell(r, "total_mae", 0)}
              {cell(r, "total_spearman", 3, false)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Verdict({ s }: { s: Record<string, Row> }) {
  const e = s.ensemble;
  const x = s.espn;
  if (!e || !x) return null;
  const rel = (a: number, b: number) => `${Math.abs((1 - a / b) * 100).toFixed(0)}% ${a < b ? "lower" : "higher"}`;
  const rank = Math.abs(e.total_spearman - x.total_spearman) < 0.01 ? "about the same" : e.total_spearman > x.total_spearman ? "better" : "slightly worse";
  return (
    <p className="small" style={{ marginTop: 8 }}>
      Against ESPN, the ensemble's per-game error is {rel(e.fppg_mae, x.fppg_mae)} and its season-total error {rel(e.total_mae, x.total_mae)}; ranking
      accuracy is {rank} (ρ {e.total_spearman.toFixed(3)} vs {x.total_spearman.toFixed(3)}). The ML component is kept because of this measured improvement.
    </p>
  );
}

export default function MethodologyPage() {
  const { data, error, isLoading } = useModelCard();
  const s = data?.summary;
  return (
    <>
      <PageHead title="Methodology"
        desc="How projections, rankings, simulations and trade verdicts are calculated, and how well the projection model has performed out of sample." />
      {error && <ErrorBox error={error} />}
      {isLoading && <LoadingBlock rows={10} />}

      <Panel title="Pipeline">
        <ol className="steps">
          <li><strong>Player projections.</strong> Per-minute rates for 14 box-score stats, minutes and availability come from three sources: a statistical baseline (SPS: the last two seasons, 2024-25 onward for 2026-27, weighted 6/2, regressed toward league average, age-adjusted), ESPN's projection, and a LightGBM model that predicts corrections to the baseline. They are blended with weights fitted on out-of-sample seasons, then updated with this season's games (Gamma–Poisson shrinkage, stat by stat). Until this season has enough games, last season's final 10 also count, at a quarter of the weight.</li>
          <li><strong>Bounded adjustments.</strong> Injury status and return dates change games played only. Each NBA team's healthy players are scaled toward a 236-minute budget (±8%). New-team players get wider uncertainty. League overrides are capped at ±15% minutes; all minutes adjustments together at −20% / +15%. Headlines are never parsed into numbers.</li>
          <li><strong>Team simulation.</strong> Every remaining day, each roster's optimal legal lineup is solved exactly. Expected points and variance per week give the matchup odds (Normal approximation; Poisson-binomial over categories in category leagues).</li>
          <li><strong>Rankings and odds.</strong> Power score is the all-play win rate over remaining weeks. Playoff and title odds come from 5,000 simulated seasons of the real schedule and bracket.</li>
          <li><strong>Matchup simulator.</strong> A week played 10,000 times at the box-score level: availability, a per-game role shock calibrated to each player's game-to-game spread, Poisson counts for each stat, and bench fill-ins when a starter sits.</li>
          <li><strong>Trades and moves.</strong> Both rosters are re-simulated with the change applied, including dropping or picking up players to keep roster size legal. Verdict thresholds are ±1% of rest-of-season output. Add/drop moves are simulated against the four lowest-value players.</li>
        </ol>
      </Panel>

      {data?.trained && s && (
        <Panel title="Backtest" note={`Rolling origin: each season predicted using only earlier seasons, including blend weights. Scored in ${data.scoring}. Players with 20+ games and a genuine ESPN preseason projection, seasons ${(data.espn_report_seasons ?? []).map(seasonLabel).join(", ")}.`}>
          <BacktestTable rows={s} />
          <Verdict s={s} />
          {data.summary_all && (
            <details style={{ marginTop: 8 }}>
              <summary className="small" style={{ cursor: "pointer" }}>All players with 20+ games, every test season (no ESPN requirement)</summary>
              <div style={{ marginTop: 8 }}><BacktestTable rows={data.summary_all} /></div>
            </details>
          )}
          {data.data_audit && (
            <div style={{ marginTop: 8 }}>
              <Notice level="warn">
                Data audit: ESPN stores one projection per past season, and for {data.data_audit.excluded_espn_seasons.map(seasonLabel).join(" and ") || "no season"} it was updated
                during the season (rest-of-season games, errors far below every other year). Those projections are excluded from training and from this comparison.
                Draft position is not used as a feature: ESPN overwrites it for completed seasons, and it reflects market opinion rather than statistics.
              </Notice>
            </div>
          )}
        </Panel>
      )}

      {data?.trained && (
        <div className="grid cols-2" style={{ alignItems: "start" }}>
          <Panel title="Blend weights" flush note="Fitted per target on out-of-sample predictions.">
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Target</th><th className="n">SPS</th><th className="n">ESPN</th><th className="n">ML</th></tr></thead>
                <tbody>
                  {Object.entries(data.ensemble_weights ?? {}).map(([t, w]) => (
                    <tr key={t}>
                      <td>{TARGET_LABEL[t] ?? t}</td>
                      {["sps", "espn", "ml"].map((k) => <td key={k} className="n">{w[k] ? `${Math.round(w[k]! * 100)}%` : <span className="faint">–</span>}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          <Panel title="In-season updating" flush
            note="Minutes of evidence the preseason projection is worth before this season's data dominates, estimated from backtest errors.">
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Stat</th><th className="n">Young / low data</th><th className="n">Established</th><th className="n">Overdispersion</th></tr></thead>
                <tbody>
                  {Object.entries(data.prior_minutes ?? {}).map(([k, v]) => (
                    <tr key={k}>
                      <td>{TARGET_LABEL[k] ?? k}</td>
                      <td className="n">{fmt0(v.low)}</td>
                      <td className="n">{fmt0(v.high)}</td>
                      <td className="n">{fmt1(data.overdispersion?.[k])}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      )}

      <Panel title="References" flush>
        <div className="table-wrap">
          <table className="data">
            <tbody>
              {REFERENCES.map(([title, why, url]) => (
                <tr key={title}>
                  <td className="wrap">{url ? <a href={url} target="_blank" rel="noreferrer">{title}</a> : title}</td>
                  <td className="wrap muted">{why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
