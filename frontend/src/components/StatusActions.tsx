import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { ActionField, NeedOut, StatusActionOut } from "../api/types";
import { ACTION_META, statusButtons } from "../lib/assign";
import type { ActionKey } from "../lib/assign";
import { useJiraOp } from "../lib/useJiraOp";
import JiraFailure from "./JiraFailure";
import Modal from "./Modal";

interface Props {
  need: NeedOut;
  onChanged: () => void; // статус изменён в Jira: родитель перечитывает список
}

// Кнопки-ссылки под чипом статуса (KON-79, KON-96): белый список × статус из кэша, без запросов к Jira.
// Переходы и поля запрашиваются у Jira при клике; не разрешила - диалог покажет «действие недоступно».
export default function StatusActions({ need, onChanged }: Props) {
  const [open, setOpen] = useState<ActionKey | null>(null);
  const buttons = statusButtons(need.status);
  if (buttons.length === 0) return null;
  return (
    <>
      <div className="acts">
        {buttons.map((k) => (
          <button
            key={k}
            type="button"
            className="link link--inline"
            aria-label={`${ACTION_META[k].label}, ${need.jira_key}`}
            onClick={() => setOpen(k)}
          >
            {ACTION_META[k].label}
          </button>
        ))}
      </div>
      {open && (
        <StatusDialog
          need={need}
          actionKey={open}
          onClose={() => setOpen(null)}
          onDone={() => {
            setOpen(null);
            onChanged();
          }}
        />
      )}
    </>
  );
}

interface DialogProps {
  need: NeedOut;
  actionKey: ActionKey;
  onClose: () => void;
  onDone: () => void;
}

// Один запрос к Jira при открытии диалога (GET status-actions); отказ - текст как есть (FR-029).
export function StatusDialog({ need, actionKey, onClose, onDone }: DialogProps) {
  const op = useJiraOp();
  const meta = ACTION_META[actionKey];
  const [action, setAction] = useState<StatusActionOut | null>(null);
  const [loaded, setLoaded] = useState(false);

  async function load() {
    const ok = await op.run(async () => {
      const r = await api.statusActions(need.jira_key);
      setAction(r.actions.find((a) => a.action === actionKey) ?? null);
    }, "Не удалось получить переходы Jira");
    setLoaded(ok);
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loaded && action) return <StatusForm need={need} action={action} onClose={onClose} onDone={onDone} />;

  const reauth = op.failure?.kind === "reauth";
  async function retry() {
    if (reauth && !(await op.reauth())) return;
    await load();
  }
  return (
    <Modal
      title={meta.title}
      wide
      size="profile"
      onClose={op.busy ? undefined : onClose}
      footer={
        <>
          <button type="button" disabled={op.busy} onClick={onClose}>
            Закрыть
          </button>
          {op.failure && (
            <span className="modal__acts">
              <button type="button" className="primary" disabled={op.busy} onClick={() => void retry()}>
                {reauth ? "Войти и продолжить" : "Повторить"}
              </button>
            </span>
          )}
        </>
      }
    >
      <JiraFailure failure={op.failure} what="в получении переходов" pwd={op.pwd} loginError={op.loginError} onEnter={() => void retry()} />
      {!op.failure && !loaded && <p className="muted">Запрашиваем доступные действия в Jira…</p>}
      {loaded && !action && (
        <section className="box box--warn" role="alert">
          <strong>Действие недоступно</strong>
          <p>
            Jira не предлагает переход в «{meta.target}» из статуса «{need.status}» (права или воркфлоу).
          </p>
        </section>
      )}
    </Modal>
  );
}

function initialValues(fields: ActionField[]): Record<string, string> {
  const v: Record<string, string> = {};
  for (const f of fields) v[f.field_id] = f.prefill_id ?? "";
  return v;
}

function shortKey(key: string): string {
  return key.replace(/^[A-Z][A-Z0-9_]*-(?=\d+$)/, "");
}

// «Поиск» - подтверждение одним кликом; «Найм» - плюс поля ставки; «Кандидатов нет» - причина и комментарий (UC-002).
function StatusForm({
  need,
  action,
  onClose,
  onDone,
}: {
  need: NeedOut;
  action: StatusActionOut;
  onClose: () => void;
  onDone: () => void;
}) {
  const op = useJiraOp();
  const [values, setValues] = useState(() => initialValues(action.fields));
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const noCandidates = action.action === "no_candidates";
  const shown = action.fields.filter((f) => f.options.length > 0);
  const reason = action.fields.find((f) => f.kind === "reason");
  const reauth = op.failure?.kind === "reauth";

  async function send() {
    const missing = shown.filter((f) => (f.required || (noCandidates && f.kind === "reason")) && !values[f.field_id]);
    if (noCandidates && !reason) {
      setError("Jira не передала справочник причин отсутствия кандидата");
      return;
    }
    if (missing.length > 0) {
      setError(`Заполните: ${missing.map((f) => f.name).join(", ")}`);
      return;
    }
    if (noCandidates && !comment.trim()) {
      setError("Укажите комментарий");
      return;
    }
    setError("");
    const fields: Record<string, string> = {};
    for (const f of shown) if (values[f.field_id]) fields[f.field_id] = values[f.field_id];
    const ok = await op.run(
      () => api.changeStatus(need.jira_key, { action: action.action, comment: comment.trim(), fields }),
      "Не удалось сменить статус",
    );
    if (ok) onDone();
  }

  async function submit() {
    if (reauth && !(await op.reauth())) return;
    await send();
  }

  const primaryText = reauth ? "Войти и продолжить" : op.failure ? "Повторить" : `Перевести в «${action.target_status}»`;
  const title = noCandidates ? "Кандидатов нет" : action.action === "hire" ? "Найм" : "Поиск кандидата";

  return (
    <Modal
      title={title}
      wide
      size="profile"
      onClose={op.busy ? undefined : onClose}
      footer={
        <>
          <button type="button" disabled={op.busy} onClick={onClose}>
            Отмена
          </button>
          <span className="modal__acts">
            <button type="button" className="primary" disabled={op.busy} onClick={() => void submit()}>
              {primaryText}
            </button>
          </span>
        </>
      }
    >
      <JiraFailure failure={op.failure} what="в смене статуса" pwd={op.pwd} loginError={op.loginError} onEnter={() => void submit()} />
      <p className="ctx">
        Потребность {shortKey(need.jira_key)} · сейчас «{need.status}»
      </p>
      {!noCandidates && shown.length === 0 && (
        <p>
          Перевести потребность в «{action.target_status}»?
        </p>
      )}
      {action.action === "hire" && shown.length > 0 && <p className="hint">Предзаполнено из потребности. Обязательно в Jira.</p>}
      {shown.map((f) => {
        const required = f.required || f.kind === "reason";
        return (
          <div className="field" key={f.field_id}>
            <label htmlFor={`sf-${f.field_id}`}>
              {f.name}
              {required && <span className="error">&nbsp;*</span>}
            </label>
            <select
              id={`sf-${f.field_id}`}
              value={values[f.field_id] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [f.field_id]: e.target.value }))}
            >
              <option value="">Выберите…</option>
              {f.options.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </div>
        );
      })}
      {noCandidates && (
        <div className="field">
          <label htmlFor="sf-comment">
            Комментарий<span className="error">&nbsp;*</span>
          </label>
          <textarea id="sf-comment" rows={3} value={comment} maxLength={32000} onChange={(e) => setComment(e.target.value)} />
          <div className="hint">Видимость комментария: для всех.</div>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Modal>
  );
}
