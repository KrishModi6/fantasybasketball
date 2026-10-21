import { Bar } from "../components/charts";
import { ErrorBox, LoadingBlock, PageHead, Panel } from "../components/ui";
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
  ["Basketball-Reference, “Simple Projection System”", "3-season 6/3/1 weighting, 1,000 minutes of league-average regression, aging curve.", "https://www.basketball-reference.com/about/projections.html"],
  ["T. Tango, “Marcel the Monkey Forecasting System”", "The weighted-history + regression + age template SPS is built on.", "https://www.tangotiger.net/archives/stud0346.shtml"],
  ["B. Efron & C. Morris (1975), “Data Analysis Using Stein's Estimator and Its Generalizations”, JASA 70(350)", "Shrinkage toward a prior; basis for the in-season Gamma–Poisson update.", null],
  ["J. M. Bates & C. W. J. Granger (1969), “The Combination of Forecasts”, Operational Research Quarterly 20(4)", "Why a weighted blend of imperfect forecasts beats any single one.", null],
  ["D. H. Wolpert (1992), “Stacked Generalization”, Neural Networks 5(2)", "Learning blend weights from out-of-fold predictions.", null],
  ["G. Ke et al. (2017), “LightGBM: A Highly Efficient Gradient Boosting Decision Tree”, NeurIPS", "The residual model's learner.", null],
  ["S. Lundberg et al. (2020), “From local explanations to global understanding with explainable AI for trees”, Nature Machine Intelligence 2", "TreeSHAP attributions shown on every player.", null],
  ["Z. Rosenof (2023), “Static quantification of player value for fantasy basketball”, arXiv:2307.02188", "G-scores for category leagues (Z-scores with week-to-week noise in the denominator).", "https://arxiv.org/abs/2307.02188"],
  ["H. W. Kuhn (1955), “The Hungarian Method for the Assignment Problem”; J. Edmonds (1971), “Matroids and the greedy algorithm”", "Exact daily lineup optimization.", null],
  ["J. Kubatko, D. Oliver, K. Pelton & D. Rosenbaum (2007), “A Starting Point for Analyzing Basketball Statistics”, JQAS 3(3)", "Per-minute rate framing of box-score production.", null],
] as const;

type Row = { fppg_mae: number; fppg_rmse: number; total_mae: number; total_spearman: number };
const seasonLabel = (y: number) => `${y - 1}–${String(y).slice(2)}`;

