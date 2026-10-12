import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { CvLimits, EmployeeOut, FileOut } from "../api/types";
import CvViewer from "../components/CvViewer";
import EmployeeSyncBar from "../components/EmployeeSyncBar";
import FilesCell from "../components/resumes/FilesCell";
import UploadDialog from "../components/resumes/UploadDialog";
import { EMPLOYEE_STATUSES, groupFiles, matchesResume, statusOptions } from "../lib/employees";
import { formatDateTime } from "../lib/needs";
import { useEmployees } from "../lib/useEmployees";

const LLM_STUB = "Будет доступно после подключения LLM (KON-37, KON-38)";

function NoteCell({ emp }: { emp: EmployeeOut }) {
  return (
    <div>
      {emp.note ? <div className="note">{emp.note}</div> : <span className="muted">нет заметки</span>}
      {emp.note && (emp.note_author || emp.note_updated_at) && (
        <div className="muted">
          {[emp.note_author, formatDateTime(emp.note_updated_at)].filter(Boolean).join(", ")}
        </div>
      )}
      {/* Редактирование (FR-033): в API нет метода сохранения заметки - заглушка до отдельной backend-задачи */}
      <button type="button" disabled title="Сохранение заметки пока недоступно: нет метода API">
        Изменить
      </button>
    </div>
  );
}

