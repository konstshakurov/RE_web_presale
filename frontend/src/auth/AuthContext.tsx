import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api, setCsrfToken, setUnauthorizedHandler } from "../api/client";
import type { MeResponse } from "../api/types";

interface AuthState {
  user: MeResponse | null;
  loading: boolean;
  login: (login: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  relogin: (password: string) => Promise<void>; // повторный ввод пароля Jira (FR-029)
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const apply = useCallback((me: MeResponse | null) => {
    setCsrfToken(me?.csrf_token ?? "");
    setUser(me);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => apply(null)); // 401 -> ProtectedRoute уводит на вход
    api
      .me()
      .then(apply)
      .catch(() => apply(null))
      .finally(() => setLoading(false));
    return () => setUnauthorizedHandler(null);
  }, [apply]);

  const login = useCallback(
    async (loginName: string, password: string) => {
      apply(await api.login({ login: loginName, password }));
    },
    [apply],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      apply(null);
    }
  }, [apply]);

  const relogin = useCallback(
    async (password: string) => {
      apply(await api.jiraRelogin(password));
    },
    [apply],
  );

  const value = useMemo(
    () => ({ user, loading, login, logout, relogin }),
    [user, loading, login, logout, relogin],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth вне AuthProvider");
  return ctx;
}
