import { useState } from "react";
import { Link } from "react-router-dom";

import type { EmployeeOut } from "../api/types";
import CvViewer from "../components/CvViewer";
import EmployeeProfile from "../components/EmployeeProfile";
import EmployeeSyncBar from "../components/EmployeeSyncBar";
import ActIcon from "../components/ActIcon";
import Switch from "../components/Switch";
import { FIRED_STATUS, resumeStatusOptions } from "../lib/employees";
import { useEmployees } from "../lib/useEmployees";

// «Сотрудники» (FR-002, FR-031): карточки Jira, фильтр по 10 статусам, отпуска, профиль, переход к CV.
export default function EmployeesPage() {
  const emps = useEmployees();
  const [status, setStatus] = useState(""); // "" - все
  const [search, setSearch] = useState("");
  const [showFired, setShowFired] = useState(false);
  const [profile, setProfile] = useState<EmployeeOut | null>(null);
  const [cvKey, setCvKey] = useState<string | null>(null);

  const items = emps.items ?? [];
  // «Уволен» - не статус-чип, а тумблер: действует вместе со статусом и поиском.
  const visible = showFired ? items : items.filter((e) => e.status !== FIRED_STATUS);
  const q = search.trim().toLowerCase();
  const shown = visible.filter(
    (e) =>
      (!status || e.status === status) &&
      (!q || e.full_name.toLowerCase().includes(q) || e.jira_key.toLowerCase().includes(q)),
  );

  return (
    <section>
      <h1>Сотрудники</h1>

      <nav className="filter" aria-label="Статусы сотрудников">
        <button
          type="button"
          className={status === "" ? "filter__item filter__item--active" : "filter__item"}
          aria-pressed={status === ""}
          onClick={() => setStatus("")}
        >
          Все <span className="filter__count">{visible.length}</span>
        </button>
        {resumeStatusOptions(emps.counts).map((o) => (
          <button
            key={o.status}
            type="button"
            className={status === o.status ? "filter__item filter__item--active" : "filter__item"}
            aria-pressed={status === o.status}
            onClick={() => setStatus(o.status)}
          >
            {o.status} <span className="filter__count">{o.count}</span>
          </button>
        ))}
      </nav>

      <EmployeeSyncBar
        sync={emps.sync}
        refreshing={emps.refreshing}
        onRefresh={() => void emps.refresh()}
        error={emps.refreshError}
        info={emps.info}
      />
      <div className="toolbar">
        <input
          type="search"
          aria-label="Поиск по ФИО и ключу"
          placeholder="ФИО или ключ"
          value={search}
          maxLength={200}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Switch label="Уволенные" checked={showFired} onChange={(e) => setShowFired(e.target.checked)} />
      </div>

      {emps.error && (
        <p className="error" role="alert">
          {emps.error}
        </p>
      )}
      {emps.items === null && !emps.error && <p className="muted">Загрузка…</p>}

      {emps.items &&
        (shown.length === 0 ? (
          <div className="empty">
            <p>Нет данных</p>
          </div>
        ) : (
          <ul className="cards">
            {shown.map((e) => (
              <li key={e.jira_key} className="card" aria-label={e.full_name}>
                <h2 className="card__title">
                  {e.jira_url ? (
                    <a href={e.jira_url} target="_blank" rel="noreferrer">
                      {e.full_name}
                    </a>
                  ) : (
                    e.full_name
                  )}
                </h2>
                <div className="muted">{e.jira_key}</div>
                <div>
                  <span className="status">{e.status}</span>{" "}
                  {!e.assignable && <span className="muted">не назначается</span>}
                </div>
                <div className="card__row">
                  <strong>Отпуска:</strong> {e.vacation_plan || <span className="muted">не указаны</span>}
                </div>
                {e.extra_experience && <div className="card__desc">{e.extra_experience}</div>}
                <div className="card__actions">
                  <button type="button" className="act" aria-label={`Профиль: ${e.full_name}`} onClick={() => setProfile(e)}>
                    <ActIcon name="user" />
                    <span>Профиль</span>
                  </button>
                  <button type="button" className="act" aria-label={`Резюме: ${e.full_name}`} onClick={() => setCvKey(e.jira_key)}>
                    <ActIcon name="doc" />
                    <span>Резюме</span>
                  </button>
                  <Link className="act" aria-label={`Файлы: ${e.full_name}`} to={`/resumes?q=${encodeURIComponent(e.jira_key)}`}>
                    <ActIcon name="folder" />
                    <span>Файлы</span>
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        ))}

      {profile && (
        <EmployeeProfile
          employee={profile}
          onClose={() => setProfile(null)}
          onViewCv={() => {
            setCvKey(profile.jira_key);
            setProfile(null);
          }}
        />
      )}
      {cvKey && <CvViewer employeeKey={cvKey} onClose={() => setCvKey(null)} />}
    </section>
  );
}
