import type { MatchTask, RunState, SearchItem } from "../api/types";

export const POLL_MS = 2500; // период опроса состояния расчёта
export const PAGE_SIZE = 10; // «Показать ещё» - по 10 (FR-036)
export const LLM_TIMEOUT_SEC = 120; // таймаут запроса к LLM по умолчанию (NFR-010)

export type PercentTone = "high" | "mid" | "low";

// Цветовая шкала % совпадения (FR-036): от 75 - высокий, от 50 - средний, ниже - низкий.
export function percentTone(percent: number): PercentTone {
  if (percent >= 75) return "high";
  if (percent >= 50) return "mid";
  return "low";
}

export function isRunning(status: string | undefined): boolean {
  return status === "running";
}

export function isAwaiting(state: Pick<RunState, "status" | "run_id"> | null): boolean {
  return state?.status === "awaiting_confirmation" && !!state.run_id;
}

// Задачи шлюза, у которых вышло время ожидания или которые упали: для них предлагается «Повторить».
export function retryableTasks(tasks: MatchTask[]): MatchTask[] {
  return tasks.filter((t) => t.status === "timeout" || t.status === "failed");
}

export function hasTimeout(tasks: MatchTask[]): boolean {
  return tasks.some((t) => t.status === "timeout");
}

export function timeoutText(seconds = LLM_TIMEOUT_SEC): string {
  return `LLM не ответила за ${seconds} с. Результат не получен, повторите расчёт.`;
}

export const STAGE_LABELS: Record<string, string> = {
  screening: "скрининг всех кандидатов по навыкам",
  deep: "глубокая оценка по тексту CV",
};

export function stageLabel(stage: string | null | undefined): string {
  return (stage && STAGE_LABELS[stage]) || "";
}

// Прогресс пакетов во время расчёта.
export function progressText(s: Pick<RunState, "stage" | "batches_done" | "batches_total" | "screened" | "screen_total">): string {
  const stage = stageLabel(s.stage);
  const batches = s.batches_total > 0 ? `пакетов: ${s.batches_done} из ${s.batches_total}` : "";
  return ["Расчёт идёт", stage && `(${stage})`, batches && `— ${batches}`].filter(Boolean).join(" ");
}

export const SOURCE_LABELS: Record<string, string> = {
  llm: "оценка LLM",
  screening: "предварительная оценка по навыкам",
  fallback: "по технологиям и тексту, без LLM",
};

// Процент для строки поиска: у fallback процента нет; предварительная оценка скрининга помечается.
export function searchPercent(i: SearchItem): { value: number | null; preliminary: boolean } {
  if (i.source === "llm" && typeof i.percent === "number") return { value: i.percent, preliminary: false };
  if (i.source === "screening" && typeof i.screen_percent === "number") return { value: i.screen_percent, preliminary: true };
  return { value: null, preliminary: false };
}

// Сводка подстановок обезличивания: «категория: N».
export function replacementsText(r: Record<string, number>): string {
  const parts = Object.entries(r)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${k}: ${n}`);
  return parts.length ? parts.join(", ") : "подстановок нет";
}

// Внешний провайдер должен быть по HTTPS (FR-030, NFR-006); внутренний может быть и по http.
export function providerUrlError(url: string, external: boolean): string {
  const v = url.trim();
  if (v.length < 8) return "Укажите URL провайдера";
  if (!/^https?:\/\/\S+$/i.test(v)) return "URL должен начинаться с https://";
  if (external && !/^https:\/\//i.test(v)) return "Для внешнего провайдера допустим только HTTPS";
  return "";
}

// Словарь обезличивания: по одному термину в строке, без пустых и повторов (в порядке ввода).
export function parseTerms(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim().slice(0, 200);
    const k = t.toLowerCase();
    if (t && !seen.has(k)) {
      seen.add(k);
      out.push(t);
    }
  }
  return out;
}

export interface PromptRejection {
  status: number;
  message: string;
  fragments: string[]; // найденные необезличенные фрагменты (422)
}

// Отказ PUT .../prompt: текст сообщения и список фрагментов. Допущение: список лежит в detail.fragments
// либо fragments, элементы - строки или объекты с text/fragment/value.
export function promptRejection(e: unknown): PromptRejection {
  const err = e as { status?: number; message?: string; body?: unknown };
  const body = (err?.body ?? {}) as { detail?: unknown; fragments?: unknown };
  const detail = (typeof body.detail === "object" && body.detail !== null ? body.detail : {}) as { fragments?: unknown };
  const raw = Array.isArray(detail.fragments) ? detail.fragments : Array.isArray(body.fragments) ? body.fragments : [];
  const fragments = raw
    .map((f) => {
      if (typeof f === "string") return f;
      const o = f as { text?: unknown; fragment?: unknown; value?: unknown };
      const v = o?.text ?? o?.fragment ?? o?.value;
      return typeof v === "string" ? v : "";
    })
    .filter(Boolean);
  const status = err?.status ?? 0;
  const message =
    err?.message || (status === 422 ? "В тексте остались необезличенные фрагменты" : "Не удалось сохранить правки");
  return { status, message, fragments };
}

// «1 кандидат», «2 кандидата», «5 кандидатов», «21 кандидат», «12 кандидатов»
export function candidatesLabel(n: number): string {
  const last = n % 10;
  const tens = n % 100;
  const word = tens >= 11 && tens <= 14 ? "кандидатов" : last === 1 ? "кандидат" : last >= 2 && last <= 4 ? "кандидата" : "кандидатов";
  return `${n} ${word}`;
}
