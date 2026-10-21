import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { parseRate, rateRequired, resumeOptions, statusButtons } from "../lib/assign";
import { createLimiter } from "../lib/limiter";

const STAMP = { scope: "needs", state: "ok", stale: false, paused: false, code: "", message: "", interval_sec: 300 };
const ME = { login: "test.user", role: "user", jira_status: { state: "ok" }, sections: [], csrf_token: "FAKE_csrf" };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

let status = "Поиск кандидата";
let proposer = "от производства";
const NEED = () => ({
  jira_key: "AN-5919",
  title: "Аналитик 1С",
  status,
  epic_key: null,
  rate: { amount: 3600, currency: "RUB", includes_vat: true, proposer },
  jira_created_at: "2026-10-01T08:30:00Z",
  jira_updated_at: "2026-10-09T09:00:00Z",
});

const file = (id: number, kind: string, name: string, title: string) => ({
  id,
  employee_key: "EMP-1",
  kind,
  kind_title: title,
  file_name: name,
  mime_type: "application/pdf",
  size_bytes: 1,
  is_archived: false,
  uploaded_by: "x",
  uploaded_at: "2026-10-10T10:00:00Z",
  extraction_status: "ok",
  can_delete: true,
  can_restore: false,
});

const MATCH = {
  need_key: "AN-5919",
  status: "done",
  requested_by: "test.user",
  model: "qwen",
  started_at: "2026-10-09T10:00:00Z",
  calculated_at: "2026-10-09T10:01:00Z",
  stale: false,
  candidates_total: 1,
  evaluated: 1,
  total: 1,
  items: [
    {
      employee_key: "EMP-1",
      full_name: "Тестов Тест",
      status: "Свободен",
      percent: 82,
      rationale: "Совпадают 1С и REST",
      cv_file_id: 5,
      calculated_at: "2026-10-09T10:00:00Z",
      stale: false,
      skills: [],
    },
  ],
  tasks: [],
  batches_total: 1,
  batches_done: 1,
  batches_failed: 0,
  errors: [],
  can_evaluate_next: false,
  deep_size: 30,
  remaining: 0,
  run_id: null,
};

const REASON = { field_id: "customfield_16100", name: "Причина отсутствия кандидата", required: true, kind: "reason", options: [{ id: "r1", name: "Низкая ставка" }, { id: "r2", name: "Нет специалистов" }] };
const VAT = { field_id: "cf_vat", name: "Включает НДС", required: true, kind: "vat", options: [{ id: "v1", name: "Да" }, { id: "v2", name: "Нет" }], prefill_id: "v1" };

type Call = { url: string; method: string; body: unknown };
let calls: Call[];
let actions: unknown;
let post: (url: string, body: unknown) => Response | undefined;
let benchmark: () => Response | undefined;
const fetchMock = vi.fn();

function serve() {
  calls = [];
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body });
    if (url.endsWith("/auth/me")) return json(ME);
    if (url.endsWith("/auth/jira-relogin")) return json(ME);
    if (url.includes("/needs/match-counts")) return json({ counts: { "AN-5919": 1 } });
    if (url.endsWith("/sync/needs/groups"))
      return json({ groups: [{ key: "all", id: null, name: "Все статусы", count: 1 }], statuses: [], sync: STAMP });
    if (url.includes("/sync/needs?")) return json({ items: [NEED()], total: 1, limit: 100, offset: 0, sync: STAMP });
    if (url.includes("/needs/rate-benchmark")) return benchmark() ?? json({ detail: "нет" }, 500);
    if (url.endsWith("/status-actions")) return json(actions);
    if (url.includes("/needs/AN-5919/match?")) return json(MATCH);
    if (url.endsWith("/llm/providers")) return json([]);
    if (url.endsWith("/cv/employees/EMP-1/files"))
      return json({
        employee_key: "EMP-1",
        files: [file(1, "main_cv", "Main.docx", "Основное CV"), file(2, "extra_cv", "Eng.pdf", "Доп. CV"), file(3, "materials", "Works.pdf", "Материалы")],
        archive: [],
      });
    if (method === "POST") {
      const r = post(url, body);
      if (r) return r;
    }
    return json({ detail: "не найдено" }, 404);
  });
}

