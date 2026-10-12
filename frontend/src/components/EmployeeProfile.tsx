import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { EmployeeOut, EmployeeProfileData, FileOut, NeedOut } from "../api/types";
import { formatSize } from "../lib/employees";
import { formatDateTime, formatRate } from "../lib/needs";
import Modal from "./Modal";

interface Props {
  employee: EmployeeOut;
  onClose: () => void;
  onViewCv: () => void;
}

// Профиль сотрудника (FR-003): данные карточки Jira, ссылки на все CV, история отправок (FR-016).
// Разбор корпоративного CV (BR-007: стек, отрасли, проекты, годы, языки) - из GET /resume/employees/{key}/profile.
const SECTION_TITLES: [string, string][] = [
  ["profile", "Профиль"],
  ["stack", "Основной стек"],
  ["industries", "Отрасли"],
  ["skills", "Ключевые навыки"],
  ["languages", "Языки"],
  ["education", "Образование и сертификаты"],
  ["projects", "Проекты"],
];

// Разделы корпоративного CV по заголовкам; годы опыта - только если CV называет их явно.
function CvSections({ profile }: { profile: EmployeeProfileData | null | undefined }) {
  if (profile === undefined) return <span className="muted">Загрузка…</span>;
  if (profile === null) return <span className="muted">Профиль из CV недоступен: нет основного CV или он не разобран</span>;
  const sections = SECTION_TITLES.filter(([k]) => profile.sections?.[k]);
  const years = profile.experience_years;
  if (sections.length === 0 && profile.skills.length === 0 && years == null) {
    return <span className="muted">В основном CV не найдены разделы корпоративного формата</span>;
  }
  return (
    <div className="cv-sections">
      {years != null && <p>Опыт: {years} лет (по данным CV)</p>}
      {profile.skills.length > 0 && <p>Технологии: {profile.skills.join(", ")}</p>}
      {sections.map(([k, title]) => (
        <div key={k}>
          <strong>{title}</strong>
          <div className="pre">{profile.sections[k]}</div>
        </div>
      ))}
    </div>
  );
}

export default function EmployeeProfile({ employee: e, onClose, onViewCv }: Props) {
  const [files, setFiles] = useState<FileOut[] | null>(null);
  const [needs, setNeeds] = useState<NeedOut[] | null>(null);
  const [error, setError] = useState("");
  const [profile, setProfile] = useState<EmployeeProfileData | null | undefined>(undefined); // null - профиля нет

  useEffect(() => {
    let alive = true;
    const fail = (err: unknown) => alive && setError(err instanceof ApiError ? err.message : "Не удалось загрузить данные профиля");
    api
      .employeeFiles(e.jira_key)
      .then((r) => alive && setFiles(r.files))
      .catch(fail);
    api
      .employeeProfile(e.jira_key)
      .then((p) => alive && setProfile(p))
      .catch(() => alive && setProfile(null)); // нет профиля (404) или недоступен - показываем пояснение
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
        <dd>
          <CvSections profile={profile} />
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
