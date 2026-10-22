import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, getToken, isLocalApp, setToken } from "./api";
import type { User } from "./types";

interface AuthState {
  user: User | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName: string) => Promise<void>;
  signOut: () => void;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(Boolean(getToken()) || isLocalApp());
  const qc = useQueryClient();

  const localSignIn = useCallback(async () => {
    const r = await api<{ token: string; user: User }>("/api/auth/local", { method: "POST" });
    setToken(r.token);
    setUser(r.user);
  }, []);

  useEffect(() => {
    if (!getToken()) {
      if (isLocalApp()) localSignIn().catch(() => undefined).finally(() => setLoading(false));
      return;
    }
    api<User>("/api/auth/me")
      .then(setUser)
      .catch(() => {
        setToken(null);
        return isLocalApp() ? localSignIn() : undefined;
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [localSignIn]);

  useEffect(() => {
    const onOut = () => {
      setUser(null);
      qc.clear();
      if (isLocalApp()) void localSignIn().catch(() => undefined); // an expired token just renews
    };
    window.addEventListener("cs:signed-out", onOut);
    return () => window.removeEventListener("cs:signed-out", onOut);
  }, [qc, localSignIn]);

  const signIn = useCallback(async (email: string, password: string) => {
    const r = await api<{ token: string; user: User }>("/api/auth/login", { body: { email, password } });
    setToken(r.token);
    setUser(r.user);
  }, []);

  const register = useCallback(async (email: string, password: string, display_name: string) => {
    const r = await api<{ token: string; user: User }>("/api/auth/register", { body: { email, password, display_name } });
    setToken(r.token);
    setUser(r.user);
  }, []);

  const signOut = useCallback(() => {
    setToken(null);
    setUser(null);
    qc.clear();
  }, [qc]);

  const value = useMemo(() => ({ user, loading, signIn, register, signOut }), [user, loading, signIn, register, signOut]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
