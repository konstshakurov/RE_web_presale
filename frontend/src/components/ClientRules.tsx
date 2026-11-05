import { useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError, api } from "../api/client";
import type { EpicOut, ThesesOut } from "../api/types";
import { formatDateTime, refreshErrorText } from "../lib/needs";

// Правила клиента (FR-015, BR-006): поля эпика + тезисы из комментариев (KON-160).
// Тезисы - только информирование: сгенерировано LLM, источник и ссылка у каждого.
const REGEN_POLL_MS = 8000;
const REGEN_MAX_TRIES = 10;

export default function ClientRules({ epicKey }: { epicKey: string }) {
  const [epic, setEpic] = useState<EpicOut | null>(null);
  const [theses, setTheses] = useState<ThesesOut | null>(null);
  const [error, setError] = useState("");
  const [thesesError, setThesesError] = useState("");
  const regenTimer = useRef<number | null>(null);

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

  useEffect(() => {
    let alive = true;
    let tries = 0;

    const load = () => {
      if (!alive) return;
      api
        .clientRuleTheses(epicKey)
        .then((t) => {
          if (!alive) return;
          setTheses(t);
          setThesesError("");
          if (t.status === "regenerating" && tries < REGEN_MAX_TRIES) {
            tries += 1;
            regenTimer.current = window.setTimeout(load, REGEN_POLL_MS);
          }
        })
        .catch((e: unknown) => {
          if (!alive) return;
          // 404 - потребность не найдена, 409 - «потребность без клиента»; текст сервера
          if (e instanceof ApiError && e.status === 409) setThesesError(e.message);
          else if (e instanceof ApiError && e.status === 404)
            setThesesError("Потребность не найдена");
          else setThesesError("Не удалось загрузить тезисы правил клиента");
        });
    };
    load();
    return () => {
      alive = false;
      if (regenTimer.current !== null) window.clearTimeout(regenTimer.current);
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
      <ThesesSection data={theses} error={thesesError} />
    </div>
  );
}

function ThesesSection({ data, error }: { data: ThesesOut | null; error: string }) {
  if (error) return <p className="error">{error}</p>;
  if (!data) return <section className="theses muted">Загрузка тезисов…</section>;
  if (data.status === "empty")
    return (
      <section className="theses">
        <p className="muted">Правил из комментариев нет: клиент ещё не писал в эпик</p>
      </section>
    );
  const regenerating = data.status === "regenerating";
  return (
    <section className="theses" aria-busy={regenerating}>
      <h4>
        Правила из комментариев эпика
        {regenerating && (
          <span className="badge theses__badge" title="Хеш комментариев изменился: тезисы пересчитываются LLM">
            Обновляется…
          </span>
        )}
      </h4>
      {regenerating && data.task_submitted === false && (
        <p className="muted">
          Тезисы считаются асинхронно (провайдер по умолчанию - внутренняя LLM); страница
          обновится автоматически.
        </p>
      )}
      {regenerating && data.theses.length === 0 && (
        <p className="muted">Тезисов пока нет</p>
      )}
      {data.theses.length > 0 && (
        <ul>
          {data.theses.map((t, i) => (
            <li
              key={`${i}:${t.text.slice(0, 40)}`}
              className={t.contradicts_rules ? "theses__thesis--conflict" : ""}
            >
              <p className="theses__text">
                {t.text}
                {t.contradicts_rules && (
                  <span className="badge badge--stale theses__badge" title="Противоречит полевому правилу клиента">
                    противоречит полевым правилам
                  </span>
                )}
              </p>
              {t.sources.length > 0 && <SourcesLine sources={t.sources} />}
            </li>
          ))}
        </ul>
      )}
      <p className="theses__note">
        Сгенерировано LLM по комментариям эпика
        {data.provider ? ` (${data.provider})` : ""}
        {data.generated_at ? `, ${formatDateTime(data.generated_at)}` : ""}; проверяйте
        по источникам.
      </p>
    </section>
  );
}

function SourcesLine({
  sources,
}: {
  sources: { id: string; author?: string | null; date?: string | null; url?: string | null }[];
}) {
  const parts: ReactNode[] = [];
  sources.forEach((s, i) => {
    const label = `${s.author || s.id}${s.date ? `, ${formatDateTime(s.date)}` : ""}`;
    if (i > 0) parts.push("; ");
    parts.push(
      s.url ? (
        <a key={s.id} href={s.url} target="_blank" rel="noreferrer">
          {label}
        </a>
      ) : (
        <span key={s.id}>{label}</span>
      ),
    );
  });
  return (
    <p className="theses__src">
      Источник: {parts}
    </p>
  );
}

