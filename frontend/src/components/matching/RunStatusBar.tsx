import { useState } from "react";

import { ApiError, api } from "../../api/client";
import type { RunState } from "../../api/types";
import { LLM_TIMEOUT_SEC, hasTimeout, isAwaiting, isRunning, progressText, retryableTasks, timeoutText } from "../../lib/matching";

interface Props {
  state: RunState;
  onOpenPreview: () => void;
  onRetried: () => void;
}

// Состояние расчёта: индикатор, ожидание подтверждения, таймаут с повтором, причины отказов.
export default function RunStatusBar({ state, onOpenPreview, onRetried }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const retry = retryableTasks(state.tasks);
  const timeout = hasTimeout(state.tasks);

  async function onRetry() {
    setBusy(true);
    setError("");
    try {
      for (const t of retry) await api.retryTask(t.task_id);
      onRetried();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось повторить запрос");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="runstatus">
      {isRunning(state.status) && (
        <p className="runstatus__busy" role="status">
          <span className="spinner" aria-hidden="true" /> {progressText(state)}. Интерфейс доступен, результат появится сам.
        </p>
      )}
      {isAwaiting(state) && (
        <p role="status">
          Расчёт ждёт подтверждения: во внешнюю LLM ничего не отправлено.{" "}
          <button type="button" className="link link--inline" onClick={onOpenPreview}>
            Проверить и отправить
          </button>
        </p>
      )}
      {timeout && (
        <p className="error" role="alert">
          {timeoutText(LLM_TIMEOUT_SEC)}
        </p>
      )}
      {!timeout && retry.length > 0 && (
        <p className="error" role="alert">
          Часть пакетов не обработана.
        </p>
      )}
      {retry.length > 0 && (
        <button type="button" disabled={busy} onClick={() => void onRetry()}>
          Повторить
        </button>
      )}
      {state.errors.length > 0 && !isRunning(state.status) && (
        <ul className="runstatus__errors muted">
          {state.errors.slice(0, 5).map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