const posts = (part: string) => calls.filter((c) => c.method === "POST" && c.url.endsWith(part));

function renderNeeds() {
  return render(
    <MemoryRouter initialEntries={["/needs"]}>
      <App />
    </MemoryRouter>,
  );
}

const OK = { need_key: "AN-5919", status: "Кандидат назначен", refreshed: true, steps: ["fields", "transition"] };

beforeEach(() => {
  status = "Поиск кандидата";
  proposer = "от производства";
  actions = { need_key: "AN-5919", status, actions: [] };
  post = () => undefined;
  benchmark = () => undefined;
  vi.stubGlobal("fetch", fetchMock);
  serve();
});
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("lib/assign", () => {
  it("ставка: пробелы и запятая, пусто, не число", () => {
    expect(parseRate("3 800")).toBe(3800);
    expect(parseRate("3800,5")).toBe(3800.5);
    expect(parseRate("  ")).toBeNull();
    expect(parseRate("abc")).toBeUndefined();
    expect(parseRate("-5")).toBeUndefined();
  });
  it("ставка обязательна при «от производства»", () => {
    expect(rateRequired({ rate: { proposer: "От производства" } })).toBe(true);
    expect(rateRequired({ rate: { proposer: "от клиента" } })).toBe(false);
    expect(rateRequired(null)).toBe(false);
  });
  it("кнопки по статусу из кэша; «Отказ по кандидату» и прочие - нет", () => {
    expect(statusButtons("Требуется кандидат")).toEqual(["search", "hire", "no_candidates"]);
    expect(statusButtons("Поиск кандидата")).toEqual(["hire", "no_candidates"]);
    expect(statusButtons("Кандидатов нет")).toEqual(["search", "hire"]);
    expect(statusButtons(" найм ")).toEqual(["search", "no_candidates"]);
    expect(statusButtons("Кандидат назначен")).toEqual(["search", "hire", "no_candidates"]);
    expect(statusButtons("Отказ по кандидату")).toEqual([]);
    expect(statusButtons("Интервью состоялось")).toEqual([]);
  });
  it("файлы: основное CV, доп. CV; вложения без основного", () => {
    const r = resumeOptions([file(1, "main_cv", "a", "t"), file(2, "extra_cv", "b", "t"), file(3, "materials", "c", "t"), file(4, "client_template_cv", "d", "t")]);
    expect(r.main?.id).toBe(1);
    expect(r.extras.map((f) => f.id)).toEqual([2]);
    expect(r.attachable.map((f) => f.id)).toEqual([2, 3]);
  });
  it("ограничитель параллельности", async () => {
    const limit = createLimiter(2);
    let active = 0;
    let peak = 0;
    const task = async () => {
      active++;
      peak = Math.max(peak, active);
      await new Promise((r) => setTimeout(r, 5));
      active--;
    };
    await Promise.all([1, 2, 3, 4, 5].map(() => limit(task)));
    expect(peak).toBe(2);
  });
});

