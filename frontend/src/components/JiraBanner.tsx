import { useRef, useState } from "react";
import type { FormEvent } from "react";

import { useAuth } from "../auth/AuthContext";

const FALLBACK = "Сессия Jira недоступна";

// Баннер под шапкой при отказе Jira (FR-029): текст отказа как есть, работа с Jira недоступна,
// разделы приложения открыты. Пароль не в state: читается из поля при отправке и сразу очищается.
export default function JiraBanner() {
  const { user, relogin } = useAuth();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);

  const state = user?.jira_status.state;
  if (state !== "blocked" && state !== "unavailable") return null;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const input = passwordRef.current!;
    const typed = input.value;
    input.value = "";
    if (!typed) return;
    setError("");
    setBusy(true);
    try {
      await relogin(typed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отправить пароль");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="jira-banner" aria-label="Состояние Jira">
      <p className="jira-banner__text" role="status">
        <strong>Работа с Jira недоступна.</strong> {user?.jira_status.message || FALLBACK}
      </p>
      <form className="jira-banner__form" method="post" onSubmit={(e) => void onSubmit(e)}>
        <label>
          Пароль Jira
          <input ref={passwordRef} name="jira-password" type="password" autoComplete="current-password" />
        </label>
        <button type="submit" disabled={busy}>
          Ввести пароль повторно
        </button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
