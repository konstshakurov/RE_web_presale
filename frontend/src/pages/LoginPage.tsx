import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import Header from "../components/Header";

// Возврат на маршрут, с которого увела потеря сессии; только внутренние пути.
function returnPath(state: unknown): string {
  const from = (state as { from?: unknown } | null)?.from;
  return typeof from === "string" && from.startsWith("/") && !from.startsWith("//") && !from.startsWith("/login")
    ? from
    : "/needs";
}

export default function LoginPage() {
  const { user, loading, login, sessionExpired } = useAuth();
  const location = useLocation();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [missing, setMissing] = useState({ login: false, password: false });
  // Пароль не в state: читается из поля в момент отправки и сразу очищается.
  const loginRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  if (!loading && user) return <Navigate to={returnPath(location.state)} replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const pwInput = passwordRef.current!;
    const name = loginRef.current!.value.trim();
    const typed = pwInput.value;
    const empty = { login: name === "", password: typed === "" };
    setMissing(empty);
    if (empty.login || empty.password) {
      setError("");
      return;
    }
    pwInput.value = "";
    setError("");
    setBusy(true);
    try {
      await login(name, typed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось войти");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Header />
      <main className="content">
        <form className="login" method="post" noValidate onSubmit={(e) => void onSubmit(e)}>
          <h1>Вход</h1>
          {sessionExpired && !loading && (
            <p role="status" className="muted">
              Сессия истекла. Войдите снова — вы вернётесь на прежнюю страницу.
            </p>
          )}
          {loading && (
            <p role="status" className="muted">
              Проверяем сессию…
            </p>
          )}
          <label>
            Логин
            <input
              ref={loginRef}
              name="username"
              type="text"
              autoComplete="username"
              required
              disabled={busy || loading}
              aria-invalid={missing.login || undefined}
            />
          </label>
          {missing.login && <div className="field-hint">Укажите логин</div>}
          <label>
            Пароль
            <input
              ref={passwordRef}
              name="password"
              type="password"
              autoComplete="current-password"
              required
              disabled={busy || loading}
              aria-invalid={missing.password || undefined}
            />
          </label>
          {missing.password && <div className="field-hint">Укажите пароль</div>}
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <button type="submit" className="primary" disabled={busy || loading}>
            {busy ? "Входим…" : "Войти"}
          </button>
        </form>
      </main>
    </>
  );
}
