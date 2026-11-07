import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLeagueCtx } from "../components/Layout";
import { AdjustedNote, NumbersTable, RULING_WORDS, RulingTag, UndoNote } from "../components/Rulings";
import { Empty, ErrorBox, LoadingBlock, Notice, PageHead, Panel, Tag, Term } from "../components/ui";
import { api } from "../lib/api";
import { relTime } from "../lib/format";
import { useToast } from "../lib/toast";
import type { ReviewTrade, Ruling, RulingCall, TradeReview } from "../lib/types";

const REACH: Record<TradeReview["reach"], string> = {
  league: "Your rulings show up in everyone's Courtside app, under Trades > League board.",
  members: "Your rulings show up for everyone in this league, under Trades > League board.",
  this_copy: "Your rulings stay on this computer, since this copy of Courtside can't reach the rest of the league.",
};

function StatusTag({ t }: { t: ReviewTrade }) {
  if (t.status === "done") return <Tag tone="up">{t.on_espn ? "Happened on ESPN" : "Happened"}</Tag>;
  if (t.status === "stale") return <Tag tone="warn">{t.on_espn ? "Happened on ESPN, players moved since" : "No longer possible"}</Tag>;
  if (t.status === "gone") return <Tag>No longer listed</Tag>;
  return <Tag>Proposed</Tag>;
}

function TradeCard({ t, reach }: { t: ReviewTrade; reach: TradeReview["reach"] }) {
  const { league } = useLeagueCtx();
  const qc = useQueryClient();
  const toast = useToast();
  const [note, setNote] = useState(t.ruling?.note ?? "");
  const path = `/api/leagues/${league.id}/trades/rulings/${t.key}`;
  const refresh = () => {
    for (const part of ["review", "rulings", "board", "activity"]) qc.invalidateQueries({ queryKey: ["league", league.id, part] });
  };
  const failed = (e: Error) => {
    toast(e.message, "err");
    qc.invalidateQueries({ queryKey: ["league", league.id, "meta"] }); // refused because another copy is the admin: the tab goes
  };
  const rule = useMutation({
    mutationFn: (ruling: RulingCall) => api<Ruling>(path, { method: "PUT", body: { ruling, note: note.trim() } }),
    onSuccess: () => {
      toast("Ruling saved.");
      refresh();
    },
    onError: failed,
  });
  const withdraw = useMutation({
    mutationFn: () => api(path, { method: "DELETE" }),
    onSuccess: () => {
      toast("Ruling withdrawn.");
      refresh();
    },
    onError: failed,
  });
  const busy = rule.isPending || withdraw.isPending;
  // A trade that's gone from the board and the feed has only what the ruling kept of it.
  const numbers = t.numbers ?? t.ruling?.numbers ?? null;
  return (
    <Panel>
      <div className="stack">
        <div className="row wrap between">
          <strong>{t.trade.text}</strong>
          <span className="row">{t.ruling && <RulingTag ruling={t.ruling.ruling} />}<StatusTag t={t} /></span>
        </div>
        <p className="small muted">
          {relTime(t.at)}
          {t.post && ` · posted by ${t.post.author}`}
          {t.post?.note && ` · ${t.post.note}`}
        </p>
        {t.status_note && <p className="small muted">{t.status_note}</p>}
        {numbers ? (
          <>
            <p className="small">
              Courtside says: <strong><Term t="fit">{numbers.fairness.label || "no label"}</Term>.</strong> {numbers.fairness.reason} {numbers.summary}
            </p>
            {numbers.completed && <UndoNote />}
            {numbers.adjusted && <AdjustedNote mine />}
            {!t.numbers &&<p className="small muted">These are Courtside's numbers from when you ruled.</p>}
            <NumbersTable numbers={numbers} />
          </>
        ) : (
          <p className="small muted">Courtside has no numbers for this trade.</p>
        )}
        <div className="row wrap">
          <input className="input wide" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500}
            placeholder="Why (optional)" aria-label={`Your note on ${t.trade.text}`} />
          {(["fair", "unfair"] as const).map((call) => (
            <button key={call} className={`btn ${t.ruling?.ruling === call ? "primary" : ""}`} aria-pressed={t.ruling?.ruling === call}
              disabled={busy} onClick={() => rule.mutate(call)}>
              {RULING_WORDS[call]}
            </button>
          ))}
          {t.ruling && <button className="btn danger" disabled={busy} onClick={() => withdraw.mutate()}>Withdraw</button>}
        </div>
        {t.ruling?.pending && reach === "league" && (
          <Notice level="warn">Saved on this computer. It goes out to the rest of the league as soon as Courtside can reach it.</Notice>
        )}
      </div>
    </Panel>
  );
}

function Review() {
  const { league } = useLeagueCtx();
  const { data, isLoading, error } = useQuery({
    queryKey: ["league", league.id, "review"],
    queryFn: () => api<TradeReview>(`/api/leagues/${league.id}/trades/review`),
  });
  const where = data?.reach === "league"
    ? "Real trades show up here once they go through on ESPN. Trades your friends post on their own apps don't reach this list, so to rule on any other trade, analyze it under Trades and post it to the board."
    : "Real trades show up here once a refresh finds them. To rule on any other trade, analyze it under Trades and post it to the board.";
  const open = data?.trades.filter((t) => !t.ruling) ?? [];
  const ruled = data?.trades.filter((t) => t.ruling) ?? [];
  return (
    <div className="stack">
      <PageHead title="Trade rulings" desc="Your call on whether each trade is fair, with Courtside's numbers beside it." />
      {isLoading && <LoadingBlock rows={8} />}
      {error && <ErrorBox error={error} />}
      {data && (
        <>
          <Notice>
            {REACH[data.reach]} A ruling is only a label: it doesn't change Courtside's numbers, and it can't block or undo anything on ESPN, so a real veto still happens on ESPN.
          </Notice>
          {!data.trades.length && <Panel><Empty title="No trades to rule on yet">{where}</Empty></Panel>}
          {open.length > 0 && <h2 className="section">Needs a ruling</h2>}
          {open.map((t) => <TradeCard key={t.key} t={t} reach={data.reach} />)}
          {ruled.length > 0 && <h2 className="section">Ruled</h2>}
          {ruled.map((t) => <TradeCard key={t.key} t={t} reach={data.reach} />)}
          {data.trades.length > 0 && <p className="small muted">{where}</p>}
        </>
      )}
    </div>
  );
}

export default function AdminPage() {
  const { league } = useLeagueCtx();
  // The server refuses everyone else anyway; this is for someone who ends up on the address.
  if (!league.me?.is_admin) {
    return (
      <div className="stack">
        <PageHead title="Trade rulings" />
        <Notice level="warn">Only the league's admin can rule on trades. Any rulings show up for you under Trades &gt; League board.</Notice>
      </div>
    );
  }
  return <Review />;
}
