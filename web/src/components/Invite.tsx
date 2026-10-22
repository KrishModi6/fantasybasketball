import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus } from "lucide-react";
import { api, isLocalApp } from "../lib/api";
import { shortDate } from "../lib/format";
import { useToast } from "../lib/toast";
import type { Invite, League } from "../lib/types";
import { Dialog, ErrorBox, Notice, Panel } from "./ui";

const DOWNLOAD_URL = "https://github.com/KrishModi6/fantasybasketball/releases/latest";

/** Whether this member can invite anyone: owners, except of a copy that runs on someone else's ESPN login. */
export function canInvite(league: League): boolean {
  if (league.me?.role !== "owner") return false;
  if (!isLocalApp()) return true;
  return league.source !== "flaim" && !(league.connector.shared_by && league.connector.has_cookies);
}

/**
 * The desktop app runs its own engine on each computer, so a link to this one can't reach anyone else.
 * There, invites are codes that carry the league's connection; on a shared server they're links.
 */
export function InvitePanel({ league }: { league: League }) {
  return isLocalApp() ? <CodeInvite league={league} /> : <LinkInvites league={league} />;
}

/** Topbar shortcut for owners. */
export function InviteButton({ league }: { league: League }) {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  if (!canInvite(league)) return null;
  return (
    <>
      <button className="btn" onClick={() => setOpen(true)} title="Invite your league mates">
        <UserPlus /> <span className="hide-sm">Invite</span>
      </button>
      {open && (
        <Dialog title={`Invite people to ${league.name}`} onClose={close}>
          <InvitePanel league={league} />
          <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" onClick={close}>Done</button></div>
        </Dialog>
      )}
    </>
  );
}

function CodeInvite({ league }: { league: League }) {
  const toast = useToast();
  const [team, setTeam] = useState<number | "">("");
  const [agreed, setAgreed] = useState(false);
  const [copied, setCopied] = useState<"code" | "message" | null>(null);
  const login = Boolean(league.connector.has_cookies);
  const create = useMutation({
    mutationFn: () => api<{ code: string; includes_login: boolean }>(`/api/leagues/${league.id}/share-code`, {
      body: { team_id: team === "" ? null : team },
    }),
  });

  if (league.source === "flaim") {
    return <Panel title="Invite with a code"><p className="small muted">Flaim leagues can't be shared with a code. Each member connects with their own Flaim token.</p></Panel>;
  }
  if (league.connector.shared_by && login) {
    return <Panel title="Invite with a code"><p className="small muted">This league runs on {league.connector.shared_by}'s ESPN login, so invite codes have to come from them.</p></Panel>;
  }

  const code = create.data?.code;
  const message = code
    ? `Join ${league.name} on Courtside:\n1. Install Courtside: ${DOWNLOAD_URL}\n2. Open it and choose "Join with invite".\n3. Paste this code:\n${code}`
    : "";
  const copy = async (what: "code" | "message") => {
    try {
      await navigator.clipboard.writeText(what === "code" ? code! : message);
      setCopied(what);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      toast("Copy failed. Select the code and copy it by hand.", "err");
    }
  };
  const others = league.teams.filter((t) => t.id !== league.me?.team_id);

  return (
    <Panel title="Invite with a code">
      <div className="form">
        <p className="small muted">
          Each person runs Courtside on their own computer. The code connects their copy to this league in one step, with no league ID{login ? " or ESPN cookies" : ""} to look up.
        </p>
        <div className="field">
          <label htmlFor="inv-team">Who is it for?</label>
          <select id="inv-team" className="select" value={team}
            onChange={(e) => { setTeam(e.target.value === "" ? "" : Number(e.target.value)); create.reset(); }}>
            <option value="">Anyone in the league (they pick their team)</option>
            {others.map((t) => <option key={t.id} value={t.id}>{t.name}{t.owner_name ? ` (${t.owner_name})` : ""}</option>)}
          </select>
        </div>
        {login && (
          <>
            <Notice level="warn">
              This is a private ESPN league, so the code includes your ESPN login cookies. Anyone who has the code could use them to act as you on ESPN, not just read the league. Send it only to people in your league, in a private chat.
            </Notice>
            <label className="row small" style={{ alignItems: "flex-start" }}>
              <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
              <span>I understand. Share my ESPN login with the people I send this to.</span>
            </label>
          </>
        )}
        {create.error && <ErrorBox error={create.error} />}
        {!code ? (
          <div>
            <button className="btn primary" onClick={() => create.mutate()} disabled={(login && !agreed) || create.isPending}>
              {create.isPending ? "Creating…" : "Create invite code"}
            </button>
          </div>
        ) : (
          <>
            <textarea className="input mono code-box" readOnly rows={4} value={code} aria-label="Invite code" onFocus={(e) => e.currentTarget.select()} />
            <div className="row">
              <button className="btn primary" onClick={() => copy("message")}>{copied === "message" ? "Copied" : "Copy invite message"}</button>
              <button className="btn" onClick={() => copy("code")}>{copied === "code" ? "Copied" : "Copy code only"}</button>
            </div>
            <p className="tiny faint">
              The message includes the download link and steps. {team === "" ? "One code works for the whole league." : "Whoever uses this code gets that team."} If
              the league stops loading for them later (for example after you replace your cookies), send a new code.
            </p>
          </>
        )}
      </div>
    </Panel>
  );
}

function LinkInvites({ league }: { league: League }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [copied, setCopied] = useState<string | null>(null);
  const { data } = useQuery({ queryKey: ["league", league.id, "invites"], queryFn: () => api<Invite[]>(`/api/leagues/${league.id}/invites`) });
  const create = useMutation({
    mutationFn: () => api<Invite>(`/api/leagues/${league.id}/invites`, { body: { expires_in_days: 30 } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["league", league.id, "invites"] }),
    onError: (e: Error) => toast(e.message, "err"),
  });
  const revoke = useMutation({
    mutationFn: (code: string) => api(`/api/leagues/${league.id}/invites/${code}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["league", league.id, "invites"] }),
  });
  const link = (inv: Invite) => (window.location.protocol.startsWith("http") ? `${window.location.origin}/join/${inv.code}` : inv.url);
  const copy = async (inv: Invite) => {
    try {
      await navigator.clipboard.writeText(link(inv));
      setCopied(inv.code);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      toast(`Copy failed. The code is ${inv.code}.`, "err");
    }
  };
  return (
    <Panel title="Invite links" flush note="Anyone with a link can create an account, join and claim a team. No ESPN cookies needed. Links expire after 30 days."
      actions={<button className="btn primary" onClick={() => create.mutate()} disabled={create.isPending}>New invite</button>}>
      {data && data.length === 0 ? <p className="small muted" style={{ padding: "0 10px 10px" }}>No active invites.</p> : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Code</th><th className="n">Joined</th><th>Expires</th><th /></tr></thead>
            <tbody>
              {data?.map((inv) => (
                <tr key={inv.code}>
                  <td className="mono">{inv.code}</td>
                  <td className="n">{inv.uses}</td>
                  <td className="muted">{shortDate(inv.expires_at)}</td>
                  <td className="n">
                    <div className="row" style={{ justifyContent: "flex-end" }}>
                      <button className="btn" onClick={() => copy(inv)}>{copied === inv.code ? "Copied" : "Copy link"}</button>
                      <button className="btn danger" onClick={() => revoke.mutate(inv.code)} aria-label={`Revoke ${inv.code}`}>Revoke</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
