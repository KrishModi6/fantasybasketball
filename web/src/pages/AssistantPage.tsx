import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useLeagueCtx } from "../components/Layout";
import { Markdown } from "../components/Markdown";
import { Empty, LoadingBlock, Notice, PageHead, Panel, Tag } from "../components/ui";
import { TOOL_LABELS } from "../lib/ai";
import { api, isDesktop } from "../lib/api";
import { useAiStatus } from "../lib/queries";
import type { AiAnswer } from "../lib/types";

const PRESETS = [
  { key: "waivers", title: "Waiver pickups", desc: "Best adds for the rest of the season and streams for this week, with who to drop." },
  { key: "matchup", title: "Matchup game plan", desc: "Your win probability, the players who swing it, and moves that raise your odds." },
  { key: "trades", title: "Trade ideas", desc: "Deals that help you and that the other manager could accept, with a pitch to send." },
  { key: "injuries", title: "Injury check", desc: "Who's hurt, what it costs you per week, and who on waivers covers it." },
  { key: "outlook", title: "Season outlook", desc: "Playoff and title odds, strengths and weaknesses, and the single best move." },
  { key: "recap", title: "Power rankings blurbs", desc: "A line of friendly trash talk for every team, built on the rankings." },
];

interface Turn {
  id: number;
  prompt: string;
  answer?: string;
  error?: string;
  tools?: AiAnswer["tools_used"];
  seconds?: number;
  responseId?: string;
}

const storeKey = (leagueId: string) => `cs.ai.${leagueId}`;
function loadTurns(leagueId: string): Turn[] {
  try {
    const raw = sessionStorage.getItem(storeKey(leagueId));
    return raw ? (JSON.parse(raw) as Turn[]).filter((t) => t.answer || t.error) : [];
  } catch {
    return [];
  }
}
function saveTurns(leagueId: string, turns: Turn[]) {
  try {
    sessionStorage.setItem(storeKey(leagueId), JSON.stringify(turns.slice(-20)));
  } catch {
    /* storage unavailable: the thread just won't survive a reload */
  }
}

