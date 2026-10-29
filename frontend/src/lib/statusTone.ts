// Тип цветного чипа статуса (KON-150, decision KON-149 п. 3). Привязка статуса к цвету - одна таблица:
// правка привязки = замена строки. Статус вне таблицы -> нейтральный.
export type StatusTone = "ok" | "info" | "err" | "warn" | "neu";

const TONES: Record<string, StatusTone> = {
  // статусы потребностей (Jira workflow, lib/assign.ts)
  "требуется кандидат": "warn",
  "поиск кандидата": "info",
  "найм": "info",
  "кандидатов нет": "err",
  "кандидат назначен": "ok",
  // статусы сотрудников (FR-002, lib/employees.ts)
  "свободен": "ok",
  "скоро освобождается": "warn",
  "ожидает подключения": "warn",
  "коммерческий проект": "info",
  "внутренний проект": "info",
  "выход новичка": "info",
  "программа обучения": "info",
  "недоступен для подключения": "err",
  "уволен": "neu",
  "декрет": "neu",
};

const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, " ");

export function statusTone(status: string | null | undefined): StatusTone {
  return TONES[norm(status ?? "")] ?? "neu";
}

// className для <span> статуса: нейтральный - базовый .status без модификатора.
export function statusClass(status: string | null | undefined): string {
  const t = statusTone(status);
  return t === "neu" ? "status" : `status status--${t}`;
}