function BacktestTable({ rows }: { rows: Record<string, Row> }) {
  const best = Math.min(...Object.values(rows).map((m) => m.fppg_mae));
  const maxMae = Math.max(...Object.values(rows).map((m) => m.fppg_mae));
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr><th>Method</th><th style={{ width: "28%" }}>FP/G mean abs. error</th><th className="n">FP/G RMSE</th><th className="n">Season FP MAE</th><th className="n">Rank corr. (season FP)</th></tr>
        </thead>
        <tbody>
          {Object.entries(rows).map(([k, m]) => (
            <tr key={k} className={k === "ensemble" ? "mine" : ""}>
              <td className={k === "ensemble" ? "strong" : ""}>{METHOD_LABEL[k] ?? k}</td>
              <td>
                <div className="row"><div style={{ flex: 1 }}><Bar value={m.fppg_mae} max={maxMae} accent={m.fppg_mae === best} /></div><span className="num small strong">{m.fppg_mae.toFixed(2)}</span></div>
              </td>
              <td className="n">{m.fppg_rmse.toFixed(2)}</td>
              <td className="n">{fmt0(m.total_mae)}</td>
              <td className="n">{m.total_spearman.toFixed(3)}</td>
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
  return (
    <p className="small" style={{ marginBottom: 0 }}>
      <strong>What the numbers say:</strong> the ensemble's per-game error is {rel(e.fppg_mae, x.fppg_mae)} than ESPN's and its season-total error is{" "}
      {rel(e.total_mae, x.total_mae)}; ranking accuracy is {Math.abs(e.total_spearman - x.total_spearman) < 0.01 ? "about the same" : e.total_spearman > x.total_spearman ? "better" : "slightly worse"}{" "}
      (ρ {e.total_spearman.toFixed(3)} vs {x.total_spearman.toFixed(3)}). The ML model earns its place only through this measured improvement over the statistical and expert inputs.
    </p>
  );
}

export default function MethodologyPage() {
  const { data, error, isLoading } = useModelCard();
  const s = data?.summary;
  return (
    <>
      <PageHead kicker="How it works" title="Methodology"
        lede="Statistics drive every number. Machine learning is used only where the backtest shows it helps, and news enters only through bounded, labelled adjustments to minutes and games played." />
      {error && <ErrorBox error={error} />}
      {isLoading && <LoadingBlock rows={10} />}
      <div className="stack" style={{ gap: 22 }}>
        <div className="grid grid-3">
          <Panel kicker="Step 1" title="Project each player">
            <p className="small" style={{ margin: 0 }}>
              Per-minute rates for 14 box-score stats, plus minutes and availability, from three sources: a statistical baseline (SPS), ESPN's
              published projection, and a LightGBM model that learns corrections to the baseline. They're blended with weights fit on out-of-sample
              seasons, then updated with this season's games.
            </p>
          </Panel>
          <Panel kicker="Step 2" title="Simulate every day">
            <p className="small" style={{ margin: 0 }}>
              For each team and each remaining day, the optimal legal lineup is solved exactly from who plays, their positions and expected points. Summed,
              that gives weekly score distributions. Rankings and trades are built on those distributions, not on sums of player values.
            </p>
          </Panel>
          <Panel kicker="Step 3" title="Bound the news">
            <p className="small" style={{ margin: 0 }}>
              Injury status and return dates change games played only. A team's healthy players are scaled toward a 236-minute budget (±8% max). Team changes
              widen uncertainty. Manual overrides are capped at ±15% minutes. All adjustments together are capped at −20%/+15%.
            </p>
          </Panel>
        </div>

        {data?.trained && s && (
          <Panel title="Backtest" kicker={`Rolling-origin · ${data.scoring}`}>
            <p className="small muted" style={{ marginTop: 0 }}>
              Each season was predicted using only earlier seasons, including the blend weights. Compared on players with 20+ games and a genuine ESPN
              preseason projection, in {(data.espn_report_seasons ?? []).map(seasonLabel).join(", ")}. Lower error and higher rank correlation are better.
            </p>
            <BacktestTable rows={s} />
            <Verdict s={s} />
            {data.summary_all && (
              <details style={{ marginTop: 14 }}>
                <summary className="strong small" style={{ cursor: "pointer" }}>All players with 20+ games, every test season (no ESPN requirement)</summary>
                <div style={{ marginTop: 10 }}><BacktestTable rows={data.summary_all} /></div>
              </details>
            )}
            {data.data_audit && (
              <div className="callout neutral small" style={{ marginTop: 14 }}>
                <strong>Data audit.</strong> ESPN keeps one projection per past season, and for{" "}
                {data.data_audit.excluded_espn_seasons.map(seasonLabel).join(" and ") || "no seasons"} it was evidently refreshed in-season
                (rest-of-season games or errors far below every other year), so those are excluded from training and from this comparison.
                {data.data_audit.adp_missing_seasons.length > 0 && " Draft position (ADP) is not used as a feature: ESPN overwrites it for completed seasons, and it reflects market opinion rather than statistics."}
              </div>
            )}
          </Panel>
        )}

        {data?.trained && (
          <div className="grid grid-2" style={{ alignItems: "start" }}>
            <Panel title="Blend weights" kicker="Learned per target from out-of-sample predictions" flush>
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
            <Panel title="In-season updating" kicker="How many minutes of evidence the preseason projection is worth" flush>
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>Stat</th><th className="n">Young / low-data</th><th className="n">Established</th><th className="n">Overdispersion</th></tr></thead>
                  <tbody>
                    {Object.entries(data.prior_minutes ?? {}).map(([k, v]) => (
                      <tr key={k}>
                        <td>{TARGET_LABEL[k] ?? k}</td>
                        <td className="n">{fmt0(v.low)} min</td>
                        <td className="n">{fmt0(v.high)} min</td>
                        <td className="n">{fmt1(data.overdispersion?.[k])}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="tiny muted" style={{ padding: "8px 16px 12px", margin: 0 }}>
                Estimated from the ensemble's own backtest errors (Gamma–Poisson method of moments, sampling noise removed). Steals need ~3× the minutes that
                rebounds do before this season's data takes over.
              </p>
            </Panel>
          </div>
        )}

        <Panel title="Team strength, trades & suggestions">
          <ul className="small" style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 6 }}>
            <li><strong>Weekly score</strong> = sum over remaining days of the optimal lineup's expected points; variance adds each start's game-to-game variance and its play/no-play uncertainty. Bench players fill in for expected absences.</li>
            <li><strong>Power score</strong> = average probability of beating every other team in every remaining week (all-play), Normal approximation. Category leagues use per-category win probabilities and a Poisson-binomial count of categories won.</li>
            <li><strong>Playoff & title odds</strong>: 5,000 simulations of the real remaining schedule, seeding and bracket.</li>
            <li><strong>Matchup simulator</strong>: a single week played out 10,000 times at the box-score level: availability, a per-game role shock calibrated to each player's game-to-game spread, Poisson counts for every stat, and bench fill-ins when a starter sits. Gives win/tie odds, score and margin distributions, and each player's swing.</li>
            <li><strong>Trades</strong>: both rosters are re-simulated with the trade applied; 2-for-1s drop the weakest player or add the best free agent. Verdict thresholds are ±1% of rest-of-season output. The finder screens all 1–2 player packages and fully simulates the best ~90.</li>
            <li><strong>Add/drop</strong>: the top 30 free agents are simulated against your four lowest-value players; only lineup-adjusted gains are shown.</li>
          </ul>
        </Panel>

        <Panel title="References">
          <ol className="small" style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 8 }}>
            {REFERENCES.map(([title, why, url]) => (
              <li key={title}>
                {url ? <a href={url} target="_blank" rel="noreferrer">{title}</a> : title}
                <div className="muted">{why}</div>
              </li>
            ))}
          </ol>
        </Panel>
      </div>
    </>
  );
}
