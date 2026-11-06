import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { calcRos, fmtPct, rosRate, rosVisible, ROS_DEFAULTS } from "../lib/ros";

const STAMP = { scope: "needs", state: "ok", stale: false, paused: false, code: "", message: "", interval_sec: 300 };
let me = { login: "test.user", role: "user", jira_status: { state: "ok" }, sections: [], csrf_token: "FAKE_csrf" };

// Эталонная модель ROS_model_v2 (FR-041); /admin/ros/settings может вернуть её же или ошибку
const ROS = { ...ROS_DEFAULTS };

let status = "Поиск кандидата";
let rate: { amount?: number | null; currency?: string | null; includes_vat?: boolean | null; proposer?: string | null; unit?: string | null } = {
  amount: 3000,
  currency: "RUB",
  includes_vat: false,
  proposer: "от клиента",
  unit: "час",
};
const NEED = () => ({
  jira_key: "AN-5919",
  title: "Аналитик 1С",
  status,
  epic_key: null,
  rate,
  jira_created_at: "2026-10-01T08:30:00Z",
  jira_updated_at: "2026-10-09T09:00:00Z",
});

const json = (body: unknown, code = 200) =>
  new Response(JSON.stringify(body), { status: code, headers: { "Content-Type": "application/json" } });

let rosSettings: (() => Response) | null;
let admin = false;
const serveRosSettings = () => {
  rosSettings = null;
  admin = false;
};
type Call = { url: string; method: string; body: unknown };
let calls: Call[];
const fetchMock = vi.fn();

function serve() {
  calls = [];
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body });
    if (url.endsWith("/auth/me")) return json(me);
    if (url.includes("/needs/match-counts")) return json({ counts: {} });
    if (url.endsWith("/sync/needs/groups"))
      return json({ groups: [{ key: "all", id: null, name: "Все статусы", count: 1 }], statuses: [], sync: STAMP });
    if (url.includes("/sync/needs?")) return json({ items: [NEED()], total: 1, limit: 100, offset: 0, sync: STAMP });
    if (url.endsWith("/admin/ros/settings")) {
      if (method === "PUT") return admin ? json(body) : json({ detail: "Недостаточно прав" }, 403);
      return (rosSettings ?? (() => json(ROS)))();
    }
    return json({ detail: "не найдено" }, 404);
  });
}

function renderNeeds() {
  return render(
    <MemoryRouter initialEntries={["/needs"]}>
      <App />
    </MemoryRouter>,
  );
}

async function openRos() {
  renderNeeds();
  await userEvent.click(await screen.findByRole("button", { name: "ROS AN-5919" }));
  return screen.getByRole("dialog", { name: "ROS специалиста" });
}

beforeEach(() => {
  status = "Поиск кандидата";
  rate = { amount: 3000, currency: "RUB", includes_vat: false, proposer: "от клиента", unit: "час" };
  serveRosSettings();
  vi.stubGlobal("fetch", fetchMock);
  serve();
});
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("lib/ros", () => {
  it("контрольный пример FR-041: 130 000 x 2 200 = 24,80% / 27,32%, переход с 23-го месяца", () => {
    const r = calcRos(ROS_DEFAULTS, 130000, 2200);
    expect(r.revenue).toBe(363000);
    expect(r.profitBefore).toBeCloseTo(90041.93, 0);
    expect(r.profitAfter).toBeCloseTo(99180.93, 0);
    expect(r.rosBefore).toBeCloseTo(24.804900, 3);
    expect(r.rosAfter).toBeCloseTo(27.322515, 3);
    expect(r.transitionMonth).toBe(22);
    expect(fmtPct(r.rosBefore)).toBe("24,80");
    expect(fmtPct(r.rosAfter)).toBe("27,32");
  });
  it("rosVisible: статусы действий кандидата; «Кандидат назначен» и далее - скрыта", () => {
    expect(rosVisible("Требуется кандидат")).toBe(true);
    expect(rosVisible("Поиск кандидата")).toBe(true);
    expect(rosVisible("Кандидатов нет")).toBe(true);
    expect(rosVisible(" найм ")).toBe(true);
    expect(rosVisible("Кандидат назначен")).toBe(false);
    expect(rosVisible("Отказ по кандидату")).toBe(false);
    expect(rosVisible("Интервью")).toBe(false);
  });
  it("rosRate: клиентская руб./час - read-only с приведением без НДС; остальное - ручной ввод", () => {
    const r1 = rosRate({ amount: 3000, currency: "RUB", includes_vat: false, proposer: "от клиента", unit: "час" }, 22);
    expect(r1).toMatchObject({ kind: "readonly", withVat: 3660, vatConverted: true });
    const r2 = rosRate({ amount: 2200, currency: "RUB", includes_vat: true, proposer: "от клиента", unit: null }, 22);
    expect(r2).toMatchObject({ kind: "readonly", withVat: 2200, vatConverted: false });
    expect(rosRate({ amount: 3000, currency: "RUB", includes_vat: true, proposer: "от клиента", unit: "ч (час)" }, 22).kind).toBe("readonly");
    expect(rosRate({ amount: 3000, currency: "RUB", includes_vat: true, proposer: "от клиента", unit: "ч" }, 22).kind).toBe("readonly");
    expect(rosRate({ amount: 3000, currency: "RUB", includes_vat: true, proposer: "от производства", unit: "час" }, 22).kind).toBe("manual");
    expect(rosRate({ amount: 50, currency: "USD", includes_vat: null, proposer: "от клиента", unit: "час" }, 22).kind).toBe("manual");
    expect(rosRate({ amount: 3000, currency: "RUB", includes_vat: true, proposer: "от клиента", unit: "день" }, 22).kind).toBe("manual");
    expect(rosRate({ amount: null, currency: null, includes_vat: null, proposer: null, unit: null }, 22).kind).toBe("manual");
  });
});

