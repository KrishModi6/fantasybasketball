import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { count, relTime } from "../lib/format";
import { useToast } from "../lib/toast";
import type { RulingCall, TradeVotes, VoteTrade } from "../lib/types";
import { useLeagueCtx } from "./Layout";
import { NumbersTable, RULING_WORDS, RulingTag, UndoNote } from "./Rulings";
import { Panel, Tag } from "./ui";

const REACH: Record<TradeVotes["reach"], string> = {
  league: "Everyone in the league with Courtside sees the trade and the count. Each app gets one vote.",
  members: "Every member of this league sees the trade and the count, and gets one vote.",
  this_copy: "Votes stay on this computer: this league can't reach your friends' apps.",
};

function Status({ t }: { t: VoteTrade }) {
  return <Tag tone="up">{t.on_espn ? "Happened on ESPN" : "Happened"}</Tag>;
}

/** The league's count, in words: "3 fair · 1 not fair". */
export function tally(v: VoteTrade["votes"]): string {
  return v.fair + v.unfair === 0 ? "No votes yet" : `${v.fair} fair · ${v.unfair} not fair`;
}

/** The trades that went through, for everyone to vote on. A vote is an opinion and changes none of Courtside's numbers. */
export function LeagueVotes() {
  const { league } = useLeagueCtx();
  const qc = useQueryClient();
  const toast = useToast();
  const key = ["league", league.id, "votes"];
  const { data } = useQuery({ queryKey: key, queryFn: () => api<TradeVotes>(`/api/leagues/${league.id}/trades/votes`) });
  const cast = useMutation({
    mutationFn: ({ t, vote }: { t: VoteTrade; vote: RulingCall }) => {
      const url = `/api/leagues/${league.id}/trades/votes/${t.key}`;
      return t.votes.mine === vote ? api(url, { method: "DELETE" }) : api(url, { method: "PUT", body: { vote } });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
    onError: (e: Error) => toast(e.message, "err"),
  });
  if (!data?.trades?.length) return null;
  return (
    <Panel title="League vote" note={`Was it fair? Vote on every trade that went through. ${REACH[data.reach]} Votes don't change Courtside's numbers, and they can't block or undo anything on ESPN.`}>
      <ul className="list">
        {data.trades.map((t) => (
          <li key={t.key}>
            <div className="row between">
              <span><Status t={t} /> {t.ruling && <><RulingTag ruling={t.ruling.ruling} />{" "}</>}{t.trade.text}</span>
              <span className="small muted">{relTime(t.at)}</span>
            </div>
            <div className="row wrap" style={{ marginTop: 6 }}>
              {(["fair", "unfair"] as const).map((v) => (
                <button key={v} className={`btn ${t.votes.mine === v ? "primary" : ""}`} aria-pressed={t.votes.mine === v}
                  disabled={cast.isPending} onClick={() => cast.mutate({ t, vote: v })}>
                  {RULING_WORDS[v]}
                </button>
              ))}
              <span className="small" aria-label={`League vote: ${tally(t.votes)}`}>
                <strong>{tally(t.votes)}</strong>
                {t.votes.mine && <span className="muted"> · you voted {RULING_WORDS[t.votes.mine].toLowerCase()} (click it again to take it back)</span>}
              </span>
            </div>
            {t.numbers && (
              <details className="small">
                <summary>Courtside's numbers ({count(t.numbers.sides.length, "team")})</summary>
                {t.numbers.completed && <UndoNote />}
                <NumbersTable numbers={t.numbers} />
                <p className="small muted">{t.numbers.fairness.label && <strong>{t.numbers.fairness.label}. </strong>}{t.numbers.summary}</p>
              </details>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
