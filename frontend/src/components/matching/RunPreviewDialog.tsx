import { useEffect, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { RunOut } from "../../api/types";
import { replacementsText, stageLabel } from "../../lib/matching";
import Modal from "../Modal";

interface Props {
  runId: string;
  onClose: () => void; // закрыть окно, ничего не отправляя: прогон остаётся ожидать подтверждения
  onConfirmed: () => void;
  onCancelled: () => void;
}

// Предпросмотр обезличенного текста всего прогона и единое подтверждение внешней отправки (FR-023).
// Пока пользователь не нажал «Подтвердить и отправить», во внешнюю LLM ничего не уходит.
export default function RunPreviewDialog({ runId, onClose, onConfirmed, onCancelled }: Props) {
  const [run, setRun] = useState<RunOut | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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

  const canConfirm = !!run && run.requires_confirmation && run.status === "awaiting_confirmation" && !busy;

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
          {run.tasks.map((t, i) => (
            <details key={t.task_id} open={run.tasks.length === 1}>
              <summary>
                Пакет {i + 1}: кандидатов {t.candidates}, подстановки: {replacementsText(t.replacements)}
              </summary>
              {t.error_message && <p className="error">{t.error_message}</p>}
              <pre className="cv-text" aria-label={`Текст пакета ${i + 1}`}>
                {t.prompt ?? ""}
              </pre>
            </details>
          ))}
          {!run.requires_confirmation && <p className="muted">Подтверждение не требуется.</p>}
        </>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="modal__actions">
        <button type="button" className="primary" disabled={!canConfirm} onClick={() => void act("confirm")}>
          Подтвердить и отправить
        </button>
        <button type="button" disabled={busy || !run} onClick={() => void act("cancel")}>
          Отменить расчёт
        </button>
        <button type="button" disabled={busy} onClick={onClose}>
          Закрыть, не отправляя
        </button>
      </div>
    </Modal>
  );
}
