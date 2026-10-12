import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { EmployeeOut, FileOut, NeedOut } from "../api/types";
import { formatSize } from "../lib/employees";
import { formatDateTime, formatRate } from "../lib/needs";
import Modal from "./Modal";

interface Props {
  employee: EmployeeOut;
  onClose: () => void;
  onViewCv: () => void;
}

// Профиль сотрудника (FR-003): данные карточки Jira, ссылки на все CV, история отправок (FR-016).
// Разбор корпоративного CV (стек, отрасли, проекты, годы, языки) - заглушка: в API его пока нет.
export default function EmployeeProfile({ employee: e, onClose, onViewCv }: Props) {
  const [files, setFiles] = useState<FileOut[] | null>(null);
  const [needs, setNeeds] = useState<NeedOut[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const fail = (err: unknown) => alive && setError(err instanceof ApiError ? err.message : "Не удалось загрузить данные профиля");
    api
      .employeeFiles(e.jira_key)
      .then((r) => alive && setFiles(r.files))
      .catch(fail);
    // История из локального кэша потребностей: потребности, где исполнитель - этот сотрудник
    api
      .needs({ group: "all", limit: 500, offset: 0 })
      .then((p) => alive && setNeeds(p.items.filter((n) => n.employee_key === e.jira_key)))
      .catch(fail);
    return () => {
      alive = false;
    };
  }, [e.jira_key]);

  return (
    <Modal title={`${e.full_name} (${e.jira_key})`} wide>
      <dl className="profile">
        <dt>Статус</dt>
        <dd>
          <span className="status">{e.status}</span>{" "}
          <span className="muted">{e.assignable ? "доступен для назначения" : "на потребность не назначается"}</span>
        </dd>
        <dt>Отпуска</dt>
        <dd>{e.vacation_plan || <span className="muted">не указаны</span>}</dd>
        <dt>Подробнее об опыте</dt>
        <dd className="pre">{e.extra_experience || <span className="muted">не указано</span>}</dd>
        <dt>Данные из CV</dt>
        <dd className="muted">
          Основной стек, отрасли, проекты, годы опыта и языки появятся после разбора основного CV (этап 1, задача на
          backend).
        </dd>
        <dt>Файлы</dt>
        <dd>
          {files === null ? (
            <span className="muted">Загрузка…</span>
          ) : files.length === 0 ? (
            <span className="muted">файлов нет</span>
          ) : (
            <ul className="files__list">
              {files.map((f) => (
                <li key={f.id}>
                  <a href={api.downloadUrl(f.id)} download>
                    {f.file_name}
                  </a>{" "}
                  <span className="muted">
                    {f.kind_title}, {formatSize(f.size_bytes)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </dd>
        <dt>История отправок</dt>
        <dd>
          {needs === null ? (
            <span className="muted">Загрузка…</span>
          ) : needs.length === 0 ? (
            <span className="muted">нет потребностей с этим исполнителем</span>
          ) : (
            <ul className="files__list">
              {needs.map((n) => {
                const rate = formatRate(n.rate);
                return (
                  <li key={n.jira_key}>
                    {n.jira_url ? (
                      <a href={n.jira_url} target="_blank" rel="noreferrer">
                        {n.jira_key}
                      </a>
                    ) : (
                      n.jira_key
                    )}{" "}
                    {n.title} <span className="status">{n.status}</span>{" "}
                    <span className="muted">
                      {rate.main}
                      {rate.vat ? ` ${rate.vat}` : ""}, {formatDateTime(n.jira_created_at)}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </dd>
      </dl>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="modal__actions">
        <button type="button" onClick={onViewCv}>
          Резюме
        </button>
        <Link className="button" to={`/resumes?q=${encodeURIComponent(e.jira_key)}`}>
          Файлы в разделе «Резюме»
        </Link>
        <button type="button" onClick={onClose}>
          Закрыть
        </button>
      </div>
    </Modal>
  );
}
