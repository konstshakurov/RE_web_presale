import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api, setCsrfToken, setUnauthorizedHandler } from "../api/client";
import type { MeResponse } from "../api/types";

interface AuthState {
  user: MeResponse | null;
  loading: boolean;
  login: (login: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
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

  const value = useMemo(() => ({ user, loading, login, logout }), [user, loading, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth вне AuthProvider");
  return ctx;
}
