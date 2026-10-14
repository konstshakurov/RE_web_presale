import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { FileOut } from "../../api/types";
import { formatDateTime } from "../../lib/needs";
import ActIcon from "../ActIcon";
import Modal from "../Modal";

interface Props {
  employeeKey: string;
  employeeName: string; // для aria-label действий
  files: FileOut[]; // актуальные файлы сотрудника
  canAdd: boolean; // лимиты загружены
  onAdd: () => void;
  onView: (fileId: number | undefined) => void; // undefined - актуальное основное CV
  onChanged: () => void; // файлы изменились: родитель перечитывает список
}

// Столбец «Файлы» (FR-022): список, «Добавить», скачивание и удаление (FR-040), архив основного CV (FR-039).
export default function FilesCell({ employeeKey, employeeName, files, canAdd, onAdd, onView, onChanged }: Props) {
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archive, setArchive] = useState<FileOut[] | null>(null);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<FileOut | null>(null);
  const [busy, setBusy] = useState(false);

  const loadArchive = useCallback(async () => {
    try {
      setArchive((await api.employeeFiles(employeeKey)).archive);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось загрузить архив");
    }
  }, [employeeKey]);

  // Архив читается при раскрытии и после любого изменения списка файлов
  useEffect(() => {
    if (archiveOpen) void loadArchive();
  }, [archiveOpen, files, loadArchive]);

  async function run(action: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    setError("");
    try {
      await action();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : fallback);
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  function row(f: FileOut) {
    const viewable = f.extraction_status === "ok" && (f.kind === "main_cv" || f.kind === "extra_cv");
    return (
      <li key={f.id} className="file">
        <span className="file__kind">{f.kind_title}</span>{" "}
        <span className="file__name">{f.file_name}</span>
        <div className="muted file__meta">
          {formatDateTime(f.uploaded_at)}
          {f.is_archived && f.archived_at ? `, в архиве с ${formatDateTime(f.archived_at)}` : ""}
        </div>
        {f.extraction_status === "error" && (
          <div className="error file__meta">ошибка извлечения: {f.extraction_message || "текст не извлечён"}</div>
        )}
        <div className="file__actions">
          <a href={api.downloadUrl(f.id)} download>
            Скачать
          </a>
          {viewable && (
            <button type="button" className="link" onClick={() => onView(f.is_archived || f.kind !== "main_cv" ? f.id : undefined)}>
              Просмотр
            </button>
          )}
          {f.can_restore && (
            <button type="button" className="link" disabled={busy} onClick={() => void run(async () => {
              await api.restoreFile(f.id);
            }, "Не удалось восстановить версию")}>
              Восстановить
            </button>
          )}
          {f.can_delete && (
            <button type="button" className="link link--danger" disabled={busy} onClick={() => setConfirm(f)}>
              Удалить
            </button>
          )}
        </div>
      </li>
    );
  }

  return (
    <div className="files">
      {files.length > 0 && <ul className="files__list">{files.map(row)}</ul>}
      {!files.some((f) => f.kind === "main_cv") && <p className="muted files__none">нет основного CV</p>}
      <button type="button" className="act" disabled={!canAdd} onClick={onAdd} aria-label={`Добавить файл: ${employeeName}`}>
        <ActIcon name="plus" size={16} />
        <span>Добавить</span>
      </button>
      <button
        type="button"
        className="link"
        aria-expanded={archiveOpen}
        onClick={() => setArchiveOpen(!archiveOpen)}
      >
        {archiveOpen ? "Скрыть архив" : "Архив основного CV"}
      </button>
      {archiveOpen && (
        <div className="files__archive">
          {archive === null ? (
            <p className="muted">Загрузка…</p>
          ) : archive.length === 0 ? (
            <p className="muted">Архив пуст</p>
          ) : (
            <ul className="files__list">{archive.map(row)}</ul>
          )}
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {confirm && (
        <Modal title="Удалить файл?" onClose={busy ? undefined : () => setConfirm(null)}>
          <p>«{confirm.file_name}» будет удалён и перестанет участвовать в сопоставлении и назначении.</p>
          <div className="modal__actions">
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => void run(() => api.deleteFile(confirm.id), "Не удалось удалить файл")}
            >
              Удалить
            </button>
            <button type="button" onClick={() => setConfirm(null)} disabled={busy}>
              Отмена
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