describe("Окно «ROS специалиста»", () => {
  const hint = (dlg: HTMLElement) =>
    Array.from(dlg.querySelectorAll(".ros__hint")).map((h) => (h.textContent ?? "").replace(/\u00A0/g, " ")).join(" | ");

  it("клиентская ставка read-only: подстановка из потребности, контрольный пример FR-041", async () => {
    rate = { amount: 2200, currency: "RUB", includes_vat: true, proposer: "от клиента", unit: "час" };
    const dlg = await openRos();
    const rateField = within(dlg).getByLabelText(/Ставка для расчёта/) as HTMLInputElement;
    expect(rateField).toHaveAttribute("readonly");
    expect(rateField.value.replace(/\u00A0/g, " ")).toBe("2 200");
    expect(within(dlg).queryByText(/приведена к «с НДС»/)).toBeNull();
    await userEvent.type(within(dlg).getByLabelText(/ЗП кандидата/), "130 000");
    expect(within(dlg).getByText(/первые 22 мес\./)).toBeInTheDocument();
    expect(within(dlg).getByText(/с 23-го месяца/)).toBeInTheDocument();
    expect(within(dlg).getByText("24,80%")).toBeInTheDocument();
    expect(within(dlg).getByText("27,32%")).toBeInTheDocument();
    expect(within(dlg).getByText(/90.041,93/)).toBeInTheDocument();
    expect(within(dlg).getByText(/99.180,93/)).toBeInTheDocument();
    expect(within(dlg).queryByText(/Детали расчёта/)).toBeNull();
    // ничего не сохраняется: только чтение
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  it("приведение «без НДС»: 3 000 -> 3 660 автоматически, подсказка x 1,22", async () => {
    const dlg = await openRos();
    const rateField = within(dlg).getByLabelText(/Ставка для расчёта/) as HTMLInputElement;
    expect(rateField).toHaveAttribute("readonly");
    expect(rateField.value.replace(/\u00A0/g, " ")).toBe("3 660");
    expect(hint(dlg)).toContain("Ставка без НДС приведена к «с НДС» (× 1,22)");
  });

  it("«от производства»: поле ставки активно и пусто, значение потребности справочно", async () => {
    rate = { amount: 3000, currency: "RUB", includes_vat: true, proposer: "от производства", unit: "час" };
    const dlg = await openRos();
    const rateField = within(dlg).getByLabelText(/Ставка для расчёта/);
    expect(rateField).toBeEnabled();
    expect(rateField).toHaveValue("");
    expect(hint(dlg)).toContain("В потребности: 3 000 RUB / час");
    await userEvent.type(rateField, "2 200");
    await userEvent.type(within(dlg).getByLabelText(/ЗП кандидата/), "130 000");
    expect(within(dlg).getByText("24,80%")).toBeInTheDocument();
  });

  it("валюта/единица не руб./час: ручной ввод, ставка потребности справочно", async () => {
    rate = { amount: 50, currency: "USD", includes_vat: null, proposer: "от клиента", unit: "час" };
    const dlg = await openRos();
    const rateField = within(dlg).getByLabelText(/Ставка для расчёта/);
    expect(rateField).toBeEnabled();
    expect(rateField).toHaveValue("");
    expect(within(dlg).getByText(/В потребности: 50 USD \/ час/)).toBeInTheDocument();
    expect(within(dlg).queryByText(/приведена к «с НДС»/)).toBeNull();
  });

  it("UC-ROS 5а/5в: ЗП <= 0 и ставка > 1 000 000 - ошибки под полями, пересчёт заблокирован", async () => {
    rate = { amount: 3000, currency: "RUB", includes_vat: true, proposer: "от производства", unit: "час" };
    const dlg = await openRos();
    await userEvent.type(within(dlg).getByLabelText(/ЗП кандидата/), "0");
    expect(within(dlg).getByText("Введите ЗП числом больше нуля")).toBeInTheDocument();
    expect(within(dlg).queryByText("24,80%")).toBeNull();
    await userEvent.type(within(dlg).getByLabelText(/Ставка для расчёта/), "2000000");
    expect(within(dlg).getByText("Ставка не может превышать 1 000 000")).toBeInTheDocument();
  });

  it("настройки недоступны: расчёт по эталону ROS_model_v2", async () => {
    rate = { amount: 3000, currency: "RUB", includes_vat: true, proposer: "от производства", unit: "час" };
    rosSettings = () => json({ detail: "сбой" }, 500);
    const dlg = await openRos();
    await userEvent.type(within(dlg).getByLabelText(/ЗП кандидата/), "130 000");
    await userEvent.type(within(dlg).getByLabelText(/Ставка для расчёта/), "2 200");
    expect(within(dlg).getByText("24,80%")).toBeInTheDocument();
    expect(within(dlg).getByText("27,32%")).toBeInTheDocument();
  });

  it("изменённые настройки применяются: НДС 20% -> приведение x 1,20 (3 000 -> 3 600)", async () => {
    rosSettings = () => json({ ...ROS, vat_rate: 20 });
    const dlg = await openRos();
    const rateField = within(dlg).getByLabelText(/Ставка для расчёта/) as HTMLInputElement;
    expect(rateField.readOnly).toBe(true);
    expect(rateField.value.replace(/\u00A0/g, " ")).toBe("3 600");
    expect(hint(dlg)).toContain("1,20");
  });

  it("кнопка ROS скрыта вне статусов действий кандидата", async () => {
    status = "Отказ по кандидату";
    renderNeeds();
    await screen.findByText("Аналитик 1С");
    expect(screen.queryByRole("button", { name: "ROS AN-5919" })).toBeNull();
    status = "Кандидат назначен";
    renderNeeds();
    await screen.findByText("Аналитик 1С");
    expect(screen.queryByRole("button", { name: "ROS AN-5919" })).toBeNull();
  });
});

describe("Настройки: вкладка «ROS» (Dev-53, FR-041)", () => {
  function renderSettings() {
    return render(
      <MemoryRouter initialEntries={["/settings"]}>
        <App />
      </MemoryRouter>,
    );
  }

  const puts = () => calls.filter((c) => c.method === "PUT" && c.url.endsWith("/admin/ros/settings"));

  it("7 полей по умолчанию с эталона; админ сохраняет изменения (PUT всеми полями)", async () => {
    me = { ...me, role: "admin" };
    admin = true;
    renderSettings();
    await userEvent.click(await screen.findByRole("tab", { name: "ROS" }));
    expect(await screen.findByLabelText(/Billable ч\/мес/)).toHaveValue(165);
    expect(screen.getByLabelText(/Косвенные расходы/)).toHaveValue(53000);
    expect(screen.getByLabelText(/Налог на прибыль/)).toHaveValue(5);
    expect(screen.getByLabelText(/Ставка НДС/)).toHaveValue(22);
    expect(screen.getByLabelText(/взносы до лимита/)).toHaveValue(15.2);
    expect(screen.getByLabelText(/взносы после лимита/)).toHaveValue(7.8);
    expect(screen.getByLabelText(/Лимит ФОТ/)).toHaveValue(2979000);
    await userEvent.clear(screen.getByLabelText(/Ставка НДС/));
    await userEvent.type(screen.getByLabelText(/Ставка НДС/), "20");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByText(/Сохранено: значения общие/);
    expect(puts()).toHaveLength(1);
    expect(puts()[0].body).toEqual({
      billable_hours: 165, indirect_cost: 53000, profit_tax_rate: 5, vat_rate: 20,
      insurance_before: 15.2, insurance_after: 7.8, fot_limit: 2979000,
    });
  });

  it("не админ: сохранение отклоняется (403), текст ошибки под формой", async () => {
    renderSettings();
    await userEvent.click(await screen.findByRole("tab", { name: "ROS" }));
    await screen.findByLabelText(/Billable ч\/мес/);
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Недостаточно прав");
    expect(puts()).toHaveLength(1);
  });
});
