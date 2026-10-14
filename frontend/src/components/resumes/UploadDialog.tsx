import { useState } from "react";
import type { FormEvent } from "react";

import { ApiError, api } from "../../api/client";
import type { CvLimits, UploadResult } from "../../api/types";
import { allowedExtensions, uploadKinds, validateUpload } from "../../lib/employees";
import Modal from "../Modal";

interface Props {
  employeeKey: string;
  employeeName: string;
  hasMainCv: boolean;
  limits: CvLimits;
  onClose: () => void;
  onUploaded: (r: UploadResult) => void;
}

// Окно загрузки ОДНОГО файла с обязательным типом (FR-022). Форматы и лимит - из GET /cv/limits.
export default function UploadDialog({ employeeKey, employeeName, hasMainCv, limits, onClose, onUploaded }: Props) {
  const [kind, setKind] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const kinds = uploadKinds(limits);
  const allowed = kind ? allowedExtensions(limits, kind) : [];

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!kind || !file) return;
    const invalid = validateUpload(file, kind, limits);
    if (invalid) {
      setError(invalid); // недопустимый файл не отправляется и в карточку не попадает
      return;
    }
    setBusy(true);
    setError("");
    try {
      onUploaded(await api.uploadFile(employeeKey, kind, file));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить файл");
      setBusy(false);
    }
  }

  return (
    <Modal title="Добавить файл" onClose={busy ? undefined : onClose}>
      <form className="modal__form" onSubmit={(e) => void onSubmit(e)}>
        <p className="muted">
          {employeeName} ({employeeKey})
        </p>
        <label>
          Тип файла
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setError("");
            }}
          >
            <option value="">— выберите тип —</option>
            {kinds.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Файл
          <input
            type="file"
            accept={allowed.map((x) => `.${x}`).join(",") || undefined}
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setError("");
            }}
          />
        </label>
        <p className="muted">
          {allowed.length > 0 ? `Допустимые форматы: ${allowed.map((x) => `.${x}`).join(", ")}. ` : ""}
          Не более {limits.max_file_mb} МБ, один файл за раз.
        </p>
        {kind === "main_cv" && hasMainCv && (
          <p className="muted">Текущее основное CV будет заменено новым и сохранится в архиве (последние 3 версии).</p>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="modal__actions">
          <button type="submit" className="primary" disabled={!kind || !file || busy}>
            {busy ? "Загрузка…" : "Загрузить"}
          </button>
          <button type="button" onClick={onClose} disabled={busy}>
            Отмена
          </button>
        </div>
      </form>
    </Modal>
  );
}
