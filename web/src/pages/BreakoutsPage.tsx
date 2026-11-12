import { useSearchParams } from "react-router-dom";
import { useLeagueCtx } from "../components/Layout";
import { Empty, ErrorBox, Kpi, Kpis, LoadingBlock, Notice, PageHead, Panel, PlayerName, Segmented, Tag, Term } from "../components/ui";
import { fmt1, pct } from "../lib/format";
import { useBreakouts } from "../lib/queries";
import type { BreakoutRow } from "../lib/types";

type Status = "all" | "fa" | "rostered";

/** The chance as a bar: scaled so the best candidates (about 60%) fill it, since nobody is near certain. */
function Chance({ value }: { value: number }) {
  return (
    <span className={`meter ${value >= 0.3 ? "hi" : ""}`}>
      <span className="track"><span style={{ width: `${Math.min(100, Math.round((value / 0.6) * 100))}%` }} /></span>
      <span className="num">{pct(value)}</span>
    </span>
  );
}

/** What moves his odds most, as the model reads it: the kinds of evidence, with which way each one pushes. */
function Reasons({ row }: { row: BreakoutRow }) {
  if (!row.reasons.length) return <span className="muted">–</span>;
  return (
    <span className="small">
      {row.reasons.map((r, i) => (
        <span key={r.factor} className={r.direction === "up" ? "" : "muted"}>
          {i > 0 && ", "}{r.direction === "up" ? "↑" : "↓"} {r.factor.toLowerCase()}
        </span>
      ))}
    </span>
  );
}

export default function BreakoutsPage() {
  const { league, teamName, openPlayer } = useLeagueCtx();
  const [search, setSearch] = useSearchParams();
  const status = (search.get("status") as Status) || "all";
  const setStatus = (v: Status) => {
    const next = new URLSearchParams(search);
    if (v === "all") next.delete("status");
    else next.set("status", v);
    setSearch(next, { replace: true });
  };
  const { data, error, isLoading } = useBreakouts(league.id, status);
  const model = data?.model;
  const def = model?.definition;
  const label = data ? `${data.season - 1}-${String(data.season).slice(2)}` : "";

  return (
    <>
      <PageHead title="Breakouts"
        desc="Players most likely to have a breakout season, from their minutes, usage, efficiency and development so far. The chance is set before the season; the season columns show how it's going." />
      <div className="toolbar">
        <Segmented label="Availability" value={status} onChange={setStatus}
          options={[{ value: "all", label: "All" }, { value: "fa", label: "Free agents" }, { value: "rostered", label: "Rostered" }]} />
        {data && <span className="small muted">{data.players.length} of {data.total}</span>}
      </div>
      {error && <ErrorBox error={error} />}
      {isLoading && <LoadingBlock rows={14} />}
      {data && !model && <Notice level="warn">This copy of Courtside has no breakout model. Update to the latest release.</Notice>}
      {data && model && def && (
        <>
          <Kpis label="Breakout model">
            <Kpi k={<Term t="breakout">A breakout season</Term>} v={`+${def.min_gain} FP/G`}
              s={`to ${def.min_level}+ FP/G over ${def.min_games}+ games`} />
            <Kpi k="How often it happens" v={pct(model.base_rate)} s={`of players with ${def.min_prior_games}+ games the season before`} />
            <Kpi k={<Term t="breakout_backtest">Backtest</Term>} v={`${fmt1(model.top_hits)} of ${model.top_n}`}
              s={`of its top ${model.top_n} broke out, ${model.backtest_seasons[0] - 1}-${String(model.backtest_seasons.at(-1)).slice(2)}`} />
            <Kpi k={`On pace in ${label}`} v={data.on_pace} tone={data.on_pace ? "up" : undefined} s="already past their breakout line" />
          </Kpis>
          {data.players.length === 0 ? (
            <Empty title="No candidates here">Nobody in this list played {def.min_prior_games} games last season.</Empty>
          ) : (
            <Panel flush>
              <div className="table-wrap" style={{ maxHeight: "calc(100vh - 330px)", overflowY: "auto" }}>
                <table className="data">
                  <thead>
                    <tr>
                      <th className="n">#</th>
                      <th>Player</th>
                      <th className="hide-sm">Team</th>
                      <th className="hide-sm">Age</th>
                      {status !== "fa" && <th className="hide-sm">Fantasy team</th>}
                      <th><Term t="breakout_chance">Chance</Term></th>
                      <th className="hide-sm">Why</th>
                      <th className="n"><Term t="breakout_last">Last season</Term></th>
                      <th className="n"><Term t="breakout_line">Line</Term></th>
                      <th className="n"><Term t="season">Season</Term></th>
                      <th className="n hide-sm">GP</th>
                      <th className="n hide-sm"><Term t="fp_pg">Projected</Term></th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.players.map((p) => (
                      <tr key={p.id} className="clickable" onClick={() => openPlayer(p.id)}>
                        <td className="n muted">{p.rank}</td>
                        <td>
                          <PlayerName name={p.name} injury={p.injury_status} onOpen={() => openPlayer(p.id)}
                            sub={p.on_waivers ? "waivers" : undefined} />
                          {p.on_pace && <> <Tag tone="up">on pace</Tag></>}
                        </td>
                        <td className="hide-sm muted">{p.pro_team}</td>
                        <td className="hide-sm muted">{p.age != null ? Math.floor(p.age) : "–"}</td>
                        {status !== "fa" && <td className="hide-sm">{p.fantasy_team_id ? teamName(p.fantasy_team_id) : <span className="muted">FA</span>}</td>}
                        <td><Chance value={p.probability} /></td>
                        <td className="hide-sm"><Reasons row={p} /></td>
                        <td className="n">{fmt1(p.last_fp_pg)}</td>
                        <td className="n muted">{fmt1(p.breakout_line)}</td>
                        <td className={`n ${p.on_pace ? "strong" : ""}`}>{fmt1(p.season_fp_pg)}</td>
                        <td className="n hide-sm muted">{p.games_played || "–"}</td>
                        <td className="n hide-sm">{fmt1(p.proj_fp_pg)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}
        </>
      )}
    </>
  );
}
