import type { RosSettings } from "../api/types";
import { statusButtons } from "./assign";

// Модель ROS (FR-039, KON-161). Источник - input/ROS/ROS_model_v2.xlsx, лист «ROS специалиста_v2»
// (165 billable-часов); «Лист1» (145 ч) признан устаревшим и не используется.
// Параметры хранятся в «Настройках», вкладка «ROS» (Dev-53, GET /admin/ros/settings);
// здесь - эталон, применяемый пока настройки не загружены (или загрузка не удалась).
export const ROS_DEFAULTS: RosSettings = {
  billable_hours: 165,
  indirect_cost: 53000,
  profit_tax_rate: 5,
  vat_rate: 22,
  insurance_before: 15.2,
  insurance_after: 7.8,
  fot_limit: 2979000,
};

// Кнопка «ROS» видна только в статусах матрицы status_actions, где доступно действие
// кандидата; «Кандидат назначен» и далее - скрыта (FR-039 п. 3, UC-ROS).
export function rosVisible(status: string): boolean {
  const s = status.trim().toLowerCase().replace(/\s+/g, " ");
  return s !== "кандидат назначен" && statusButtons(s).length > 0;
}

export interface RosResult {
  revenue: number; // выручка = billable_hours * ставка
  vat: number; // НДС с выручки (доход = выручка / (100 + НДС%) * НДС%)
  directBefore: number; // прямые расходы до лимита: ЗП + взносы 15,2% + НДС
  directAfter: number; // после лимита: ЗП + взносы 7,8% + НДС
  profitBefore: number; // прибыль до лимита (после налога на прибыль)
  profitAfter: number;
  rosBefore: number; // ROS % = прибыль / выручка * 100
  rosAfter: number;
  transitionMonth: number; // N = ОКРУГЛВНИЗ(Лимит ФОТ / ЗП, 0): «до» N месяцев, «после» с N+1-го
}

// Контрольный пример (критерий приёмки FR-039): ЗП 130 000, ставка 2 200 с НДС ->
// выручка 363 000; ROS до лимита 24,80% (прибыль 90 041,93), после 27,32% (99 180,93);
// переход на 23-й месяц.
export function calcRos(p: RosSettings, salaryGross: number, rateWithVat: number): RosResult {
  const revenue = p.billable_hours * rateWithVat;
  const vat = revenue === 0 ? 0 : (revenue * p.vat_rate) / (100 + p.vat_rate);
  const insB = (salaryGross * p.insurance_before) / 100;
  const insA = (salaryGross * p.insurance_after) / 100;
  const beforeTaxB = revenue - (salaryGross + insB + vat) - p.indirect_cost;
  const beforeTaxA = revenue - (salaryGross + insA + vat) - p.indirect_cost;
  const taxB = (beforeTaxB > 0 ? beforeTaxB : 0) * (p.profit_tax_rate / 100); // налог только с прибыли > 0
  const taxA = (beforeTaxA > 0 ? beforeTaxA : 0) * (p.profit_tax_rate / 100);
  const profitBefore = beforeTaxB - taxB;
  const profitAfter = beforeTaxA - taxA;
  return {
    revenue,
    vat,
    directBefore: salaryGross + insB + vat,
    directAfter: salaryGross + insA + vat,
    profitBefore,
    profitAfter,
    rosBefore: revenue > 0 ? (profitBefore / revenue) * 100 : 0,
    rosAfter: revenue > 0 ? (profitAfter / revenue) * 100 : 0,
    transitionMonth: Math.floor(p.fot_limit / salaryGross),
  };
}

// Ставка для расчёта (decision.md KON-161 п. 1.2-1.4):
// - «задана клиентом» (руб./час, не «от производства») - read-only: «без НДС» автоматически
//   приводится к «с НДС» умножением на (1 + НДС% из Настроек);
// - «не задана» / «от производства» / не рубли / единица не «час» - ручной ввод,
//   значение потребности показывается справочно.
export interface RosRate {
  kind: "readonly" | "manual";
  withVat: number | null; // значение подставляемой ставки (kind: readonly)
  vatConverted: boolean; // была «без НДС» - приводим автоматически (подсказка)
  demandRate: string; // справочная подпись ставки потребности («3 000 RUB / час без НДС»)
}

// Час: «час», «ч», «ч (час)» (формат Jira), «часы»; пустая единица = час по умолчанию
// (ставка потребности в рублях за час).
const HOUR_UNIT_RE = /^(ч|часы|час|ч\(час\))$/;
function unitIsHour(unit: string): boolean {
  const n = unit.trim().toLowerCase().replace(/\s+/g, "");
  return n === "" || HOUR_UNIT_RE.test(n);
}

export function rosRate(rate: NeedRate, vatRate: number): RosRate {
  const amount = rate.amount ?? null;
  const proposer = (rate.proposer ?? "").trim().toLowerCase();
  const currency = (rate.currency ?? "").trim().toUpperCase();
  const parts = [fmtNum(amount), rate.currency, rate.unit ? `/ ${rate.unit}` : "", vatLabel(rate.includes_vat)].filter(
    Boolean,
  );
  const demandRate = parts.join(" ");
  const clientDefined =
    amount !== null && proposer !== "от производства" && currency === "RUB" && unitIsHour(rate.unit ?? "");
  if (!clientDefined) return { kind: "manual", withVat: null, vatConverted: false, demandRate };
  return {
    kind: "readonly",
    withVat: rate.includes_vat === false ? amount * (1 + vatRate / 100) : amount,
    vatConverted: rate.includes_vat === false,
    demandRate,
  };
}

// Числа и подписи
export const fmtNum = (v: number | null) =>
  v === null ? "" : v.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
export const fmtPct = (v: number) => v.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const vatFactor = (vatRate: number) =>
  // подсказка приведения по критерию приёмки KON-161: всегда два знака («× 1,22», «× 1,20»)
  (1 + vatRate / 100).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function vatLabel(includesVat?: boolean | null): string {
  return includesVat === true ? "с НДС" : includesVat === false ? "без НДС" : "НДС не указан";
}

// Локальный тип ставки потребности (совпадает с Rate из api/types)
interface NeedRate {
  amount?: number | null;
  currency?: string | null;
  includes_vat?: boolean | null;
  proposer?: string | null;
  unit?: string | null;
}
