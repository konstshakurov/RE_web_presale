import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import type { CvTplContext, CvTplResult, LlmTask, ProviderPublic } from "../api/types";
import { useJiraOp } from "../lib/useJiraOp";
import JiraFailure from "./JiraFailure";
import Modal from "./Modal";

interface Props {
  needKey: string;
  employeeKey: string;
  employeeName: string;
  onClose: () => void;
  onSaved: (fileId: number) => void; // файл сохранён: родитель перечитывает файлы и может выбрать его
}

type Step = "files" | "generating" | "result";
const LS_PROVIDER_KEY = "cvtpl.providerId";
const FINAL = new Set(["done", "failed", "timeout", "cancelled"]);

function lsGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // окружение без storage (тесты, приватный режим)
  }
}

function lsSet(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* не критично: модель выбирается каждый раз вручную */
  }
}

function fmtDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString("ru-RU");
}

function parseResult(raw: string | null | undefined): CvTplResult {
  // Ответ шлюза - JSON-объект cv/recommendations/letter (промпт собирает backend)
  const head = (raw ?? "").indexOf("{");
  let data: unknown = null;
  if (head >= 0) {
    try {
      data = JSON.parse((raw ?? "").slice(head));
    } catch {
      data = null;
    }
  }
  const out = data as CvTplResult | null;
  if (!out || typeof out.cv !== "string" || !out.cv.trim() ||
      !Array.isArray(out.recommendations) || typeof out.letter !== "string" || !out.letter.trim()) {
    throw new Error("Ответ LLM не разобрать (нет раздела CV или письма). Повторите запрос.");
  }
  return out;
}