describe("Столбец «Статус»: кнопки", () => {
  it("кнопки из белого списка и кэша: список не обращается к Jira, клик - ровно один запрос", async () => {
    actions = {
      need_key: "AN-5919",
      status,
      actions: [{ action: "hire", label: "Найм", target_status: "Найм", comment_required: false, fields: [] }],
    };
    renderNeeds();
    const hire = await screen.findByRole("button", { name: "Найм, AN-5919" }, { timeout: 5000 });
    expect(screen.getByRole("button", { name: "Кандидатов нет, AN-5919" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Поиск,/ })).toBeNull();
    expect(calls.filter((c) => c.url.endsWith("/status-actions"))).toHaveLength(0);
    await userEvent.click(hire);
    await screen.findByRole("button", { name: "Перевести в «Найм»" });
    expect(calls.filter((c) => c.url.endsWith("/status-actions"))).toHaveLength(1);
  });

  it("Jira не предлагает переход: «Действие недоступно»", async () => {
    renderNeeds();
    await userEvent.click(await screen.findByRole("button", { name: "Найм, AN-5919" }));
    const dlg = await screen.findByRole("dialog", { name: "Найм" });
    expect(await within(dlg).findByText("Действие недоступно")).toBeInTheDocument();
    expect(within(dlg).queryByRole("button", { name: /Перевести/ })).toBeNull();
  });

  it("отказ Jira при запросе переходов: текст как есть, «Повторить»", async () => {
    const base = fetchMock.getMockImplementation()!;
    let n = 0;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/status-actions")) {
        n += 1;
        calls.push({ url, method: "GET", body: undefined });
        return n === 1
          ? json({ detail: { state: "no_permission", code: "no_permission", message: "You do not have permission (403)", completed: [] } }, 403)
          : json({ need_key: "AN-5919", status, actions: [{ action: "hire", label: "Найм", target_status: "Найм", comment_required: false, fields: [] }] });
      }
      return base(url, init);
    });
    renderNeeds();
    await userEvent.click(await screen.findByRole("button", { name: "Найм, AN-5919" }));
    const dlg = await screen.findByRole("dialog", { name: "Найм" });
    expect(await within(dlg).findByText("You do not have permission (403)")).toBeInTheDocument();
    await userEvent.click(within(dlg).getByRole("button", { name: "Повторить" }));
    expect(await screen.findByRole("button", { name: "Перевести в «Найм»" })).toBeInTheDocument();
  });

  it("«Отказ по кандидату»: под чипом ничего, запрос не уходит", async () => {
    status = "Отказ по кандидату";
    renderNeeds();
    await screen.findByText("Аналитик 1С");
    expect(calls.some((c) => c.url.endsWith("/status-actions"))).toBe(false);
    expect(screen.queryByText("действия недоступны")).toBeNull();
  });

  it("«Кандидатов нет»: причина и комментарий обязательны; отказ Jira как есть, введённое сохранено", async () => {
    actions = {
      need_key: "AN-5919",
      status,
      actions: [{ action: "no_candidates", label: "Кандидатов нет", target_status: "Кандидатов нет", comment_required: true, fields: [REASON] }],
    };
    let attempt = 0;
    post = (url) => {
      if (!url.endsWith("/status")) return undefined;
      attempt += 1;
      return attempt === 1 ? json({ detail: { state: "blocked", code: "jira_error", message: "Transition is not valid", completed: [] } }, 502) : json({ ...OK, status: "Кандидатов нет" });
    };
    renderNeeds();
    await userEvent.click(await screen.findByRole("button", { name: "Кандидатов нет, AN-5919" }));
    const dlg = screen.getByRole("dialog", { name: "Кандидатов нет" });
    await userEvent.click(within(dlg).getByRole("button", { name: "Перевести в «Кандидатов нет»" }));
    expect(within(dlg).getByRole("alert")).toHaveTextContent(/Заполните: Причина/);
    expect(posts("/status")).toHaveLength(0);
    await userEvent.selectOptions(within(dlg).getByLabelText(/Причина/), "r1");
    await userEvent.click(within(dlg).getByRole("button", { name: "Перевести в «Кандидатов нет»" }));
    expect(within(dlg).getByRole("alert")).toHaveTextContent("Укажите комментарий");
    await userEvent.type(within(dlg).getByLabelText(/Комментарий/), "Нет аналитиков");
    await userEvent.click(within(dlg).getByRole("button", { name: "Перевести в «Кандидатов нет»" }));
    expect(await within(dlg).findByText("Transition is not valid")).toBeInTheDocument();
    expect(within(dlg).getByLabelText(/Комментарий/)).toHaveValue("Нет аналитиков");
    expect(within(dlg).getByLabelText(/Причина/)).toHaveValue("r1");
    await userEvent.click(within(dlg).getByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posts("/status")[1].body).toEqual({ action: "no_candidates", comment: "Нет аналитиков", fields: { customfield_16100: "r1" } });
    // потребность перечитана
    expect(calls.filter((c) => c.url.includes("/sync/needs?")).length).toBeGreaterThan(1);
  });

  it("«Найм»: подсказка и предзаполнение из потребности", async () => {
    actions = {
      need_key: "AN-5919",
      status,
      actions: [{ action: "hire", label: "Найм", target_status: "Найм", comment_required: false, fields: [VAT] }],
    };
    post = (url) => (url.endsWith("/status") ? json(OK) : undefined);
    renderNeeds();
    await userEvent.click(await screen.findByRole("button", { name: "Найм, AN-5919" }));
    const dlg = screen.getByRole("dialog", { name: "Найм" });
    expect(within(dlg).getByLabelText(/Включает НДС/)).toHaveValue("v1");
    expect(within(dlg).getByText("Предзаполнено из потребности. Обязательно в Jira.")).toBeInTheDocument();
    await userEvent.click(within(dlg).getByRole("button", { name: "Перевести в «Найм»" }));
    await waitFor(() => expect(posts("/status")).toHaveLength(1));
    expect(posts("/status")[0].body).toEqual({ action: "hire", comment: "", fields: { cf_vat: "v1" } });
  });

  it("«Поиск»: подтверждение одним кликом", async () => {
    status = "Найм";
    actions = {
      need_key: "AN-5919",
      status,
      actions: [{ action: "search", label: "Поиск", target_status: "Поиск кандидата", comment_required: false, fields: [] }],
    };
    post = (url) => (url.endsWith("/status") ? json(OK) : undefined);
    renderNeeds();
    await userEvent.click(await screen.findByRole("button", { name: "Поиск, AN-5919" }));
    await userEvent.click(screen.getByRole("button", { name: "Перевести в «Поиск кандидата»" }));
    await waitFor(() => expect(posts("/status")).toHaveLength(1));
    expect(posts("/status")[0].body).toEqual({ action: "search", comment: "", fields: {} });
  });
});

