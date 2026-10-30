import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useLeagueCtx } from "../components/Layout";
import { Delta, ErrorBox, LoadingBlock, PageHead, Panel, PlayerName, Segmented, Term } from "../components/ui";
import { fmt0, fmt1 } from "../lib/format";
import type { TermKey } from "../lib/glossary";
import { usePlayers } from "../lib/queries";

const POSITIONS = ["", "PG", "SG", "SF", "PF", "C"];
type Status = "all" | "fa" | "rostered";

const COLUMNS: { key: string; label: string; term: TermKey; hideSm?: boolean }[] = [
  { key: "fp_pg", label: "FP/G", term: "fp_pg" },
  { key: "ros_value", label: "ROS", term: "ros" },
  { key: "vorp", label: "Over repl.", term: "over_repl", hideSm: true },
  { key: "games_next7", label: "Next 7", term: "next7" },
  { key: "mpg", label: "MPG", term: "mpg", hideSm: true },
  { key: "season_fp_pg", label: "Season", term: "season", hideSm: true },
  { key: "recent_fp_pg", label: "Last 10", term: "last10", hideSm: true },
  { key: "xfp_pg", label: "xFP/G", term: "xfp", hideSm: true },
  { key: "luck_pg", label: "Luck", term: "luck", hideSm: true },
  { key: "ownership_pct", label: "Rostered", term: "rostered", hideSm: true },
];

export default function PlayersPage() {
  const { league, teamName, openPlayer } = useLeagueCtx();
  // Filters live in the URL so Back (or reopening the app) returns to the same list.
  const [search, setSearch] = useSearchParams();
  const status = (search.get("status") as Status) || "fa";
  const pos = search.get("pos") ?? "";
  const sort = search.get("sort") || "ros_value";
  const setParam = (key: string, value: string, fallback: string) => {
    const next = new URLSearchParams(search);
    if (value === fallback) next.delete(key);
    else next.set(key, value);
    setSearch(next, { replace: true });
  };
  const setStatus = (v: Status) => setParam("status", v, "fa");
  const setPos = (v: string) => setParam("pos", v, "");
  const setSort = (v: string) => setParam("sort", v, "ros_value");
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
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
                    <th key={c.key} className={`n ${c.hideSm ? "hide-sm" : ""}`} aria-sort={sort === c.key ? "descending" : undefined}>
                      <Term t={c.term} onClick={() => setSort(c.key)}>{c.label}{sort === c.key ? " ▾" : ""}</Term>
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
                    <td className="n hide-sm">{fmt1(p.xfp_pg)}</td>
                    <td className="n hide-sm">{p.luck_pg != null ? <Delta value={p.luck_pg} /> : <span className="muted">–</span>}</td>
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
