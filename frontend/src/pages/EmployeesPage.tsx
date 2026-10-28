import { Fragment, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import type { EmployeeOut } from "../api/types";
import CvViewer from "../components/CvViewer";
import EmployeeProfile from "../components/EmployeeProfile";
import EmployeeSyncBar from "../components/EmployeeSyncBar";
import ActIcon from "../components/ActIcon";
import DeptHeadSelect, { DeptHeadHint } from "../components/DeptHeadSelect";
import StatusChips from "../components/StatusChips";
import Switch from "../components/Switch";
import {
  EMPTY_FILTERS,
  applyEmployeeFilters,
  baseEmployees,
  deptHeadLabel,
  filtersActive,
  statusCountOptions,
  totalForSummary,
  type EmployeeFilters,
} from "../lib/employeeFilters";
import { useDeptHeads } from "../lib/useDeptHeads";
import { useEmployees } from "../lib/useEmployees";

// «Сотрудники» (FR-002, FR-031, CR-012): карточки Jira, мультивыбор статусов, РО, поиск, «Уволенные»; профиль, переход к CV.
export default function EmployeesPage() {
  const emps = useEmployees();
  const dh = useDeptHeads();
  const [filters, setFilters] = useState<EmployeeFilters>(EMPTY_FILTERS);
  const [profile, setProfile] = useState<EmployeeOut | null>(null);
  const [cvView, setCvView] = useState<{ key: string; name: string } | null>(null);

  const items = emps.items ?? [];
  const patchFilters = (p: Partial<EmployeeFilters>) => setFilters((f) => ({ ...f, ...p }));
  // Счётчики статусов и «Все» - с учётом РО и «Уволенные», без поиска.
  const base = baseEmployees(items, filters);
  const shown = applyEmployeeFilters(items, filters);
  const total = totalForSummary(items, filters.showFired);
  const active = filtersActive(filters);
  const reset = () => setFilters(EMPTY_FILTERS);

  const parts: ReactNode[] = [];
  if (filters.statuses.length) parts.push(<span key="st">статус: <b>{filters.statuses.join(", ")}</b></span>);
  if (filters.deptHead) parts.push(<span key="ro">РО: <b>{deptHeadLabel(filters.deptHead, dh.list)}</b></span>);
  if (filters.search.trim()) parts.push(<span key="q">поиск: <b>«{filters.search.trim()}»</b></span>);
  if (filters.showFired) parts.push(<b key="fired">включая уволенных</b>);

  return (
    <section>
      <EmployeeSyncBar
        title="Сотрудники"
        sync={emps.sync}
        refreshing={emps.refreshing}
        onRefresh={() => void emps.refresh()}
        error={emps.refreshError}
        info={emps.info}
      />

      <StatusChips
        options={statusCountOptions(base)}
        selected={filters.statuses}
        total={base.length}
        onChange={(statuses) => patchFilters({ statuses })}
      />

      <div className="toolbar toolbar--fields" role="search">
        <label className="fld">
          <span>Поиск</span>
          <input
            type="search"
            aria-label="Поиск по ФИО и ключу"
            placeholder="ФИО или ключ"
            value={filters.search}
            maxLength={200}
            onChange={(e) => patchFilters({ search: e.target.value })}
          />
        </label>
        <DeptHeadSelect value={filters.deptHead} onChange={(deptHead) => patchFilters({ deptHead })} list={dh.list} state={dh.state} />
        <div className="switchbox">
          <Switch label="Уволенные" checked={filters.showFired} onChange={(e) => patchFilters({ showFired: e.target.checked })} />
        </div>
        <DeptHeadHint state={dh.state} list={dh.list} onRetry={() => void dh.retry()} />
      </div>

      {emps.items && (
        <p className="fsum">
          <span aria-live="polite">
            Показано <b>{shown.length}</b> из {total}
          </span>
          {active && (
            <>
              <span>
                {parts.map((p, i) => (
                  <Fragment key={i}>
                    {i > 0 && <span aria-hidden="true"> · </span>}
                    {p}
                  </Fragment>
                ))}{" "}
                <span className="muted">(все условия сразу)</span>
              </span>
              <button type="button" className="link linkbtn" onClick={reset}>
                Сбросить фильтры
              </button>
            </>
          )}
        </p>
      )}

      {emps.error && (
        <p className="error" role="alert">
          Не удалось загрузить сотрудников. Проверьте соединение и обновите страницу.
        </p>
      )}
      {emps.items === null && !emps.error && (
        <p className="muted" role="status">
          <span className="spinner" aria-hidden="true" /> Загрузка…
        </p>
      )}

      {emps.items &&
        (shown.length === 0 ? (
          active ? (
            <div className="empty">
              <p>По выбранным фильтрам сотрудников нет.</p>
              <button type="button" className="btn-secondary" onClick={reset}>
                Сбросить фильтры
              </button>
            </div>
          ) : (
            <div className="empty">
              <p>Нет данных</p>
            </div>
          )
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
                  <button type="button" className="act" aria-label={`Резюме: ${e.full_name}`} onClick={() => setCvView({ key: e.jira_key, name: e.full_name })}>
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
            setCvView({ key: profile.jira_key, name: profile.full_name });
            setProfile(null);
          }}
        />
      )}
      {cvView && <CvViewer employeeKey={cvView.key} employeeName={cvView.name} onClose={() => setCvView(null)} />}
    </section>
  );
}
