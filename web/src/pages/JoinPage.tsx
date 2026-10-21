import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrandMark } from "../components/Layout";
import { ErrorBox, LoadingBlock } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { AuthForm } from "./AuthPage";

interface Preview {
  league_name: string;
  season: number;
  owner: string | null;
  member_count: number;
  teams: { id: number; name: string; claimed: boolean }[];
}

export function JoinPage() {
  const { code = "" } = useParams();
  const { user } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [team, setTeam] = useState<number | "">("");
  const preview = useQuery({ queryKey: ["invite", code], queryFn: () => api<Preview>(`/api/invites/${code}`), retry: false });
  const accept = useMutation({
    mutationFn: () => api<{ league_id: string }>(`/api/invites/${code}/accept`, { body: { team_id: team === "" ? null : team } }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["leagues"] });
      nav(`/l/${r.league_id}`);
    },
  });
  const p = preview.data;
  return (
    <main className="content" style={{ maxWidth: 560, paddingTop: 56 }}>
      <div className="row" style={{ marginBottom: 28 }}>
        <BrandMark size={30} />
        <span className="brand-name">Courtside</span>
      </div>
      {preview.isLoading && <LoadingBlock />}
      {preview.error && <ErrorBox error={preview.error} />}
      {p && (
        <div className="stack" style={{ gap: 22 }}>
          <div className="stack" style={{ gap: 8, borderBottom: "2px solid var(--ink)", paddingBottom: 16 }}>
            <div className="kicker accent">You're invited</div>
            <h1 className="h1">{p.league_name}</h1>
            <p className="lede">
              {p.owner ? `${p.owner} invited you` : "You've been invited"} to the {p.season - 1}–{String(p.season).slice(2)} league hub · {p.member_count} member
              {p.member_count === 1 ? "" : "s"} so far.
            </p>
          </div>
          {!user ? (
            <div className="panel"><div className="panel-body"><AuthForm compact /></div></div>
          ) : (
            <div className="stack">
              <div className="field">
                <label htmlFor="team">Which team is yours?</label>
                <select id="team" className="select" value={team} onChange={(e) => setTeam(e.target.value === "" ? "" : Number(e.target.value))}>
                  <option value="">I'll pick later</option>
                  {p.teams.map((t) => (
                    <option key={t.id} value={t.id} disabled={t.claimed}>
                      {t.name}
                      {t.claimed ? " (claimed)" : ""}
                    </option>
                  ))}
                </select>
                <span className="hint">Your team gets personalized recommendations; you'll still see the whole league.</span>
              </div>
              {accept.error && <ErrorBox error={accept.error} />}
              <button className="btn accent" onClick={() => accept.mutate()} disabled={accept.isPending} style={{ height: 42 }}>
                {accept.isPending ? "Joining…" : "Join league"}
              </button>
            </div>
          )}
        </div>
      )}
    </main>
  );
}
