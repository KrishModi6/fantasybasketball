import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ErrorBox, LoadingBlock, Panel } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { AuthForm, StandaloneTop } from "./AuthPage";

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
    <div className="standalone">
      <StandaloneTop />
      <main className="center">
        <div className="auth-box">
          {preview.isLoading && <LoadingBlock rows={3} />}
          {preview.error && <ErrorBox error={preview.error} />}
          {p && (
            <>
              <div className="intro">
                <h1>Join {p.league_name}</h1>
                <p>
                  {p.owner ? `Invited by ${p.owner}` : "Invitation"} · {p.season - 1}–{String(p.season).slice(2)} season · {p.member_count} member
                  {p.member_count === 1 ? "" : "s"}
                </p>
              </div>
              {!user ? (
                <AuthForm />
              ) : (
                <Panel>
                  <div className="form">
                    <div className="field">
                      <label htmlFor="team">Your team</label>
                      <select id="team" className="select" value={team} onChange={(e) => setTeam(e.target.value === "" ? "" : Number(e.target.value))}>
                        <option value="">Choose later</option>
                        {p.teams.map((t) => (
                          <option key={t.id} value={t.id} disabled={t.claimed}>{t.name}{t.claimed ? " (claimed)" : ""}</option>
                        ))}
                      </select>
                      <span className="hint">Recommendations are tailored to this team; the whole league stays visible.</span>
                    </div>
                    {accept.error && <ErrorBox error={accept.error} />}
                    <button className="btn primary lg" onClick={() => accept.mutate()} disabled={accept.isPending}>
                      {accept.isPending ? "Joining…" : "Join league"}
                    </button>
                  </div>
                </Panel>
              )}
            </>
          )}
        </div>
      </main>
    </div>
  );
}
