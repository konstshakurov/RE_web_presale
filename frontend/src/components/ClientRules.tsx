import { useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import type { EpicOut } from "../api/types";
import { formatDateTime, refreshErrorText } from "../lib/needs";

// Правила клиента (FR-015, BR-006): только заполненные поля, только информирование.
export default function ClientRules({ epicKey }: { epicKey: string }) {
  const [epic, setEpic] = useState<EpicOut | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .epic(epicKey)
      .then((e) => alive && setEpic(e))
      .catch((e: unknown) => {
        if (!alive) return;
        if (e instanceof ApiError) setError(refreshErrorText(e.detail?.state, e.message));
        else setError("Не удалось загрузить правила клиента");
      });
    return () => {
      alive = false;
    };
  }, [epicKey]);

  if (error) return <p className="error">{error}</p>;
  if (!epic) return <p className="muted">Загрузка правил клиента…</p>;
  const rules = epic.rules.filter((r) => r.value && r.value.trim() !== "");
  return (
    <div className="rules">
      <h3>Правила клиента: {epic.name}</h3>
      {epic.stale && <span className="badge badge--stale">данные могут отставать от Jira</span>}
      {epic.error?.message && <p className="error">{epic.error.message}</p>}
      {rules.length === 0 ? (
        <p className="muted">Правила клиента не заполнены</p>
      ) : (
        <dl>
          {rules.map((r) => (
            <div key={r.id} className="rules__row">
              <dt>{r.name}</dt>
              <dd>{r.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {epic.fetched_at && <p className="muted">получено из Jira: {formatDateTime(epic.fetched_at)}</p>}
    </div>
  );
}
