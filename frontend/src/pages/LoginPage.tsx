import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { Navigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import Header from "../components/Header";

export default function LoginPage() {
  const { user, loading, login } = useAuth();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Пароль не в state: читается из поля в момент отправки и сразу очищается.
  const loginRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  if (!loading && user) return <Navigate to="/needs" replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const pwInput = passwordRef.current!;
    const name = loginRef.current!.value.trim();
    const typed = pwInput.value;
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
        <form className="login" method="post" onSubmit={(e) => void onSubmit(e)}>
          <h1>Вход</h1>
          <label>
            Логин
            <input ref={loginRef} name="username" type="text" autoComplete="username" required />
          </label>
          <label>
            Пароль
            <input
              ref={passwordRef}
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <button type="submit" disabled={busy}>
            Войти
          </button>
        </form>
      </main>
    </>
  );
}
