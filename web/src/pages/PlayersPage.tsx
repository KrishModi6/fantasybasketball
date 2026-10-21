import { useEffect, useState } from "react";
import { ArrowDown } from "lucide-react";
import { useLeagueCtx } from "../components/Layout";
import { Delta, ErrorBox, LoadingBlock, PageHead, PlayerCell, Segmented } from "../components/ui";
import { fmt0, fmt1 } from "../lib/format";
import { usePlayers } from "../lib/queries";

const POSITIONS = ["PG", "SG", "SF", "PF", "C"];
type Status = "all" | "fa" | "rostered";

const COLUMNS: { key: string; label: string; title: string; hideSm?: boolean }[] = [
  { key: "fp_pg", label: "FP/G", title: "Projected fantasy points per game" },
  { key: "ros_value", label: "ROS", title: "Rest-of-season value: per-game value × expected games" },
  { key: "vorp", label: "Over repl.", title: "Rest-of-season value above the best available free agents", hideSm: true },
  { key: "games_next7", label: "Next 7", title: "Expected games in the next 7 days" },
  { key: "mpg", label: "MPG", title: "Projected minutes per game", hideSm: true },
  { key: "season_fp_pg", label: "Season", title: "Fantasy points per game this season", hideSm: true },
  { key: "recent_fp_pg", label: "Last 10", title: "Average of the last 10 games", hideSm: true },
  { key: "ownership_pct", label: "ESPN %", title: "Rostered in ESPN leagues", hideSm: true },
];

export default function PlayersPage() {
  const { league, teamName, openPlayer } = useLeagueCtx();
  const [status, setStatus] = useState<Status>("fa");
  const [pos, setPos] = useState<string>("");
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [sort, setSort] = useState("ros_value");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 220);
    return () => clearTimeout(t);
  }, [q]);
  const { data, error, isLoading, isFetching } = usePlayers(league.id, { status, pos, q: debounced, sort, limit: 150 });
  const isPoints = league.settings?.scoring.kind !== "categories";
  const cats = league.settings?.scoring.categories ?? [];

  return (
    <>
      <PageHead
        kicker="Player projections"
        title="Players"
        lede="Every number is a projection for the rest of the season, built from stats first. Open a player to see exactly how it was calculated."
      />
      <div className="row wrap between" style={{ marginBottom: 14, gap: 12 }}>
        <div className="row wrap" style={{ gap: 10 }}>
          <Segmented label="Availability" value={status} onChange={setStatus}
            options={[{ value: "fa", label: "Free agents" }, { value: "rostered", label: "Rostered" }, { value: "all", label: "All" }]} />
          <div className="chips" role="group" aria-label="Position">
            <button className="chip" aria-pressed={pos === ""} onClick={() => setPos("")}>All</button>
            {POSITIONS.map((p) => <button key={p} className="chip" aria-pressed={pos === p} onClick={() => setPos(pos === p ? "" : p)}>{p}</button>)}
          </div>
        </div>
        <input className="input search" style={{ width: 240 }} placeholder="Search players" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search players" />
      </div>
      {error && <ErrorBox error={error} />}
      {isLoading && <LoadingBlock rows={12} />}
      {data && (
        <section className="panel" aria-busy={isFetching}>
          <div className="table-wrap" style={{ maxHeight: "calc(100vh - 260px)", overflowY: "auto" }}>
            <table className="data">
              <caption className="sr-only">Players sorted by {sort}</caption>
              <thead>
                <tr>
                  <th className="n">#</th>
                  <th>Player</th>
                  {status !== "fa" && <th className="hide-sm">Fantasy team</th>}
                  {COLUMNS.map((c) => (
                    <th key={c.key} className={`n ${c.hideSm ? "hide-sm" : ""}`} aria-sort={sort === c.key ? "descending" : undefined} title={c.title}>
                      <button onClick={() => setSort(c.key)}>
                        {c.label}
                        {sort === c.key && <ArrowDown size={11} aria-hidden="true" />}
                      </button>
                    </th>
                  ))}
                  {!isPoints && cats.map((c) => <th key={c} className="n hide-sm">{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.players.map((p) => (
                  <tr key={p.id} className="clickable" onClick={() => openPlayer(p.id)}>
                    <td className="n muted">{p.rank}</td>
                    <td className="player">
                      <PlayerCell name={p.name} team={p.pro_team} positions={p.positions} injury={p.injury_status}
                        onOpen={() => openPlayer(p.id)} sub={p.on_waivers ? "waivers" : p.flags.includes("new_team") ? "new team" : undefined} />
                    </td>
                    {status !== "fa" && <td className="hide-sm small">{p.fantasy_team_id ? teamName(p.fantasy_team_id) : <span className="muted">FA</span>}</td>}
                    <td className="n strong">{fmt1(p.fp_pg)}</td>
                    <td className="n">{fmt0(p.ros_value)}</td>
                    <td className="n hide-sm"><Delta value={p.vorp} digits={0} /></td>
                    <td className="n">{fmt1(p.games_next7)}</td>
                    <td className="n hide-sm">{fmt1(p.mpg)}</td>
                    <td className="n hide-sm">{fmt1(p.season_fp_pg)}</td>
                    <td className="n hide-sm">{fmt1(p.recent_fp_pg)}</td>
                    <td className="n hide-sm">{fmt1(p.ownership_pct)}</td>
                    {!isPoints && cats.map((c) => <td key={c} className="n hide-sm"><Delta value={p.gscores?.[c] ?? 0} digits={2} /></td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row between small muted" style={{ padding: "10px 16px" }}>
            <span>Showing {data.players.length} of {data.total}</span>
            {isFetching && <span>Updating…</span>}
          </div>
        </section>
      )}
    </>
  );
}
