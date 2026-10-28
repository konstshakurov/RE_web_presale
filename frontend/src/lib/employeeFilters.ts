import type { DeptHead, EmployeeOut } from "../api/types";
import { FIRED_STATUS, resumeStatusOptions, type StatusOption } from "./employees";

// Значение фильтра «РО» для сотрудников без РО (совпадает с backend, параметр dept_head).
export const NO_DEPT_HEAD = "__none__";
export const NO_DEPT_HEAD_LABEL = "Без РО";

// Фильтры «Сотрудников» (CR-012, design/KON-134): статусы - ИЛИ между собой; статусы, РО, поиск, «Уволенные» - И.
export interface EmployeeFilters {
  statuses: string[];
  deptHead: string; // "" - все; логин РО или NO_DEPT_HEAD
  search: string;
  showFired: boolean;
}

export const EMPTY_FILTERS: EmployeeFilters = { statuses: [], deptHead: "", search: "", showFired: false };

export function filtersActive(f: EmployeeFilters): boolean {
  return f.statuses.length > 0 || f.deptHead !== "" || f.search.trim() !== "" || f.showFired;
}

// Нажатие чипа: переключает статус независимо от остальных; пустой набор = «Все».
export function toggleStatus(selected: string[], status: string): string[] {
  return selected.includes(status) ? selected.filter((s) => s !== status) : [...selected, status];
}

export function matchesDeptHead(e: EmployeeOut, deptHead: string): boolean {
  if (!deptHead) return true;
  if (deptHead === NO_DEPT_HEAD) return !e.dept_head_login;
  return e.dept_head_login === deptHead;
}

// «Уволенные» + РО: основа счётчиков статусов (поиск и статусы в неё не входят).
export function baseEmployees(items: EmployeeOut[], f: EmployeeFilters): EmployeeOut[] {
  return items.filter((e) => (f.showFired || e.status !== FIRED_STATUS) && matchesDeptHead(e, f.deptHead));
}

export function statusCountOptions(base: EmployeeOut[]): StatusOption[] {
  const counts = new Map<string, number>();
  base.forEach((e) => counts.set(e.status, (counts.get(e.status) ?? 0) + 1));
  return resumeStatusOptions([...counts].map(([status, count]) => ({ status, count })));
}

export function applyEmployeeFilters(items: EmployeeOut[], f: EmployeeFilters): EmployeeOut[] {
  const q = f.search.trim().toLowerCase();
  return baseEmployees(items, f).filter(
    (e) =>
      (f.statuses.length === 0 || f.statuses.includes(e.status)) &&
      (!q || e.full_name.toLowerCase().includes(q) || e.jira_key.toLowerCase().includes(q)),
  );
}

// M в «Показано N из M»: с учётом «Уволенные», без остальных фильтров.
export function totalForSummary(items: EmployeeOut[], showFired: boolean): number {
  return showFired ? items.length : items.filter((e) => e.status !== FIRED_STATUS).length;
}

// Список РО для select: по алфавиту; «Все» и «Без РО» добавляет компонент.
export function sortDeptHeads(list: DeptHead[]): DeptHead[] {
  return [...list].sort((a, b) => a.display_name.localeCompare(b.display_name, "ru"));
}

export function deptHeadLabel(value: string, list: DeptHead[]): string {
  if (!value) return "Все";
  if (value === NO_DEPT_HEAD) return NO_DEPT_HEAD_LABEL;
  return list.find((d) => d.login === value)?.display_name ?? value;
}
