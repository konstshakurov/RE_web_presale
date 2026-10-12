import { useEffect, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { RunOut, RunTask } from "../../api/types";
import { promptRejection, replacementsText, stageLabel, type PromptRejection } from "../../lib/matching";
import Modal from "../Modal";

interface Props {
  runId: string;
  onClose: () => void; // «Отложить»: только закрыть окно, прогон остаётся ожидать подтверждения
  onConfirmed: () => void;
  onCancelled: () => void;
}

function isDirty(t: RunTask, drafts: Record<string, string>): boolean {
  const d = drafts[t.task_id];
  return d !== undefined && d !== (t.prompt ?? "");
}

function without<T>(rec: Record<string, T>, key: string): Record<string, T> {
  const next = { ...rec };
  delete next[key];
  return next;
}

// Предпросмотр обезличенного текста всего прогона и единое подтверждение внешней отправки (FR-023).
// Пока пользователь не нажал «Подтвердить и отправить», во внешнюю LLM ничего не уходит.
// Текст каждого пакета можно поправить; правка проверяется backend на остатки ПДн перед сохранением.
export default function RunPreviewDialog({ runId, onClose, onConfirmed, onCancelled }: Props) {
  const [run, setRun] = useState<RunOut | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({}); // несохранённые правки по task_id
  const [rejected, setRejected] = useState<Record<string, PromptRejection>>({}); // отказ сохранения по task_id
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .runPreview(runId)
      .then((r) => alive && setRun(r))
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "Не удалось загрузить предпросмотр"));
    return () => {
      alive = false;
    };
  }, [runId]);

  async function act(kind: "confirm" | "cancel") {
    setBusy(true);
    setError("");
    try {
      if (kind === "confirm") await api.confirmRun(runId);
      else await api.cancelRun(runId);
      if (kind === "confirm") onConfirmed();
      else onCancelled();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось выполнить действие");
      setBusy(false);
    }
  }

  async function savePrompt(t: RunTask) {
    const text = drafts[t.task_id];
    if (text === undefined) return;
    setSaving(t.task_id);
    setRejected((r) => without(r, t.task_id));
    try {
      const res = await api.updateTaskPrompt(runId, t.task_id, text);
      // ответ - обновлённый прогон либо одна задача; в обоих случаях подставляем сохранённый текст
      setRun((cur) => {
        if (!cur) return cur;
        if ("tasks" in res && Array.isArray(res.tasks)) return res;
        const upd = res as RunTask;
        return { ...cur, tasks: cur.tasks.map((x) => (x.task_id === t.task_id ? { ...x, ...upd } : x)) };
      });
      setDrafts((d) => without(d, t.task_id));
    } catch (e) {
      // текст в поле остаётся как ввёл пользователь
      setRejected((r) => ({ ...r, [t.task_id]: promptRejection(e) }));
    } finally {
      setSaving(null);
    }
  }

  const hasDrafts = !!run && run.tasks.some((t) => isDirty(t, drafts));
  const canConfirm =
    !!run && run.requires_confirmation && run.status === "awaiting_confirmation" && !busy && !hasDrafts && saving === null;

  return (
    <Modal title="Предпросмотр отправки во внешнюю LLM" wide>
      {!run && !error && <p className="muted">Загрузка…</p>}
      {run && (
        <>
          <p>
            Провайдер: <strong>{run.provider_name}</strong>, ступень: {stageLabel(run.stage) || run.stage}. Кандидатов:{" "}
            {run.candidates}, пакетов: {run.tasks.length}.
          </p>
          <p className="muted">
            Во внешнюю модель уйдёт только обезличенный текст, показанный ниже: без ФИО, названий компаний и ключей.
            Подстановки: {replacementsText(run.replacements)}. Отправка выполняется один раз для всего прогона.
          </p>
          {run.tasks.map((t, i) => {
            const dirty = isDirty(t, drafts);
            const rej = rejected[t.task_id];
            const editable = t.editable === true && run.status === "awaiting_confirmation";
            return (
              <details key={t.task_id} open={run.tasks.length === 1 || dirty}>
                <summary>
                  Пакет {i + 1}: кандидатов {t.candidates}, подстановки: {replacementsText(t.replacements)}
                  {(t.edited || dirty) && <span className="status">{dirty ? "изменено, не сохранено" : "изменено"}</span>}
                </summary>
                {t.error_message && <p className="error">{t.error_message}</p>}
                {editable ? (
                  <>
                    <textarea
                      className="cv-text prompt-edit"
                      rows={12}
                      aria-label={`Текст пакета ${i + 1}`}
                      value={drafts[t.task_id] ?? t.prompt ?? ""}
                      disabled={busy || saving === t.task_id}
                      onChange={(e) => setDrafts((d) => ({ ...d, [t.task_id]: e.target.value }))}
                    />
                    <p className="muted">Правки проверяются на остатки персональных данных перед сохранением.</p>
                    {rej && (
                      <div role="alert">
                        <p className="error">{rej.message}</p>
                        {rej.fragments.length > 0 && (
                          <ul className="error" aria-label={`Найденные фрагменты пакета ${i + 1}`}>
                            {rej.fragments.map((f, k) => (
                              <li key={k}>{f}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                    <button
                      type="button"
                      aria-label={`Сохранить правки пакета ${i + 1}`}
                      disabled={!dirty || busy || saving !== null}
                      onClick={() => void savePrompt(t)}
                    >
                      Сохранить правки
                    </button>
                  </>
                ) : (
                  <pre className="cv-text" aria-label={`Текст пакета ${i + 1}`}>
                    {t.prompt ?? ""}
                  </pre>
                )}
              </details>
            );
          })}
          {!run.requires_confirmation && <p className="muted">Подтверждение не требуется.</p>}
        </>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {hasDrafts && <p className="muted">Есть несохранённые правки: сохраните их, чтобы отправить именно этот текст.</p>}
      <div className="modal__actions">
        <button type="button" className="primary" disabled={!canConfirm} onClick={() => void act("confirm")}>
          Подтвердить и отправить
        </button>
        <button
          type="button"
          disabled={busy}
          title="Окно закроется, расчёт останется в ожидании подтверждения"
          onClick={onClose}
        >
          Отложить
        </button>
        <button
          type="button"
          disabled={busy || !run}
          title="Прогон будет отменён, текст не будет отправлен; начать заново можно кнопкой «Сопоставить»"
          onClick={() => void act("cancel")}
        >
          Отменить расчёт
        </button>
      </div>
      <p className="muted">
        «Отложить» только закрывает окно: расчёт останется в ожидании подтверждения (баннер «Проверить и отправить»).
        «Отменить расчёт» отменяет прогон: текст не будет отправлен, начать заново можно кнопкой «Сопоставить».
      </p>
    </Modal>
  );
}
