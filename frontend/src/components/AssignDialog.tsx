import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import type { FileOut, NeedOut, OpResult, RateBenchmark } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import {
  RATE_FORMAT_TEXT,
  RATE_REQUIRED_TEXT,
  STEP_FIELDS,
  STEP_RATE,
  STEP_TRANSITION,
  attachStep,
  benchmarkText,
  parseRate,
  rateRequired,
  resumeOptions,
} from "../lib/assign";
import { formatDateTime, formatRate } from "../lib/needs";
import { useJiraOp } from "../lib/useJiraOp";
import JiraFailure from "./JiraFailure";
import Modal from "./Modal";

interface Props {
  needKey: string;
  need?: NeedOut | null; // для контекстной строки и признака «ставка обязательна»
  employeeKey: string;
  employeeName: string;
  onClose: () => void;
  onDone: () => void; // успех: родитель перечитывает потребность
  onPartial?: () => void; // назначено, ставка не записана: окно остаётся, родитель перечитывает потребность
}

type Stage = "form" | "confirm" | "sending";

function shortKey(key: string): string {
  return key.replace(/^[A-Z][A-Z0-9_]*-(?=\d+$)/, "");
}

function fileLabel(f: FileOut): string {
  return `${f.kind_title} — ${f.file_name} (${formatDateTime(f.uploaded_at).split(",")[0]})`;
}

function rateText(need: NeedOut): string {
  const r = formatRate(need.rate);
  return [r.main, r.vat, r.proposer && `«${r.proposer}»`].filter(Boolean).join(", ");
}

