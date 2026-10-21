import { useEffect, useState } from "react";
import { useLeagueCtx } from "../components/Layout";
import { Delta, ErrorBox, LoadingBlock, PageHead, Panel, PlayerName, Segmented } from "../components/ui";
import { fmt0, fmt1 } from "../lib/format";
import { usePlayers } from "../lib/queries";

const POSITIONS = ["", "PG", "SG", "SF", "PF", "C"];
type Status = "all" | "fa" | "rostered";

const COLUMNS: { key: string; label: string; title: string; hideSm?: boolean }[] = [
  { key: "fp_pg", label: "FP/G", title: "Projected fantasy points per game" },
  { key: "ros_value", label: "ROS", title: "Rest-of-season value: per-game value × expected games" },
  { key: "vorp", label: "Over repl.", title: "Rest-of-season value above the best available free agents", hideSm: true },
  { key: "games_next7", label: "Next 7", title: "Expected games in the next 7 days" },
  { key: "mpg", label: "MPG", title: "Projected minutes per game", hideSm: true },
  { key: "season_fp_pg", label: "Season", title: "Fantasy points per game this season", hideSm: true },
  { key: "recent_fp_pg", label: "Last 10", title: "Average over the last 10 games", hideSm: true },
  { key: "ownership_pct", label: "Rostered", title: "Share of ESPN leagues rostering the player", hideSm: true },
];

export default function PlayersPage() {
  const { league, teamName, openPlayer } = useLeagueCtx();
  const [status, setStatus] = useState<Status>("fa");
  const [pos, setPos] = useState("");
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [sort, setSort] = useState("ros_value");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 220);
    return () => clearTimeout(t);
  }, [q]);
  const { data, error, isLoading } = usePlayers(league.id, { status, pos, q: debounced, sort, limit: 200 });
  const isPoints = league.settings?.scoring.kind !== "categories";
  const cats = league.settings?.scoring.categories ?? [];

  return (
    <>
      <PageHead title="Players" desc="Rest-of-season projections. Select a player to see how the projection is built." />
      <div className="toolbar">
        <Segmented label="Availability" value={status} onChange={setStatus}
          options={[{ value: "fa", label: "Free agents" }, { value: "rostered", label: "Rostered" }, { value: "all", label: "All" }]} />
        <label className="field-inline"><span className="label">Position</span>
          <select className="select" value={pos} onChange={(e) => setPos(e.target.value)}>
            {POSITIONS.map((p) => <option key={p} value={p}>{p || "Any"}</option>)}
          </select>
        </label>
        <input className="input" style={{ width: 220 }} placeholder="Search name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search players" />
        {data && <span className="small muted">{data.players.length} of {data.total}</span>}
      </div>
      {error && <ErrorBox error={error} />}
      {isLoading && <LoadingBlock rows={14} />}
      {data && (
        <Panel flush>
          <div className="table-wrap" style={{ maxHeight: "calc(100vh - 230px)", overflowY: "auto" }}>
            <table className="data">
              <thead>
                <tr>
                  <th className="n">#</th>
                  <th>Player</th>
                  <th className="hide-sm">Team</th>
                  <th className="hide-sm">Pos</th>
                  {status !== "fa" && <th className="hide-sm">Fantasy team</th>}
                  {COLUMNS.map((c) => (
                    <th key={c.key} className={`n ${c.hideSm ? "hide-sm" : ""}`} aria-sort={sort === c.key ? "descending" : undefined} title={c.title}>
                      <button onClick={() => setSort(c.key)}>{c.label}{sort === c.key ? " ▾" : ""}</button>
                    </th>
                  ))}
                  {!isPoints && cats.map((c) => <th key={c} className="n hide-sm">{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.players.map((p) => (
                  <tr key={p.id} className="clickable" onClick={() => openPlayer(p.id)}>
                    <td className="n muted">{p.rank}</td>
                    <td><PlayerName name={p.name} injury={p.injury_status} onOpen={() => openPlayer(p.id)}
                      sub={p.on_waivers ? "waivers" : p.flags.includes("new_team") ? "new team" : undefined} /></td>
                    <td className="hide-sm muted">{p.pro_team}</td>
                    <td className="hide-sm muted">{p.positions.join(", ")}</td>
                    {status !== "fa" && <td className="hide-sm">{p.fantasy_team_id ? teamName(p.fantasy_team_id) : <span className="muted">FA</span>}</td>}
                    <td className="n strong">{fmt1(p.fp_pg)}</td>
                    <td className="n">{fmt0(p.ros_value)}</td>
                    <td className="n hide-sm"><Delta value={p.vorp} digits={0} /></td>
                    <td className="n">{fmt1(p.games_next7)}</td>
                    <td className="n hide-sm">{fmt1(p.mpg)}</td>
                    <td className="n hide-sm">{fmt1(p.season_fp_pg)}</td>
                    <td className="n hide-sm">{fmt1(p.recent_fp_pg)}</td>
                    <td className="n hide-sm muted">{p.ownership_pct != null ? `${fmt1(p.ownership_pct)}%` : "–"}</td>
                    {!isPoints && cats.map((c) => <td key={c} className="n hide-sm"><Delta value={p.gscores?.[c] ?? 0} digits={2} /></td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  );
}