function Elapsed() {
  const [s, setS] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setS((v) => v + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return <span className="mono">{s}s</span>;
}

export default function AssistantPage() {
  const { league, myTeamId, teamName } = useLeagueCtx();
  const status = useAiStatus();
  const [params, setParams] = useSearchParams();
  const [turns, setTurns] = useState<Turn[]>(() => loadTurns(league.id));
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [quota, setQuota] = useState<AiAnswer["quota"]>(null);
  const lastRef = useRef<HTMLElement>(null);
  const autoAsked = useRef(false);

  useEffect(() => {
    saveTurns(league.id, turns);
  }, [league.id, turns]);
  useEffect(() => {
    // Braces matter: newer browsers return a Promise from scrollIntoView, which React would take as a cleanup.
    lastRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [turns.length, busy]);

  const enabled = status.data?.enabled ?? false;
  const previous = [...turns].reverse().find((t) => t.responseId)?.responseId;

  const ask = useCallback(async (q: { prompt?: string; preset?: string }, followUp: boolean) => {
    const label = q.preset ? PRESETS.find((p) => p.key === q.preset)?.title ?? q.preset : q.prompt!;
    const id = Date.now();
    setBusy(true);
    setTurns((ts) => [...ts, { id, prompt: label }]);
    try {
      const r = await api<AiAnswer>(`/api/leagues/${league.id}/ai/ask`, {
        body: { ...q, previous_response_id: followUp ? previous ?? null : null },
      });
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, answer: r.answer, tools: r.tools_used, seconds: r.seconds, responseId: r.response_id } : t)));
      if (r.quota) setQuota(r.quota);
    } catch (e) {
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, error: e instanceof Error ? e.message : String(e) } : t)));
    } finally {
      setBusy(false);
    }
  }, [league.id, previous]);

  // Links from other pages (?ask=… or ?preset=…) start a fresh question once.
  useEffect(() => {
    const prompt = params.get("ask")?.slice(0, 2000);
    const preset = params.get("preset") ?? undefined;
    if (autoAsked.current || !enabled || (!prompt && !preset)) return;
    autoAsked.current = true;
    const next = new URLSearchParams(params);
    next.delete("ask");
    next.delete("preset");
    setParams(next, { replace: true });
    void ask(prompt ? { prompt } : { preset }, false);
  }, [params, enabled, ask, setParams]);

  const submit = () => {
    const prompt = draft.trim();
    if (!prompt || busy) return;
    setDraft("");
    void ask({ prompt }, true);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };
  const reset = () => {
    setTurns([]);
    setDraft("");
  };

  const offMessage = isDesktop()
    ? "The assistant isn't available in this build of the app. Download the latest release to use it."
    : "The assistant isn't set up on this server yet. The server owner can add an OpenAI key or connect the Courtside AI proxy (see docs/AI.md).";

  return (
    <div className="stack">
      <PageHead title="Assistant"
        desc="Ask about your team in plain English. The assistant runs Courtside's projections, simulations and trade evaluator, then explains the results. Every number it gives comes from those tools."
        actions={turns.length > 0 && <button className="btn" onClick={reset} disabled={busy}>New conversation</button>} />
      {status.data && !enabled && <Notice level="warn">{offMessage}</Notice>}
      {enabled && myTeamId == null && (
        <Notice level="warn">
          You haven't claimed a team in this league, so answers can't be about your roster. <Link to={`/l/${league.id}/league`}>Claim your team</Link> first.
        </Notice>
      )}

      <div className="grid cols-main">
        <Panel title={myTeamId != null ? `Conversation · ${teamName(myTeamId)}` : "Conversation"} className="assistant">
          {status.isLoading && <LoadingBlock rows={3} />}
          {turns.length === 0 && !status.isLoading && (
            <Empty title="Ask about your league">
              Pick a quick question or type your own, like "Who should I stream on Thursday?" or "Is my center depth a problem?"
            </Empty>
          )}
          <div className="transcript" aria-live="polite">
            {turns.map((t, i) => (
              <article key={t.id} className="turn" ref={i === turns.length - 1 ? lastRef : undefined}>
                <div className="turn-q"><span className="who">You</span><span>{t.prompt}</span></div>
                {t.answer && <Markdown text={t.answer} />}
                {t.error && <Notice level="error">{t.error}</Notice>}
                {!t.answer && !t.error && (
                  <div className="turn-wait">
                    <span className="small muted">Running Courtside's numbers… <Elapsed /></span>
                    <LoadingBlock rows={3} />
                  </div>
                )}
                {t.tools && (
                  <div className="turn-foot">
                    <span className="faint">Ran</span>
                    {t.tools.length === 0 && <span className="faint">no tools</span>}
                    {[...new Map(t.tools.map((u) => [u.name, u])).values()].map((u) => (
                      <Tag key={u.name} tone={u.ok ? undefined : "warn"}>{TOOL_LABELS[u.name] ?? u.name}</Tag>
                    ))}
                    {t.seconds != null && <span className="faint mono">{t.seconds.toFixed(1)}s</span>}
                  </div>
                )}
              </article>
            ))}
          </div>
          <div className="composer">
            <textarea className="input" rows={3} value={draft} maxLength={2000} disabled={!enabled}
              placeholder={previous ? "Ask a follow-up, or start a new conversation" : "Ask anything about your team, matchups, waivers or trades"}
              onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} aria-label="Question" />
            <div className="row between">
              <span className="small faint">
                {quota ? `${quota.device_used} of ${quota.device_limit} AI calls used today on this computer` : "Enter to send, Shift+Enter for a new line"}
              </span>
              <button className="btn primary" onClick={submit} disabled={!enabled || busy || !draft.trim()}>{busy ? "Working…" : "Ask"}</button>
            </div>
          </div>
        </Panel>

        <div className="stack">
          <Panel title="Quick questions" flush>
            <ul className="list presets">
              {PRESETS.filter((p) => !status.data || status.data.presets.includes(p.key)).map((p) => (
                <li key={p.key}>
                  <button className="preset" disabled={!enabled || busy} onClick={() => void ask({ preset: p.key }, false)}>
                    <strong>{p.title}</strong>
                    <span className="small muted">{p.desc}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel title="What it can look up">
            <ul className="steps">
              <li>Standings, power scores and playoff odds</li>
              <li>Any roster, its weak spots and upcoming schedule</li>
              <li>Free agents by rest-of-season value or games this week</li>
              <li>Waiver moves simulated against your real lineup</li>
              <li>Trade evaluation and the trade finder</li>
              <li>Matchup simulations, including benching players</li>
              <li>Recent news and injuries (context only)</li>
            </ul>
            <p className="panel-foot">Your question and the tool results needed to answer it are sent to OpenAI through Courtside's AI service. Nothing else from your league leaves the app.</p>
          </Panel>
        </div>
      </div>
    </div>
  );
}