async function openAssign() {
  renderNeeds();
  await userEvent.click(await screen.findByRole("button", { name: /^1 кандидат.*, AN-5919$/ }));
  await userEvent.click(await screen.findByRole("button", { name: "Назначить" }));
  return screen.getByRole("dialog", { name: "Назначить кандидата" });
}

describe("Окно «Назначить»", () => {
  it("UC-001: форма -> подтверждение с пояснением про статус -> отправка; список перечитан", async () => {
    post = (url) => (url.endsWith("/assign") ? json(OK) : undefined);
    const dlg = await openAssign();
    expect(within(dlg).getByLabelText("Резюме")).toHaveValue("main");
    await userEvent.type(within(dlg).getByLabelText(/Ставка/), "3 800");
    await userEvent.type(within(dlg).getByLabelText(/Комментарий/), "Рекомендую");
    await userEvent.click(await within(dlg).findByRole("checkbox", { name: /Works\.pdf/ }));
    await userEvent.click(within(dlg).getByRole("button", { name: "Назначить…" }));
    const conf = screen.getByRole("dialog", { name: "Подтвердите назначение" });
    expect(conf).toHaveTextContent(/Jira сама переведёт потребность в «Кандидат назначен»/);
    expect(conf).toHaveTextContent("3 800 руб. с НДС");
    await userEvent.click(within(conf).getByRole("button", { name: "Подтвердить и отправить в Jira" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posts("/assign")[0].body).toEqual({
      employee_key: "EMP-1",
      resume_file_id: null,
      extra_file_ids: [3],
      rate_rub: 3800,
      comment: "Рекомендую",
      completed: [],
    });
    expect(calls.filter((c) => c.url.includes("/sync/needs?")).length).toBeGreaterThan(1);
  });

  it("«от производства»: поле активно, «*» и placeholder; в прогрессе шаг «Ставка» последним", async () => {
    let release: () => void = () => {};
    post = (url) =>
      url.endsWith("/assign") ? (new Promise<Response>((res) => (release = () => res(json(OK)))) as unknown as Response) : undefined;
    const dlg = await openAssign();
    const rateField = within(dlg).getByLabelText(/Ставка/);
    expect(rateField).toBeEnabled();
    expect(rateField).toHaveAttribute("placeholder", "например, 3 800");
    expect(within(dlg).getByText("*")).toBeInTheDocument();
    await userEvent.type(rateField, "3 800");
    await within(dlg).findByRole("checkbox", { name: /Works/ });
    await userEvent.click(within(dlg).getByRole("button", { name: "Назначить…" }));
    const conf = screen.getByRole("dialog", { name: "Подтвердите назначение" });
    expect(within(conf).getByText(/3.800 руб\. с НДС/)).toBeInTheDocument();
    await userEvent.click(within(conf).getByRole("button", { name: "Подтвердить и отправить в Jira" }));
    const items = (await screen.findAllByRole("listitem")).map((li) => li.textContent);
    expect(items[items.length - 1]).toBe("Ставка");
    release();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("«от производства»: без ставки дальше не идём, сообщение «Укажите ставку…»", async () => {
    const dlg = await openAssign();
    await within(dlg).findByRole("checkbox", { name: /Works/ });
    await userEvent.click(within(dlg).getByRole("button", { name: "Назначить…" }));
    expect(within(dlg).getByRole("alert")).toHaveTextContent(/Укажите ставку/);
    expect(screen.queryByRole("dialog", { name: "Подтвердите назначение" })).toBeNull();
  });

  it("не «от производства»: ставка не обязательна", async () => {
    proposer = "от клиента";
    post = (url) => (url.endsWith("/assign") ? json(OK) : undefined);
    const dlg = await openAssign();
    await within(dlg).findByRole("checkbox", { name: /Works/ });
    await userEvent.click(within(dlg).getByRole("button", { name: "Назначить…" }));
    expect(screen.getByRole("dialog", { name: "Подтвердите назначение" })).toBeInTheDocument();
  });

  const BENCH = (groups: unknown[]) => ({ period_months: 6, vat_percent: 22, as_of: "2026-10-20T10:00:00Z", groups });

  it("ориентир (FR-019): среднее и медиана группы валюты и единицы потребности", async () => {
    benchmark = () => json(BENCH([{ currency: "USD", unit: null, count: 5, mean: 10, median: 9 }, { currency: "RUB", unit: null, count: 4, mean: 3450, median: 3400 }]));
    const dlg = await openAssign();
    expect(await within(dlg).findByText(/Среднее за 6 мес\.: 3.450, медиана 3.400 \(ориентир\)/)).toBeInTheDocument();
    expect(calls.filter((c) => c.url.includes("/needs/rate-benchmark?period_months=6"))).toHaveLength(1);
  });

  it("ориентир скрыт: ошибка запроса или мало данных; окно работает", async () => {
    benchmark = () => json({ detail: "сбой" }, 500);
    const dlg = await openAssign();
    await within(dlg).findByRole("checkbox", { name: /Works/ });
    await waitFor(() => expect(calls.some((c) => c.url.includes("/needs/rate-benchmark"))).toBe(true));
    expect(within(dlg).queryByText(/ориентир/)).toBeNull();
    expect(within(dlg).getByText(/В потребности:/)).toBeInTheDocument();
    expect(within(dlg).getByRole("button", { name: "Назначить…" })).toBeEnabled();
  });

  it("ориентир скрыт: в группе меньше трёх записей (нет mean/median)", async () => {
    benchmark = () => json(BENCH([{ currency: "RUB", unit: null, count: 2 }]));
    const dlg = await openAssign();
    await within(dlg).findByRole("checkbox", { name: /Works/ });
    expect(within(dlg).queryByText(/ориентир/)).toBeNull();
  });

  it("«от клиента»: ориентир не показывается и не запрашивается", async () => {
    proposer = "от клиента";
    benchmark = () => json(BENCH([{ currency: "RUB", unit: null, count: 4, mean: 3450, median: 3400 }]));
    const dlg = await openAssign();
    await within(dlg).findByRole("checkbox", { name: /Works/ });
    expect(within(dlg).queryByText(/ориентир/)).toBeNull();
    expect(calls.some((c) => c.url.includes("/needs/rate-benchmark"))).toBe(false);
  });

  it("«от клиента»: поле ставки не вводится и не отправляется (CR-008)", async () => {
    proposer = "от клиента";
    post = (url) => (url.endsWith("/assign") ? json(OK) : undefined);
    const dlg = await openAssign();
    await within(dlg).findByRole("checkbox", { name: /Works/ });
    const rateField = within(dlg).getByLabelText(/Ставка/);
    expect(rateField).toBeDisabled();
    expect(rateField).not.toHaveAttribute("placeholder");
    expect(within(dlg).queryByText("*")).toBeNull();
    expect(within(dlg).getByText(/В потребности:.*Ставку предлагает клиент — здесь не вводится\./)).toBeInTheDocument();
    await userEvent.click(within(dlg).getByRole("button", { name: "Назначить…" }));
    const conf = screen.getByRole("dialog", { name: "Подтвердите назначение" });
    expect(within(conf).getByText("ставка клиента, не передаётся")).toBeInTheDocument();
    await userEvent.click(within(conf).getByRole("button", { name: "Подтвердить и отправить в Jira" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posts("/assign")[0].body).toMatchObject({ rate_rub: null });
  });

  it("назначено, ставка не записана: окно остаётся с предупреждением, повтор шлёт completed", async () => {
    let n = 0;
    post = (url) => {
      if (!url.endsWith("/assign")) return undefined;
      n += 1;
      return n === 1
        ? json({
            ...OK,
            steps: ["fields", "attach:1", "transition"],
            rate_pending: true,
            warnings: ["Кандидат назначен, но ставка не записана в Jira: Jira отклонила запись ставки. Введённое сохранено, повторите запись ставки"],
          })
        : json({ ...OK, steps: ["fields", "attach:1", "transition", "rate"] });
    };
    const dlg = await openAssign();
    await userEvent.type(within(dlg).getByLabelText(/Ставка/), "3 800");
    await within(dlg).findByRole("checkbox", { name: /Works/ });
    await userEvent.click(within(dlg).getByRole("button", { name: "Назначить…" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Подтвердить и отправить в Jira" }));
    const form = await screen.findByRole("dialog", { name: "Назначить кандидата" });
    expect(await within(form).findByText(/ставка не записана в Jira: Jira отклонила/)).toBeInTheDocument();
    expect(within(form).getByLabelText(/Ставка/)).toHaveValue("3 800"); // введённое сохранено
    expect(within(form).getByRole("alert")).toHaveTextContent("Кандидат назначен, ставка не записана");
    expect(within(form).getByLabelText(/Резюме/)).toBeDisabled();
    expect(within(form).getByLabelText(/Комментарий/)).toBeDisabled();
    expect(within(form).getByRole("checkbox", { name: /Works/ })).toBeDisabled();
    expect(within(form).getByLabelText(/Ставка/)).toBeEnabled();
    expect(within(form).queryByRole("button", { name: "Отмена" })).toBeNull();
    expect(within(form).getByRole("button", { name: "Закрыть" })).toBeEnabled();
    await userEvent.click(within(form).getByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posts("/assign")[1].body).toMatchObject({ rate_rub: 3800, completed: ["fields", "attach:1", "transition"] });
  });

  it("отказ Jira: текст как есть, данные сохранены, «Повторить» передаёт completed", async () => {
    let n = 0;
    post = (url) => {
      if (!url.endsWith("/assign")) return undefined;
      n += 1;
      return n === 1
        ? json({ detail: { state: "no_permission", code: "no_permission", message: "You do not have the permission to assign issues. (403)", step: "attach:2", completed: ["fields"] } }, 403)
        : json(OK);
    };
    const dlg = await openAssign();
    await userEvent.type(within(dlg).getByLabelText(/Ставка/), "3800");
    await userEvent.type(within(dlg).getByLabelText(/Комментарий/), "Текст");
    await userEvent.click(within(dlg).getByRole("button", { name: "Назначить…" }));
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить и отправить в Jira" }));
    const form = await screen.findByRole("dialog", { name: "Назначить кандидата" });
    expect(within(form).getByText("You do not have the permission to assign issues. (403)")).toBeInTheDocument();
    expect(within(form).getByLabelText(/Ставка/)).toHaveValue("3800");
    expect(within(form).getByLabelText(/Комментарий/)).toHaveValue("Текст");
    await userEvent.click(within(form).getByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posts("/assign")[1].body).toMatchObject({ rate_rub: 3800, comment: "Текст", completed: ["fields"] });
  });

  it("сессия Jira истекла: пароль в том же окне, данные сохранены, пароль не остаётся", async () => {
    let n = 0;
    post = (url) => {
      if (!url.endsWith("/assign")) return undefined;
      n += 1;
      return n === 1
        ? json({ detail: { state: "needs_reauthorization", code: "needs_reauthorization", message: "Сессия Jira не создана", completed: [] } }, 409)
        : json(OK);
    };
    const dlg = await openAssign();
    await userEvent.type(within(dlg).getByLabelText(/Ставка/), "3800");
    await userEvent.click(within(dlg).getByRole("button", { name: "Назначить…" }));
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить и отправить в Jira" }));
    const form = await screen.findByRole("dialog", { name: "Назначить кандидата" });
    expect(within(form).getByText("Сессия Jira истекла")).toBeInTheDocument();
    expect(within(form).getByLabelText(/Ставка/)).toHaveValue("3800");
    const pwd = within(form).getByLabelText("Пароль Jira");
    await userEvent.type(pwd, "SECRET_pw");
    await userEvent.click(within(form).getByRole("button", { name: "Войти и продолжить" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const relogin = posts("/auth/jira-relogin");
    expect(relogin).toHaveLength(1);
    expect(relogin[0].body).toEqual({ password: "SECRET_pw" });
    expect(JSON.stringify(posts("/assign"))).not.toContain("SECRET_pw");
    expect(posts("/assign")).toHaveLength(2);
  });

  it("во время отправки закрыть окно нельзя", async () => {
    let release: (r: Response) => void = () => undefined;
    fetchMock.mockImplementationOnce(async () => json(ME)); // не используется: страховка порядка
    post = (url) => {
      if (!url.endsWith("/assign")) return undefined;
      return undefined;
    };
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
      url.endsWith("/assign") ? new Promise<Response>((r) => (release = r)) : base(url, init),
    );
    const dlg = await openAssign();
    await userEvent.type(within(dlg).getByLabelText(/Ставка/), "3800");
    await userEvent.click(within(dlg).getByRole("button", { name: "Назначить…" }));
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить и отправить в Jira" }));
    const sending = await screen.findByRole("dialog", { name: "Отправка в Jira" });
    expect(within(sending).getByRole("button", { name: "Закрыть" })).toBeDisabled();
    expect(within(sending).queryByRole("button", { name: "Закрыть окно" })).toBeNull();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Отправка в Jira" })).toBeInTheDocument();
    release(json(OK));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