export default function ResumesPage() {
  const [params] = useSearchParams();
  const emps = useEmployees();
  const [files, setFiles] = useState<FileOut[] | null>(null);
  const [filesError, setFilesError] = useState("");
  const [limits, setLimits] = useState<CvLimits | null>(null);
  const [limitsError, setLimitsError] = useState("");
  const initialQuery = params.get("q") ?? "";
  const [draft, setDraft] = useState({ q: initialQuery, status: "" });
  const [applied, setApplied] = useState({ q: initialQuery, status: "" });
  const [uploadFor, setUploadFor] = useState<EmployeeOut | null>(null);
  const [view, setView] = useState<{ key: string; fileId?: number } | null>(null);
  const [notice, setNotice] = useState("");

  const loadFiles = useCallback(async () => {
    try {
      setFiles((await api.cvFiles()).items);
      setFilesError("");
    } catch (e) {
      setFilesError(e instanceof ApiError ? e.message : "Не удалось загрузить файлы");
    }
  }, []);

  useEffect(() => {
    void loadFiles();
    api
      .cvLimits()
      .then(setLimits)
      .catch((e) => setLimitsError(e instanceof ApiError ? e.message : "Не удалось загрузить лимиты файлов"));
  }, [loadFiles]);

  const byEmployee = useMemo(() => groupFiles(files ?? []), [files]);
  const options = statusOptions(emps.counts);
  const rows = (emps.items ?? []).filter((e) => matchesResume(e, byEmployee.get(e.jira_key) ?? [], applied.q, applied.status));
  const mainCount = (files ?? []).filter((f) => f.kind === "main_cv").length;

  function onApply(e: FormEvent) {
    e.preventDefault();
    setApplied({ q: draft.q.trim(), status: draft.status });
  }

  function onReset() {
    setDraft({ q: "", status: "" });
    setApplied({ q: "", status: "" });
  }

  const error = emps.error || filesError;

  return (
    <section>
      <h1>Резюме</h1>

      <form className="resume-filters" onSubmit={onApply} role="search">
        <label>
          Поиск
          <input
            type="search"
            placeholder="ФИО, ключ или имя файла"
            value={draft.q}
            maxLength={200}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
          />
        </label>
        <label>
          Статус сотрудника
          <select value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>
            <option value="">— все —</option>
            {(options.length ? options : EMPLOYEE_STATUSES.map((s) => ({ status: s, count: 0 }))).map((o) => (
              <option key={o.status} value={o.status}>
                {o.status}
              </option>
            ))}
          </select>
        </label>
        <label>
          Технология БА (можно несколько)
          <input type="text" disabled placeholder="Выбор технологий" title={LLM_STUB} />
        </label>
        <label className="resume-filters__llm">
          Поиск по смыслу (LLM)
          <textarea disabled rows={2} placeholder="Запрос на естественном языке" title={LLM_STUB} />
        </label>
        <div className="resume-filters__actions">
          <button type="submit" className="primary">
            Применить
          </button>
          <button type="button" onClick={onReset}>
            Сбросить
          </button>
        </div>
      </form>
      <p className="muted">
        Технологии и поиск по смыслу появятся после выделения технологий из основного CV через LLM (KON-37, KON-38).
        Поиск по тексту внутри CV в этом разделе пока недоступен: ищет по ФИО, ключу и имени файла.
      </p>

      <EmployeeSyncBar
        sync={emps.sync}
        refreshing={emps.refreshing}
        onRefresh={() => void emps.refresh()}
        error={emps.refreshError}
        info={emps.info}
      />
      {limitsError && (
        <p className="error" role="alert">
          {limitsError}
        </p>
      )}
      {notice && <p className="muted">{notice}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {emps.items === null && !error && <p className="muted">Загрузка…</p>}

      {emps.items && (
        <>
          <p className="muted">
            Сотрудников: {emps.items.length}, с основным CV: {mainCount}, показано: {rows.length}
          </p>
          {rows.length === 0 ? (
            <div className="empty">
              <p>Нет данных</p>
            </div>
          ) : (
            <table className="needs resumes">
              <thead>
                <tr>
                  <th>Сотрудник</th>
                  <th>Файлы</th>
                  <th>Содержание</th>
                  <th>Описание</th>
                  <th>Заметка</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((e) => {
                  const list = byEmployee.get(e.jira_key) ?? [];
                  const main = list.find((f) => f.kind === "main_cv");
                  return (
                    <tr key={e.jira_key}>
                      <td>
                        {e.jira_url ? (
                          <a href={e.jira_url} target="_blank" rel="noreferrer">
                            {e.full_name}
                          </a>
                        ) : (
                          e.full_name
                        )}
                        <div className="muted">{e.jira_key}</div>
                        <span className="status">{e.status}</span>
                      </td>
                      <td>
                        <FilesCell
                          employeeKey={e.jira_key}
                          files={list}
                          canAdd={limits !== null}
                          onAdd={() => setUploadFor(e)}
                          onView={(fileId) => setView({ key: e.jira_key, fileId })}
                          onChanged={() => void loadFiles()}
                        />
                      </td>
                      <td>
                        {!main ? (
                          <span className="muted">нет основного CV</span>
                        ) : main.extraction_status === "ok" ? (
                          <button type="button" onClick={() => setView({ key: e.jira_key })}>
                            Показать текст
                          </button>
                        ) : (
                          <span className="error">
                            ошибка: {main.extraction_message || "текст не извлечён"}
                          </span>
                        )}
                      </td>
                      <td>
                        {/* Технологии из основного CV выделяет LLM (FR-027) - KON-37 */}
                        <span className="muted" title={LLM_STUB}>
                          —
                        </span>
                      </td>
                      <td>
                        <NoteCell emp={e} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </>
      )}

      {uploadFor && limits && (
        <UploadDialog
          employeeKey={uploadFor.jira_key}
          employeeName={uploadFor.full_name}
          hasMainCv={(byEmployee.get(uploadFor.jira_key) ?? []).some((f) => f.kind === "main_cv")}
          limits={limits}
          onClose={() => setUploadFor(null)}
          onUploaded={(r) => {
            setUploadFor(null);
            setNotice(
              r.replaced_file_id
                ? `Файл «${r.file.file_name}» загружен как основное CV, прежняя версия ушла в архив`
                : `Файл «${r.file.file_name}» загружен`,
            );
            void loadFiles();
          }}
        />
      )}
      {view && <CvViewer employeeKey={view.key} fileId={view.fileId} onClose={() => setView(null)} />}
    </section>
  );
}
