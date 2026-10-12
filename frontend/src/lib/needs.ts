import type { Rate, SyncStamp } from "../api/types";

const dateTime = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

export function formatDateTime(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : dateTime.format(d);
}

// Ставка полностью (BR-003): сумма, валюта, НДС, единица; пусто - «не указана» (FR-001).
export function formatRate(rate: Rate): { main: string; vat: string; proposer: string } {
  const proposer = rate.proposer ?? "";
  if (rate.amount === null || rate.amount === undefined) {
    return { main: "не указана", vat: "", proposer };
  }
  const amount = rate.amount.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
  const main = [amount, rate.currency, rate.unit ? `/ ${rate.unit}` : ""].filter(Boolean).join(" ");
  const vat = rate.includes_vat === true ? "с НДС" : rate.includes_vat === false ? "без НДС" : "НДС не указан";
  return { main, vat, proposer };
}

export interface SyncNote {
  kind: "paused" | "reauth" | "stale" | "error";
  text: string;
}

// Пометки по блоку sync (FR-028/029): текст отказа Jira показывается как есть.
export function syncNotes(sync: SyncStamp): SyncNote[] {
  const notes: SyncNote[] = [];
  const reauth = sync.state === "needs_reauthorization" || sync.code === "needs_reauthorization";
  if (sync.paused || sync.state === "paused") notes.push({ kind: "paused", text: "опрос приостановлен" });
  if (reauth) notes.push({ kind: "reauth", text: "нужна повторная авторизация" });
  if (sync.stale) notes.push({ kind: "stale", text: "данные могут отставать от Jira" });
  if (sync.message) notes.push({ kind: "error", text: sync.message });
  return notes;
}

const REFRESH_LABELS: Record<string, string> = {
  needs_reauthorization: "Нужна повторная авторизация",
  no_permission: "Нет прав в Jira",
  unavailable: "Jira недоступна",
  blocked: "Jira отклонила запрос",
};

// Ошибка «Обновить» по detail.state; message Jira - как есть.
export function refreshErrorText(state: string | undefined, message: string): string {
  const label = state ? REFRESH_LABELS[state] : undefined;
  if (!label) return message;
  return message && message !== label ? `${label}: ${message}` : label;
}
