import type { RefObject } from "react";

import type { OpFailure } from "../lib/assign";

interface Props {
  failure: OpFailure | null;
  what: string; // «в назначении» / «в смене статуса»
  pwd: RefObject<HTMLInputElement>;
  loginError: string;
  onEnter: () => void; // Enter в поле пароля
}

// Отказ записи в Jira (KON-79 п. 4-5): текст ответа как есть; истёкшая сессия - пароль в том же окне.
export default function JiraFailure({ failure, what, pwd, loginError, onEnter }: Props) {
  if (!failure) return null;
  if (failure.kind === "reauth") {
    return (
      <section className="box box--warn" aria-label="Сессия Jira истекла">
        <strong>Сессия Jira истекла</strong>
        <p>Введите пароль Jira, чтобы продолжить. Введённые данные сохранены, пароль нигде не запоминается.</p>
        <div className="field">
          <label htmlFor="jira-reauth-pwd">Пароль Jira</label>
          <input
            id="jira-reauth-pwd"
            ref={pwd}
            type="password"
            name="jira-password"
            autoComplete="current-password"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                onEnter();
              }
            }}
          />
        </div>
        {loginError && (
          <p className="error" role="alert">
            {loginError}
          </p>
        )}
      </section>
    );
  }
  const validation = failure.status === 422 || failure.status === 404;
  return (
    <section className="box box--err" role="alert">
      <strong className="error">{validation ? "Проверьте данные" : `Jira отказала ${what}`}</strong>
      <p>{failure.message}</p>
      <p className="hint">
        {validation
          ? "Введённые данные сохранены — можно исправить и повторить."
          : "Текст ответа Jira показан как есть. Введённые данные сохранены — можно исправить и повторить."}
      </p>
    </section>
  );
}
