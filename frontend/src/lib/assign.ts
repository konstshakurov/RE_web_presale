import { ApiError } from "../api/client";
import type { FileOut, NeedOut, RateBenchmark } from "../api/types";

// Кнопки статуса (KON-79 status_actions.md, KON-96): белый список действий × текущий статус из кэша.
// Строится без обращений к Jira; точные переходы и поля запрашиваются при клике на кнопку.
export type ActionKey = "search" | "hire" | "no_candidates";

export const ACTION_META: Record<ActionKey, { label: string; target: string; title: string }> = {
  search: { label: "Поиск", target: "Поиск кандидата", title: "Поиск кандидата" },
  hire: { label: "Найм", target: "Найм", title: "Найм" },
  no_candidates: { label: "Кандидатов нет", target: "Кандидатов нет", title: "Кандидатов нет" },
};

const MATRIX: Record<string, ActionKey[]> = {
  "требуется кандидат": ["search", "hire", "no_candidates"],
  "поиск кандидата": ["hire", "no_candidates"],
  "кандидатов нет": ["search", "hire"],
  "найм": ["search", "no_candidates"],
  "кандидат назначен": ["search", "hire", "no_candidates"],
};

export function statusButtons(status: string): ActionKey[] {
  return MATRIX[status.trim().toLowerCase().replace(/\s+/g, " ")] ?? [];
}

// Ставка, руб. с НДС: «3 800», «3800,50» -> число; пусто -> null; не число -> undefined.
export function parseRate(text: string): number | null | undefined {
  const t = text.replace(/\s/g, "").replace(",", ".");
  if (t === "") return null;
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return undefined;
  return Number(t);
}

// FR-018: ставка обязательна, если в потребности «Кто предлагает ставку» = «от производства».
export function rateRequired(need?: Pick<NeedOut, "rate"> | null): boolean {
  return (need?.rate?.proposer ?? "").trim().toLowerCase() === "от производства";
}

// Ориентир (FR-019): группа с валютой и единицей потребности; без mean/median (мало данных) или без группы - null (скрыт).
export function benchmarkText(bench: RateBenchmark | null, rate?: NeedOut["rate"] | null): string | null {
  const currency = rate?.currency;
  if (!bench || !currency) return null;
  const g = bench.groups.find((x) => x.currency === currency && (x.unit ?? null) === (rate?.unit ?? null));
  if (!g || g.mean == null || g.median == null) return null;
  const fmt = (v: number) => v.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  const cur = currency === "RUB" ? "" : ` ${currency}`;
  return `Среднее за ${bench.period_months} мес.: ${fmt(g.mean)}${cur}, медиана ${fmt(g.median)}${cur} (ориентир).`;
}

export const RATE_REQUIRED_TEXT = "Укажите ставку: в потребности «Кто предлагает ставку» = «от производства».";
export const RATE_FORMAT_TEXT = "Введите ставку числом, например 3 800";

// Резюме для назначения: Основное CV (по умолчанию) и Доп. CV; файлы - Доп. CV и Материалы (FR-008).
export function resumeOptions(files: FileOut[]): { main: FileOut | undefined; extras: FileOut[]; attachable: FileOut[] } {
  const live = files.filter((f) => !f.is_archived);
  return {
    main: live.find((f) => f.kind === "main_cv"),
    extras: live.filter((f) => f.kind === "extra_cv"),
    attachable: live.filter((f) => f.kind === "extra_cv" || f.kind === "materials"),
  };
}

// Разбор отказа записи: что показывать (FR-029 - текст Jira как есть) и можно ли повторить после ввода пароля.
export interface OpFailure {
  kind: "reauth" | "refused";
  message: string;
  completed: string[];
  status?: number;
}

export function toFailure(e: unknown, fallback: string): OpFailure {
  if (e instanceof ApiError) {
    const reauth = e.status === 409 && e.detail?.state === "needs_reauthorization";
    return { kind: reauth ? "reauth" : "refused", message: e.message, completed: e.detail?.completed ?? [], status: e.status };
  }
  return { kind: "refused", message: fallback, completed: [] };
}

export const STEP_FIELDS = "fields";
export const STEP_TRANSITION = "transition";
export const STEP_RATE = "rate";
export const attachStep = (id: number) => `attach:${id}`;