// Окно «Назначить кандидата» (KON-79, вариант A; UC-001, FR-008, FR-010, FR-018): форма -> подтверждение -> отправка.
// Ручной смены статуса нет: «Кандидат назначен» выставляет Jira. Введённое хранится здесь и переживает отказы и повторный вход.
export default function AssignDialog({ needKey, need, employeeKey, employeeName, onClose, onDone, onPartial }: Props) {
  const { user } = useAuth();
  const op = useJiraOp();
  const [files, setFiles] = useState<FileOut[] | null>(null);
  const [filesError, setFilesError] = useState("");
  const [stage, setStage] = useState<Stage>("form");
  const [resumeId, setResumeId] = useState("main"); // main - Основное CV (resume_file_id = null)
  const [rate, setRate] = useState("");
  const [rateError, setRateError] = useState("");
  const [comment, setComment] = useState("");
  const [partial, setPartial] = useState(""); // предупреждение: назначено, но ставка не записана
  const [assigned, setAssigned] = useState(false); // назначение выполнено: повтор пишет только ставку, остальные поля заблокированы
  const [bench, setBench] = useState<RateBenchmark | null>(null); // ориентир (FR-019); ошибка/пусто - скрыт
  const [extra, setExtra] = useState<number[]>([]);
  const done = useRef<string[]>([]); // шаги, уже выполненные прошлой попыткой: повтор не дублирует вложения

  useEffect(() => {
    let alive = true;
    api
      .employeeFiles(employeeKey)
      .then((r) => alive && setFiles(r.files))
      .catch((e) => alive && setFilesError(e instanceof ApiError ? e.message : "Не удалось загрузить файлы сотрудника"));
    return () => {
      alive = false;
    };
  }, [employeeKey]);

  // Ориентир нужен только там, где ставка вводится (CR-008): «от производства». Ошибка запроса окно не ломает.
  const benchWanted = !!need && rateRequired(need);
  useEffect(() => {
    if (!benchWanted) return;
    let alive = true;
    api
      .rateBenchmark()
      .then((r) => alive && setBench(r))
      .catch(() => alive && setBench(null));
    return () => {
      alive = false;
    };
  }, [benchWanted]);

  useEffect(() => {
    if (op.failure) done.current = Array.from(new Set([...done.current, ...op.failure.completed]));
  }, [op.failure]);

  const { main, extras, attachable } = resumeOptions(files ?? []);
  const required = rateRequired(need);
  const selectedResume = resumeId === "main" ? main : extras.find((f) => String(f.id) === resumeId);
  const pickable = attachable.filter((f) => f.id !== selectedResume?.id);
  const attach = pickable.filter((f) => extra.includes(f.id));
  // ставку вносим и отправляем только при «от производства»; при «Клиент» она справочная (CR-008)
  const rateEditable = !need || required;
  const failure = op.failure;
  const reauth = failure?.kind === "reauth";
  const epic = need?.epic_name || need?.epic_key;
  const label = need ? `${shortKey(needKey)}${epic ? ` · ${epic}` : ""}` : needKey;

  // undefined - ввод неверен (сообщение под полем), null - ставка не указана
  function validate(): number | null | undefined {
    if (!rateEditable) {
      setRateError("");
      return null;
    }
    const parsed = parseRate(rate);
    if (parsed === undefined) {
      setRateError(RATE_FORMAT_TEXT);
      return undefined;
    }
    if (parsed === null && required) {
      setRateError(RATE_REQUIRED_TEXT);
      return undefined;
    }
    setRateError("");
    return parsed;
  }

  function toConfirm() {
    if (validate() === undefined) return;
    op.clear();
    setStage("confirm");
  }

  async function send() {
    const parsed = validate();
    if (parsed === undefined) {
      setStage("form");
      return;
    }
    setStage("sending");
    setPartial("");
    let result: OpResult | undefined;
    const ok = await op.run(async () => {
      result = await api.assign(needKey, {
        employee_key: employeeKey,
        resume_file_id: resumeId === "main" ? null : Number(resumeId),
        extra_file_ids: attach.map((f) => f.id),
        rate_rub: parsed,
        comment: comment.trim(),
        completed: done.current,
      });
    }, "Не удалось отправить в Jira");
    if (ok && result?.rate_pending) {
      // назначение не откатывается; повтор запишет только ставку (completed = выполненные шаги)
      done.current = result.steps;
      setPartial((result.warnings ?? []).join(" ") || "Кандидат назначен, но ставка не записана в Jira.");
      setAssigned(true);
      setStage("form");
      onPartial?.();
    } else if (ok) onDone();
    else setStage("form");
  }

  async function submitRetry() {
    if (reauth && !(await op.reauth())) return;
    await send();
  }

  const sending = stage === "sending";
  const noMain = files !== null && !main;
  const formBlocked = files === null || noMain || op.busy;

  let primary;
  if (stage === "confirm") {
    primary = (
      <button type="button" className="primary" disabled={op.busy} onClick={() => void send()}>
        Подтвердить и отправить в Jira
      </button>
    );
  } else if (failure || partial) {
    primary = (
      <button type="button" className="primary" disabled={formBlocked} onClick={() => void submitRetry()}>
        {reauth ? "Войти и продолжить" : "Повторить"}
      </button>
    );
  } else {
    primary = (
      <button type="button" className="primary" disabled={formBlocked} onClick={toConfirm}>
        Назначить…
      </button>
    );
  }

  const footer = sending ? (
    <button type="button" disabled>
      Закрыть
    </button>
  ) : (
    <>
      <button type="button" onClick={stage === "confirm" ? () => setStage("form") : onClose}>
        {stage === "confirm" ? "Назад" : assigned ? "Закрыть" : "Отмена"}
      </button>
      <span className="modal__acts">{primary}</span>
    </>
  );

  const title = stage === "confirm" ? "Подтвердите назначение" : sending ? "Отправка в Jira" : "Назначить кандидата";
  const parsedRate = parseRate(rate);

  return (
    <Modal title={title} wide size="profile" footer={footer} onClose={sending ? undefined : onClose}>
      {sending && <Progress files={[selectedResume, ...attach]} done={done.current} withRate={required && !!parsedRate} />}

      {stage === "confirm" && (
        <>
          <dl className="sum">
            <dt>Потребность</dt>
            <dd>{label}</dd>
            <dt>Кандидат</dt>
            <dd>{employeeName}</dd>
            <dt>Резюме</dt>
            <dd>{selectedResume ? `${selectedResume.kind_title} — ${selectedResume.file_name}` : "—"}</dd>
            <dt>Ставка</dt>
            <dd>{rateEditable && parsedRate ? `${parsedRate.toLocaleString("ru-RU")} руб. с НДС` : rateEditable ? "не указана" : "ставка клиента, не передаётся"}</dd>
            <dt>Файлы</dt>
            <dd>{attach.length ? attach.map((f) => f.file_name).join(", ") : "—"}</dd>
            <dt>Комментарий</dt>
            <dd>{comment.trim() || "—"}</dd>
            <dt>Исполнитель</dt>
            <dd>Вы ({user?.display_name || user?.login})</dd>
          </dl>
          <p className="hint">
            Файлы прикрепляются как есть, без обезличивания. Повторное назначение заменит предыдущее. Файлы и комментарий статус не меняют.
          </p>
          <div className="box">
            <strong>Статус изменится автоматически.</strong>
            <div className="hint">
              После отправки Jira сама переведёт потребность в «Кандидат назначен». Вручную статус менять не нужно.
            </div>
          </div>
        </>
      )}

      {stage === "form" && (
        <div className="assign-form">
          {partial && !failure && (
            <section className="box box--warn" role="alert">
              <strong>Кандидат назначен, ставка не записана</strong>
              <p>{partial}</p>
            </section>
          )}
          <JiraFailure failure={failure} what="в назначении" pwd={op.pwd} loginError={op.loginError} onEnter={() => void submitRetry()} />
          <p className="ctx">
            Потребность {label} · Кандидат: <strong>{employeeName}</strong>
          </p>
          {filesError && (
            <p className="error" role="alert">
              {filesError}
            </p>
          )}
          {files === null && !filesError && <p className="muted">Загрузка…</p>}
          {noMain && (
            <p className="error" role="alert">
              У сотрудника нет Основного CV: загрузите его в разделе «Резюме».
            </p>
          )}
          <div className="field">
            <label htmlFor="assign-resume">Резюме</label>
            <select id="assign-resume" value={resumeId} disabled={files === null || assigned} onChange={(e) => setResumeId(e.target.value)}>
              <option value="main">{main ? fileLabel(main) : "Основное CV"}</option>
              {extras.map((f) => (
                <option key={f.id} value={f.id}>
                  {fileLabel(f)}
                </option>
              ))}
            </select>
          </div>
          <div className={rateError ? "field field--err" : "field"}>
            <label htmlFor="assign-rate">
              Ставка, руб. с НДС{required && <span className="error">&nbsp;*</span>}
            </label>
            <input
              id="assign-rate"
              type="text"
              inputMode="decimal"
              placeholder={rateEditable ? "например, 3 800" : undefined}
              value={rate}
              disabled={!rateEditable}
              aria-invalid={rateError ? true : undefined}
              aria-describedby="assign-rate-hint"
              onChange={(e) => {
                setRate(e.target.value);
                setRateError("");
              }}
            />
            <div className="hint" id="assign-rate-hint">
              {need ? `В потребности: ${rateText(need)}.` : ""}
              {!rateEditable ? " Ставку предлагает клиент — здесь не вводится." : ""}
              {rateEditable && benchmarkText(bench, need?.rate) ? ` ${benchmarkText(bench, need?.rate)}` : ""}
            </div>
            {rateError && (
              <div className="error" role="alert">
                {rateError}
              </div>
            )}
          </div>
          <div className="field">
            <label htmlFor="assign-comment">Комментарий (уйдёт комментарием в потребность)</label>
            <textarea id="assign-comment" rows={3} value={comment} disabled={assigned} maxLength={32000} onChange={(e) => setComment(e.target.value)} />
          </div>
          <fieldset className="field files-pick">
            <legend>Файлы (из БД сотрудника)</legend>
            {pickable.length === 0 && <span className="muted">Дополнительных файлов нет</span>}
            {pickable.map((f) => (
              <label key={f.id} className="chk">
                <input
                  type="checkbox"
                  checked={extra.includes(f.id)}
                  disabled={assigned}
                  onChange={(e) => setExtra((x) => (e.target.checked ? [...x, f.id] : x.filter((i) => i !== f.id)))}
                />
                {f.file_name} <span className="muted">{f.kind_title}</span>
              </label>
            ))}
            <button type="button" className="btn-secondary" disabled title="Диалог появится в задаче KON-80">
              CV по шаблону клиента…
            </button>
          </fieldset>
        </div>
      )}
    </Modal>
  );
}

