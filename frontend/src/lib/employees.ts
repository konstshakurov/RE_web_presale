import type { CvLimits, EmployeeOut, FileOut } from "../api/types";

// 10 статусов карточки сотрудника в Jira, FR-002 (workflow типа Epic, БП-8).
export const EMPLOYEE_STATUSES = [
  "Выход новичка",
  "Программа обучения",
  "Внутренний проект",
  "Свободен",
  "Коммерческий проект",
  "Ожидает подключения",
  "Скоро освобождается",
  "Недоступен для подключения",
  "Уволен",
  "Декрет",
];

// Статус «Уволен» в «Резюме» и «Сотрудниках» вынесен из списка статусов в тумблер (CR).
export const FIRED_STATUS = "Уволен";

// Порядок типов в окне загрузки; названия и допустимые типы приходят из GET /cv/limits.
const KIND_ORDER = ["main_cv", "extra_cv", "materials"];

export interface StatusOption {
  status: string;
  count: number;
}

// 10 статусов FR-002 в фиксированном порядке (с нулями); статус вне списка (Jira добавила) - в конце.
export function statusOptions(counts: { status: string; count: number }[]): StatusOption[] {
  const byStatus = new Map(counts.map((c) => [c.status, c.count]));
  const known = EMPLOYEE_STATUSES.map((s) => ({ status: s, count: byStatus.get(s) ?? 0 }));
  const extra = counts.filter((c) => !EMPLOYEE_STATUSES.includes(c.status));
  return [...known, ...extra.map((c) => ({ status: c.status, count: c.count }))];
}

// Варианты статусов в «Резюме» и «Сотрудниках»: без «Уволен» (он - отдельный тумблер).
export function resumeStatusOptions(counts: { status: string; count: number }[]): StatusOption[] {
  return statusOptions(counts).filter((o) => o.status !== FIRED_STATUS);
}

export function fileExtension(name: string): string {
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i + 1).toLowerCase();
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} МБ`;
}

export function uploadKinds(limits: CvLimits): { kind: string; title: string }[] {
  const kinds = Object.keys(limits.upload_kinds);
  const ordered = [...KIND_ORDER.filter((k) => kinds.includes(k)), ...kinds.filter((k) => !KIND_ORDER.includes(k))];
  return ordered.map((k) => ({ kind: k, title: limits.upload_kinds[k] }));
}

export function allowedExtensions(limits: CvLimits, kind: string): string[] {
  return limits.extensions_by_kind[kind] ?? [];
}

// Проверка до отправки (FR-022 п. 3): формат по типу файла и лимит размера. Сервер проверяет повторно.
export function validateUpload(file: File, kind: string, limits: CvLimits): string | null {
  const allowed = allowedExtensions(limits, kind);
  const ext = fileExtension(file.name);
  if (!allowed.includes(ext)) {
    const list = allowed.map((e) => `.${e}`).join(", ");
    return `Формат .${ext || "?"} не поддерживается для этого типа файла. Допустимо: ${list}`;
  }
  if (file.size === 0) return "Файл пустой";
  if (file.size > limits.max_file_bytes) return `Файл больше лимита ${limits.max_file_mb} МБ`;
  return null;
}

// Поиск раздела «Резюме»: ФИО, ключ Jira, имя файла (FR-032 п. 1; текст внутри CV - заглушка, нет API).
// Уволенные скрыты, пока showFired выключен; тумблер действует вместе с поиском и статусом.
export function matchesResume(
  emp: EmployeeOut,
  files: FileOut[],
  query: string,
  status: string,
  showFired = false,
): boolean {
  if (!showFired && emp.status === FIRED_STATUS) return false;
  if (status && emp.status !== status) return false;
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    emp.full_name.toLowerCase().includes(q) ||
    emp.jira_key.toLowerCase().includes(q) ||
    files.some((f) => f.file_name.toLowerCase().includes(q))
  );
}

// Совпадения поиска в тексте CV (FR-037): позиции без учёта регистра, без пересечений.
export function findMatches(text: string, query: string): number[] {
  const q = query.toLowerCase();
  if (!q) return [];
  const hay = text.toLowerCase();
  const res: number[] = [];
  for (let i = hay.indexOf(q); i >= 0; i = hay.indexOf(q, i + q.length)) res.push(i);
  return res;
}

export interface Segment {
  text: string;
  hit: number | null; // номер совпадения или null для обычного текста
}

export function textSegments(text: string, query: string): Segment[] {
  const matches = findMatches(text, query);
  if (matches.length === 0) return [{ text, hit: null }];
  const out: Segment[] = [];
  let pos = 0;
  matches.forEach((start, n) => {
    if (start > pos) out.push({ text: text.slice(pos, start), hit: null });
    out.push({ text: text.slice(start, start + query.length), hit: n });
    pos = start + query.length;
  });
  if (pos < text.length) out.push({ text: text.slice(pos), hit: null });
  return out;
}

// Файлы по ключу сотрудника: основное CV первым, дальше по дате загрузки (новые сверху).
export function groupFiles(files: FileOut[]): Map<string, FileOut[]> {
  const map = new Map<string, FileOut[]>();
  for (const f of files) {
    const list = map.get(f.employee_key) ?? [];
    list.push(f);
    map.set(f.employee_key, list);
  }
  for (const list of map.values()) {
    list.sort((a, b) => {
      const am = a.kind === "main_cv" ? 0 : 1;
      const bm = b.kind === "main_cv" ? 0 : 1;
      return am - bm || b.uploaded_at.localeCompare(a.uploaded_at);
    });
  }
  return map;
}