// Диалог «CV по шаблону клиента» (FR-017; вариант A, design/KON-80/decision.md):
// подтверждение файлов и модели -> генерация (опрос задачи шлюза) -> результат
// с предпросмотром и письмом; публикация письма - только после явного апрува.
export default function CvTemplateDialog({ needKey, employeeKey, employeeName, onClose, onSaved }: Props) {
  const op = useJiraOp();
  const [ctx, setCtx] = useState<CvTplContext | null>(null);
  const [ctxError, setCtxError] = useState("");
  const [step, setStep] = useState<Step>("files");
  const [providers, setProviders] = useState<ProviderPublic[]>([]);
  const [providerId, setProviderId] = useState<number | null>(null);
  const [template, setTemplate] = useState<File | null>(null);
  const [useTemplate, setUseTemplate] = useState(false);
  const [confirmMain, setConfirmMain] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [task, setTask] = useState<LlmTask | null>(null);
  const [result, setResult] = useState<CvTplResult | null>(null);
  const [letter, setLetter] = useState("");
  const [letterConfirmed, setLetterConfirmed] = useState(false);
  const [published, setPublished] = useState(false);
  const [savedFile, setSavedFile] = useState<{ id: number; name: string } | null>(null);
  const [err, setErr] = useState("");
  const [confirming, setConfirming] = useState(false); // FR-023: внешний провайдер - явное подтверждение отправки
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let alive = true;
    api.cvtplContext(needKey, employeeKey)
      .then((c) => alive && setCtx(c))
      .catch((e) => alive && setCtxError(e instanceof ApiError ? e.message : "Не удалось загрузить данные"));
    api.publicProviders()
      .then((ps) => {
        if (!alive) return;
        const sel = lsGet(LS_PROVIDER_KEY);
        const found = ps.find((p) => String(p.id) === sel);
        setProviders(ps);
        setProviderId(found ? found.id : (ps.find((p) => p.is_default)?.id ?? ps[0]?.id ?? null));
      })
      .catch(() => alive && setProviders([]));
    return () => {
      alive = false;
    };
  }, [needKey, employeeKey]);

  // Генерация: опрос задачи шлюза; итог разбирается здесь (ADR-007)
  useEffect(() => {
    if (step !== "generating" || !taskId) return;
    let alive = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const t = await api.llmTask(taskId);
        if (!alive) return;
        setTask(t);
        if (t.requires_confirmation && t.status === "awaiting_confirmation") {
          // внешний провайдер: подтверждение обезличенного предпросмотра (FR-023)
          const ok = await api.confirmLlmTask(taskId);
          if (!alive) return;
          setTask(ok);
          timer = window.setTimeout(tick, 3000);
          return;
        }
        if (FINAL.has(t.status)) {
          if (t.status === "done") {
            setResult(parseResult(t.result));
            setLetter(parseLetter(t.result));
            setStep("result");
          } else {
            setErr(t.error_message || "Генерация не удалась. Повторите запрос.");
            setStep("files");
          }
          return;
        }
        timer = window.setTimeout(tick, 3000);
      } catch (e) {
        if (!alive) return;
        setErr(e instanceof Error ? e.message : "Не удалось получить состояние задачи");
        setStep("files");
      }
    };
    void tick();
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
    function parseLetter(raw: string | null | undefined): string {
      try {
        return parseResult(raw).letter;
      } catch {
        return "";
      }
    }
  }, [taskId, step]);

  const templateOk = !!template && useTemplate;
  const mainCvOk = !!ctx?.main_cv && confirmMain;
  const canGenerate = templateOk && mainCvOk && providerId !== null && step === "files";

  async function generate() {
    if (!template || !ctx?.main_cv || providerId === null) {
      console.info("[cvtpl-debug]", { template: !!template, main: !!ctx?.main_cv, providerId, step });
      return;
    }
    setErr("");
    try {
      const out = await api.cvtplGenerate(template, {
        needKey,
        employeeKey,
        mainCvFileId: ctx.main_cv.id,
        providerId,
      });
      setTaskId(out.task_id);
      lsSet(LS_PROVIDER_KEY, String(providerId));
      // FR-023: для внешнего провайдера открытый предпросмотр обезличенного текста
      // уже получен пользователем из окна «Назначить» - здесь подтверждение явное
      setConfirming(out.requires_confirmation);
      setStep(out.requires_confirmation ? "confirm" : "generating");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Не удалось поставить задачу генерации");
    }
  }

  async function confirmExternal() {
    if (!taskId || !confirming) return;
    try {
      const t = await api.confirmLlmTask(taskId);
      setTask(t);
      setConfirming(false);
      setStep("generating");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Не удалось подтвердить отправку во внешнюю LLM");
    }
  }

  async function saveCv() {
    if (!task || savedFile) return;
    try {
      const saved = await api.cvtplSave(task.id, { need_key: needKey, employee_key: employeeKey });
      setSavedFile({ id: saved.file_id, name: saved.file_name });
      onSaved(saved.file_id);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Не удалось сохранить CV");
    }
  }

  async function publish() {
    if (!task || published) return;
    const ok = await op.run(async () => {
      await api.cvtplPublish(task.id, { need_key: needKey, letter, confirmed: true });
    }, "Не удалось опубликовать письмо в Jira");
    if (ok) {
      setPublished(true);
      op.clear();
    }
  }

  const client = ctx?.client_name || ctx?.client_epic_key || "—";

  return (
    <Modal
      wide
      title="CV по шаблону клиента"
      meta={<span className="modal__ctx">{`Потребность ${needKey} · Клиент ${client}`}</span>}
      onClose={onClose}
      closeDisabled={step === "generating"}
      footer={
        step === "files" ? (
          <>
            <span className="footnote">
              Результат сохранится с типом «CV по шаблону клиента» и клиентом {client}.
            </span>
            <span style={{ flex: 1 }} />
            <button type="button" className="btn" onClick={onClose}>Отмена</button>
            <button type="button" className="btn primary" disabled={!canGenerate} onClick={generate}>
              Сгенерировать
            </button>
          </>
        ) : step === "confirm" ? (
          <>
            <span className="footnote">Текст уходит во внешнюю LLM обезличенным (FR-023).</span>
            <span style={{ flex: 1 }} />
            <button type="button" className="btn" onClick={() => {
              setConfirming(false);
              onClose();
            }}>
              Отмена
            </button>
            <button type="button" className="btn primary" onClick={confirmExternal}>
              Подтвердить и отправить
            </button>
          </>
        ) : step === "generating" ? (
          <span className="footnote">Закрыть окно во время генерации нельзя.</span>
        ) : (
          <>
            <span className="footnote">
              {published
                ? "CV сохранён и письмо опубликовано."
                : "Сохраните CV и (по желанию) опубликуйте письмо после апрува."}
            </span>
            <span style={{ flex: 1 }} />
            <button type="button" className="btn" onClick={onClose}>Готово</button>
            <button type="button" className="btn primary" onClick={saveCv} disabled={!!savedFile}>
              Сохранить CV
            </button>
          </>
        )
      }
    >
      {err && <p className="box box--err" role="alert">{err}</p>}
      {!ctx && !ctxError && <p className="muted">Загрузка…</p>}

      {step === "files" && ctx && (
        <>
          {ctx.repeat && (
            <div className="box box--warn" role="alert">
              Для пары «{employeeName} + {client}» уже есть CV по шаблону
              {ctx.repeat.uploaded_at ? ` от ${fmtDate(ctx.repeat.uploaded_at)}` : ""}.
              Новый результат добавится в историю, прежний останется.
            </div>
          )}
          {!ctx.main_cv && (
            <div className="box box--err" role="alert">
              У сотрудника нет Основного CV — генерация невозможна. Загрузите Основное CV в карточке сотрудника.
            </div>
          )}
          <h4>Входы</h4>
          <div className="cvtpl__file">
            <div className="cvtpl__picked">
              <div>
                <div className="name">{template ? template.name : "Файл шаблона клиента"}</div>
                <div className="meta">
                  {template
                    ? "Файл выбран — подтвердите использование"
                    : "Файл шаблона из «Файлов» клиента; выбор вложения Jira — отдельно"}
                </div>
              </div>
              <label className="cvtpl__chk">
                <input
                  type="file"
                  accept=".doc,.docx"
                  aria-label="Файл шаблона клиента"
                  ref={fileInput}
                  onChange={(e) => {
                    const f = e.target.files?.[0] ?? null;
                    setTemplate(f);
                    setUseTemplate(false);
                  }}
                />
              </label>
            </div>
            {template && (
              <label className="cvtpl__chk">
                <input
                  type="checkbox"
                  checked={useTemplate}
                  onChange={(e) => setUseTemplate(e.target.checked)}
                />
                Использую как шаблон
              </label>
            )}
          </div>
          {ctx.main_cv && (
            <div className="cvtpl__file">
              <div className="cvtpl__picked">
                <div>
                  <div className="name">{ctx.main_cv.file_name}</div>
                  <div className="meta">Основное CV сотрудника · обновлён {fmtDate(ctx.main_cv.uploaded_at)}</div>
                </div>
                <label className="cvtpl__chk">
                  <input
                    type="checkbox"
                    checked={confirmMain}
                    onChange={(e) => setConfirmMain(e.target.checked)}
                  />
                  Подтверждаю вход
                </label>
              </div>
            </div>
          )}
          <div className="cvtpl__row">
            <span className="footnote">Модель:</span>
            <select
              value={providerId ?? ""}
              onChange={(e) => setProviderId(e.target.value === "" ? null : Number(e.target.value))}
            >
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}{p.is_external ? " (внешняя, с обезличиванием)" : ""}
                </option>
              ))}
            </select>
          </div>
        </>
      )}

      {step === "generating" && (
        <div className="cvtpl__progress" role="status">
          <span className="spinner" aria-hidden="true" />
          <span>
            <strong>Генерация…</strong>
            {task?.provider_name ? ` · модель ${task.provider_name}` : ""}
          </span>
        </div>
      )}

      {step === "confirm" && (
        <div className="cvtpl__progress" role="status">
          <span>
            <strong>Отправка во внешнюю LLM</strong> — текст будет обезличен (FR-023).
            Подтвердите отправку.
          </span>
        </div>
      )}

      {step === "result" && result && (
        <div className="cvtpl__result">
          <div className="cvtpl__prev" aria-label="Предпросмотр CV">{result.cv}</div>
          <div className="cvtpl__side">
            <h4>Рекомендации по адаптации</h4>
            <ul>
              {result.recommendations.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
            <h4>Сопроводительное письмо</h4>
            <textarea value={letter} onChange={(e) => setLetter(e.target.value)} rows={6} />
            <label className="cvtpl__chk">
              <input
                type="checkbox"
                checked={published ? true : letterConfirmed}
                disabled={published}
                onChange={(e) => setLetterConfirmed(e.target.checked)}
              />
              Я проверил(а) письмо, публикую как есть
            </label>
            <button type="button" className="btn primary" disabled={!letterConfirmed || published}
              onClick={publish}>
              {published ? "Опубликовано" : "Опубликовать письмо в Jira"}
            </button>
            <JiraFailure
              failure={op.failure}
              what="в публикации письма"
              pwd={op.pwd}
              loginError={op.loginError}
              onEnter={() => void op.reauth().then((r) => r && publish())}
            />
            {savedFile && (
              <p className="footnote">
                Файл сохранён: {savedFile.name} (id {savedFile.id}) — доступен в «Файлы» сотрудника
                и в окне «Назначить».
              </p>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