// Прогресс по этапам. Backend отвечает одним запросом, поэтому отмечены шаги прошлых попыток, идущий - выделен.
function Progress({ files, done, withRate }: { files: (FileOut | undefined)[]; done: string[]; withRate: boolean }) {
  const steps: { id: string; text: string }[] = [
    { id: STEP_FIELDS, text: "Профиль сотрудника, исполнитель" },
    ...files.filter((f): f is FileOut => !!f).map((f) => ({ id: attachStep(f.id), text: `Вложение: ${f.file_name}` })),
    { id: STEP_TRANSITION, text: "Комментарий и статус «Кандидат назначен» (выставляет Jira)" },
    ...(withRate ? [{ id: STEP_RATE, text: "Ставка" }] : []),
  ];
  const current = steps.findIndex((s) => !done.includes(s.id));
  return (
    <div role="status" aria-live="polite">
      <p>
        <strong>Передаём в Jira…</strong>
      </p>
      <div className="prog" aria-hidden="true">
        <i />
      </div>
      <ul className="steps-list">
        {steps.map((s, i) => (
          <li key={s.id}>
            {done.includes(s.id) ? "✓ " : i === current ? "→ " : ""}
            {i === current ? <strong>{s.text}</strong> : s.text}
          </li>
        ))}
      </ul>
      <p className="hint">Не закрывайте окно. Обычно до 10 секунд.</p>
    </div>
  );
}
