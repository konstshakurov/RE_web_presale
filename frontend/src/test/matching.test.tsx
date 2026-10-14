import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { MatchItem, MatchState, MatchTask, RunOut, SearchItem, SearchState } from "../api/types";

// Быстрый опрос: проверки не ждут реальные 2,5 с
vi.mock("../lib/matching", async (orig) => ({ ...(await orig<typeof import("../lib/matching")>()), POLL_MS: 15 }));

const STAMP = {
  scope: "needs",
  state: "ok",
  stale: false,
  last_success_at: "2026-10-09T10:00:00Z",
  paused: false,
  code: "",
  message: "",
  interval_sec: 300,
};

const ME = { login: "test.user", role: "user", jira_status: { state: "ok" }, sections: [], csrf_token: "FAKE_csrf" };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const fetchMock = vi.fn();
type Call = { url: string; method: string; body: unknown };
let calls: Call[];
let counts: Record<string, number> = { "AN-1": 1 }; // результат уже есть -> в списке «N кандидатов»

function item(over: Partial<MatchItem> = {}): MatchItem {
  return {
    employee_key: "EMP-1",
    full_name: "Тестов Тест",
    status: "Свободен",
    percent: 82,
    rationale: "Совпадают 1С и REST",
    note: null,
    note_author: null,
    note_updated_at: null,
    cv_file_id: 5,
    calculated_at: "2026-10-09T10:00:00Z",
    stale: false,
    skills: ["1c"],
    ...over,
  };
}

function state(over: Partial<MatchState> = {}): MatchState {
  return {
    need_key: "AN-1",
    status: "done",
    requested_by: "test.user",
    model: "qwen",
    started_at: "2026-10-09T10:00:00Z",
    calculated_at: "2026-10-09T10:01:00Z",
    stale: false,
    candidates_total: 1,
    evaluated: 1,
    total: 1,
    items: [item()],
    tasks: [],
    batches_total: 1,
    batches_done: 1,
    batches_failed: 0,
    errors: [],
    can_evaluate_next: false,
    deep_size: 30,
    remaining: 0,
    run_id: null,
    ...over,
  };
}

function task(over: Partial<MatchTask> = {}): MatchTask {
  return { task_id: "t-1", status: "done", requires_confirmation: false, batch_status: "done", candidates: 1, ...over };
}

function search(over: Partial<SearchState> = {}): SearchState {
  return {
    query: "аналитик 1С",
    status: "done",
    mode: "llm",
    notice: null,
    requested_by: "test.user",
    model: "qwen",
    started_at: null,
    calculated_at: "2026-10-09T10:01:00Z",
    stale: false,
    candidates_total: 2,
    evaluated: 1,
    total: 2,
    items: [],
    tasks: [],
    batches_total: 1,
    batches_done: 1,
    batches_failed: 0,
    errors: [],
    ...over,
  };
}

function sitem(over: Partial<SearchItem> = {}): SearchItem {
  return {
    employee_key: "EMP-1",
    full_name: "Тестов Тест",
    status: "Свободен",
    source: "llm",
    percent: 90,
    screen_percent: null,
    rationale: "Подходит",
    score: 5,
    matched_skills: ["1c"],
    matched_terms: [],
    note: null,
    note_author: null,
    note_updated_at: null,
    cv_file_id: 5,
    calculated_at: "2026-10-09T10:00:00Z",
    stale: false,
    skills: ["1c"],
    ...over,
  };
}

const NEED = {
  jira_key: "AN-1",
  title: "Аналитик 1С",
  status: "Поиск кандидата",
  epic_key: null,
  rate: {},
  jira_created_at: "2026-10-01T08:30:00Z",
  jira_updated_at: "2026-10-09T09:00:00Z",
};

const EMP = {
  jira_key: "EMP-1",
  full_name: "Тестов Тест",
  status: "Свободен",
  assignable: true,
  synced_at: "2026-10-09T10:00:00Z",
};

type Handler = (url: string, method: string, body: unknown) => Response | undefined;

function serve(h: Handler, role = "user") {
  calls = [];
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body });
    const r = h(url, method, body);
    if (r) return r;
    if (url.endsWith("/auth/me")) return json({ ...ME, role });
    if (url.includes("/needs/match-counts")) return json({ counts });
    if (url.endsWith("/sync/needs/groups"))
      return json({ groups: [{ key: "all", id: null, name: "Все статусы", count: 1 }], statuses: [], sync: STAMP });
    if (url.includes("/sync/needs?")) return json({ items: [NEED], total: 1, limit: 100, offset: 0, sync: STAMP });
    if (url.includes("/sync/employees?"))
      return json({ items: [EMP], total: 1, limit: 500, offset: 0, statuses: [{ status: "Свободен", count: 1 }], sync: STAMP });
    if (url.endsWith("/cv/limits")) return json({ max_file_mb: 10, max_file_bytes: 1, upload_kinds: {}, extensions_by_kind: {} });
    if (url.endsWith("/cv/files")) return json({ items: [] });
    if (url.endsWith("/llm/providers")) return json([]);
    if (url.endsWith("/resume/technologies")) return json([{ tag: "1c", employees: 3 }]);
    if (url.includes("/resume/employees?")) return json({ items: [], total: 0 });
    return json({ detail: "не найдено" }, 404);
  });
}

const called = (method: string, part: string) => calls.filter((c) => c.method === method && c.url.includes(part));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  counts = { "AN-1": 1 };
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("Потребности: сопоставление", () => {
  it("«Сопоставить»: POST с CSRF, индикатор не блокирует интерфейс, затем кандидаты", async () => {
    counts = {};
    let polls = 0;
    serve((url, method) => {
      if (url.endsWith("/needs/AN-1/match") && method === "POST")
        return json(state({ status: "running", items: [], total: 0, evaluated: 0, calculated_at: null, stage: "screening" }), 202);
      if (url.includes("/needs/AN-1/match?")) {
        polls += 1;
        return json(polls < 2 ? state({ status: "running", items: [], total: 0, calculated_at: null }) : state());
      }
    });
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "Сопоставить AN-1" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/Расчёт идёт/);
    // интерфейс доступен: кнопка «Обновить» не заблокирована
    expect(screen.getByRole("button", { name: "Обновить" })).toBeEnabled();
    expect(await screen.findByText("Тестов Тест")).toBeInTheDocument();
    expect(called("POST", "/needs/AN-1/match")).toHaveLength(1);
    expect(screen.getByText("82%")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Назначить" })).toBeDisabled();
  });

  it("обоснование и рекомендации видны сразу; метка устаревшего результата", async () => {
    serve((url) => {
      if (url.includes("/needs/AN-1/match?")) return json(state({ stale: true, items: [item({ stale: true })] }));
    });
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "1 кандидат, AN-1" }));
    await screen.findByText("Тестов Тест");
    expect(screen.getByText("Совпадают 1С и REST")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Рекомендации" })).toBeInTheDocument();
    expect(screen.getByText("82%")).toHaveClass("pct--high");
    expect(screen.getAllByText(/устарел/i).length).toBeGreaterThan(0);
    expect(called("POST", "/match")).toHaveLength(0); // просмотр сохранённого не запускает расчёт
  });

  it("блок «Чего не хватает до 100%»: список, полное совпадение, подсказка при gaps=null", async () => {
    serve((url) => {
      if (url.includes("/needs/AN-1/match?"))
        return json(
          state({
            total: 3,
            items: [
              item({ gaps: ["Нет опыта с Kafka", "Грейд ниже требуемого"] }),
              item({ employee_key: "EMP-2", full_name: "Полный", percent: 100, gaps: [] }),
              item({ employee_key: "EMP-3", full_name: "Старый", percent: 60, gaps: null }),
            ],
          }),
        );
    });
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "1 кандидат, AN-1" }));
    await screen.findByText("Тестов Тест");
    expect(screen.getByText("Чего не хватает до 100%")).toBeInTheDocument();
    expect(screen.getByText("Нет опыта с Kafka")).toBeInTheDocument();
    expect(screen.getByText("Грейд ниже требуемого")).toBeInTheDocument();
    expect(screen.getByText("Полное совпадение")).toBeInTheDocument();
    expect(screen.getByText("Нажмите «Пересчитать», чтобы увидеть, чего не хватает")).toBeInTheDocument();
  });

  it("строка статистики: часть про скрининг только при screened > 0, с подсказкой", async () => {
    const stats = (over: Partial<MatchState>) =>
      serve((url) => {
        if (url.includes("/needs/AN-1/match?")) return json(state({ candidates_total: 4, evaluated: 4, ...over }));
      });
    stats({ screened: 0, screen_total: 4 });
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "1 кандидат, AN-1" }));
    const line = await screen.findByText(/Оценено кандидатов: 4 из 4/);
    expect(line.textContent).not.toContain("предварительная оценка");
    expect(line).toHaveAttribute("title", expect.stringContaining("быстрый отбор при большом числе кандидатов"));
  });

  it("строка статистики: при screened > 0 показывает предварительную оценку", async () => {
    serve((url) => {
      if (url.includes("/needs/AN-1/match?")) return json(state({ candidates_total: 40, evaluated: 30, screened: 40, screen_total: 40 }));
    });
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "1 кандидат, AN-1" }));
    const line = await screen.findByText(/Оценено кандидатов: 30 из 40/);
    expect(line.textContent).toContain("предварительная оценка по навыкам: 40 из 40");
  });

  it("«Показать ещё» берёт следующие 10 по offset; «Оценить следующих» и «Пересчитать» (force)", async () => {
    serve((url, method) => {
      if (url.includes("/needs/AN-1/match?") && url.includes("offset=10"))
        return json(
          state({ total: 12, can_evaluate_next: true, remaining: 10, items: [item({ employee_key: "EMP-11", full_name: "Одиннадцатый" })] }),
        );
      if (url.includes("/needs/AN-1/match?"))
        return json(
          state({
            total: 12,
            candidates_total: 40,
            can_evaluate_next: true,
            remaining: 10,
            items: Array.from({ length: 10 }, (_, i) => item({ employee_key: `EMP-${i}`, full_name: `Кандидат ${i}` })),
          }),
        );
      if (method === "POST") return json(state({ status: "running", items: [] }), 202);
    });
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "1 кандидат, AN-1" }));
    await screen.findByText("Кандидат 9");
    await userEvent.click(screen.getByRole("button", { name: "Показать ещё" }));
    expect(await screen.findByText("Одиннадцатый")).toBeInTheDocument();
    expect(screen.getByText("Кандидат 0")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("limit=10&offset=10"))).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: /Оценить следующих/ }));
    await waitFor(() => expect(called("POST", "/match/next")).toHaveLength(1));
    expect(called("POST", "/match/next")[0].body).toEqual({ provider_id: null });
  });

  it("«Пересчитать» отправляет force=true", async () => {
    serve((url, method) => {
      if (method === "POST") return json(state({ status: "running", items: [] }), 202);
      if (url.includes("/needs/AN-1/match?")) return json(state());
    });
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "1 кандидат, AN-1" }));
    await screen.findByText("Тестов Тест");
    await userEvent.click(screen.getByRole("button", { name: "Пересчитать" }));
    await waitFor(() => expect(called("POST", "/needs/AN-1/match")).toHaveLength(1));
    expect(called("POST", "/needs/AN-1/match")[0].body).toMatchObject({ force: true });
  });

  it("внешняя отправка: предпросмотр всего прогона, без подтверждения confirm не вызывается; один confirm", async () => {
    const run: RunOut = {
      run_id: "11111111-1111-1111-1111-111111111111",
      kind: "match",
      need_key: "AN-1",
      stage: "screening",
      status: "awaiting_confirmation",
      requires_confirmation: true,
      is_external: true,
      provider_name: "Внешняя модель",
      candidates: 7,
      replacements: { persons: 3 },
      tasks: [
        { task_id: "a", status: "awaiting_confirmation", candidates: 4, requires_confirmation: true, replacements: { persons: 2 }, ner_used: true, prompt: "ТЕКСТ ПАКЕТА ОДИН" },
        { task_id: "b", status: "awaiting_confirmation", candidates: 3, requires_confirmation: true, replacements: { persons: 1 }, ner_used: true, prompt: "ТЕКСТ ПАКЕТА ДВА" },
      ],
    };
    let confirmed = false;
    serve((url, method) => {
      if (url.endsWith(`/matching/runs/${run.run_id}/confirm`) && method === "POST") {
        confirmed = true;
        return json({ ...run, status: "running" });
      }
      if (url.endsWith(`/matching/runs/${run.run_id}`)) return json(run);
      if (url.endsWith("/needs/AN-1/match") && method === "POST")
        return json(state({ status: "awaiting_confirmation", run_id: run.run_id, items: [], total: 0, calculated_at: null }), 202);
      if (url.includes("/needs/AN-1/match?"))
        return json(confirmed ? state() : state({ status: "awaiting_confirmation", run_id: run.run_id, items: [], total: 0, calculated_at: null }));
    });
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "Сопоставить AN-1" }));
    const dialog = await screen.findByRole("dialog", { name: /Предпросмотр/ });
    await screen.findByText("Провайдер:", { exact: false });
    expect(within(dialog).getAllByText(/Пакет \d/)).toHaveLength(2);
    expect(called("POST", "/confirm")).toHaveLength(0);

    // закрытие окна ничего не отправляет
    await userEvent.click(within(dialog).getByRole("button", { name: "Отложить" }));
    expect(called("POST", "/confirm")).toHaveLength(0);
    expect(screen.getByText(/ничего не отправлено/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Проверить и отправить" }));
    await userEvent.click(await screen.findByRole("button", { name: "Подтвердить и отправить" }));
    await waitFor(() => expect(called("POST", "/confirm")).toHaveLength(1));
    expect(await screen.findByText("Тестов Тест")).toBeInTheDocument();
  });

  it("«Отложить» и «Отменить расчёт» различаются подсказками; отложить cancel не вызывает", async () => {
    const run: RunOut = {
      run_id: "33333333-3333-3333-3333-333333333333",
      kind: "match",
      stage: "deep",
      status: "awaiting_confirmation",
      requires_confirmation: true,
      is_external: true,
      provider_name: "Внешняя",
      candidates: 1,
      replacements: {},
      tasks: [],
    };
    serve((url) => {
      if (url.endsWith(`/matching/runs/${run.run_id}`)) return json(run);
      if (url.includes("/needs/AN-1/match?"))
        return json(state({ status: "awaiting_confirmation", run_id: run.run_id, items: [], total: 0, calculated_at: null }));
    });
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "1 кандидат, AN-1" }));
    const dialog = await screen.findByRole("dialog", { name: /Предпросмотр/ });
    expect(within(dialog).getByRole("button", { name: "Отложить" })).toHaveAttribute(
      "title",
      "Окно закроется, расчёт останется в ожидании подтверждения",
    );
    expect(within(dialog).getByRole("button", { name: "Отменить расчёт" })).toHaveAttribute(
      "title",
      expect.stringContaining("Прогон будет отменён, текст не будет отправлен"),
    );
    expect(within(dialog).queryByRole("button", { name: /Закрыть, не отправляя/ })).toBeNull();
    await userEvent.click(within(dialog).getByRole("button", { name: "Отложить" }));
    expect(called("POST", "/cancel")).toHaveLength(0);
    expect(called("POST", "/confirm")).toHaveLength(0);
  });

  describe("правка текста пакета", () => {
    const runId = "44444444-4444-4444-4444-444444444444";
    const mkRun = (prompt: string, edited = false): RunOut => ({
      run_id: runId,
      kind: "match",
      need_key: "AN-1",
      stage: "screening",
      status: "awaiting_confirmation",
      requires_confirmation: true,
      is_external: true,
      provider_name: "Внешняя модель",
      candidates: 1,
      replacements: { persons: 1 },
      tasks: [
        { task_id: "t1", status: "awaiting_confirmation", candidates: 1, requires_confirmation: true, replacements: { persons: 1 }, ner_used: true, prompt, editable: true, edited },
      ],
    });
    const open = async (put: (body: { prompt: string }) => Response) => {
      const current = mkRun("ИСХОДНЫЙ ТЕКСТ");
      serve((url, method, body) => {
        if (url.endsWith("/tasks/t1/prompt") && method === "PUT") return put(body as { prompt: string });
        if (url.endsWith(`/matching/runs/${runId}/confirm`) && method === "POST") return json({ ...current, status: "running" });
        if (url.endsWith(`/matching/runs/${runId}`)) return json(current);
        if (url.includes("/needs/AN-1/match?"))
          return json(state({ status: "awaiting_confirmation", run_id: runId, items: [], total: 0, calculated_at: null }));
      });
      renderAt("/needs");
      await userEvent.click(await screen.findByRole("button", { name: "1 кандидат, AN-1" }));
      const dialog = await screen.findByRole("dialog", { name: /Предпросмотр/ });
      const box = (await within(dialog).findByLabelText("Текст пакета 1")) as HTMLTextAreaElement;
      return { dialog, box };
    };

    it("текст редактируется; сохранение активно только при изменении; подтверждение блокируется до сохранения", async () => {
      const { dialog, box } = await open((b) => json(mkRun(b.prompt, true)));
      expect(box.tagName).toBe("TEXTAREA");
      expect(box).toHaveValue("ИСХОДНЫЙ ТЕКСТ");
      expect(within(dialog).getByText(/проверяются на остатки персональных данных/)).toBeInTheDocument();
      const save = within(dialog).getByRole("button", { name: "Сохранить правки пакета 1" });
      const confirm = within(dialog).getByRole("button", { name: "Подтвердить и отправить" });
      expect(save).toBeDisabled();
      expect(confirm).toBeEnabled();

      await userEvent.type(box, " плюс");
      expect(save).toBeEnabled();
      expect(confirm).toBeDisabled();
      expect(within(dialog).getByText(/изменено, не сохранено/)).toBeInTheDocument();

      await userEvent.click(save);
      await waitFor(() => expect(called("PUT", "/tasks/t1/prompt")).toHaveLength(1));
      expect(called("PUT", "/tasks/t1/prompt")[0].body).toEqual({ prompt: "ИСХОДНЫЙ ТЕКСТ плюс" });
      await waitFor(() => expect(confirm).toBeEnabled());
      expect(save).toBeDisabled();
      expect(within(dialog).getByLabelText("Текст пакета 1")).toHaveValue("ИСХОДНЫЙ ТЕКСТ плюс");
      expect(within(dialog).getByText("изменено")).toBeInTheDocument();
    });

    it("422: сообщение и найденные фрагменты, введённый текст остаётся, подтверждение заблокировано", async () => {
      const { dialog, box } = await open(() =>
        json({ detail: { message: "В тексте остались необезличенные фрагменты", fragments: ["Иванов И. И.", "ООО Ромашка"] } }, 422),
      );
      await userEvent.clear(box);
      await userEvent.type(box, "Иванов И. И. работал");
      await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить правки пакета 1" }));
      expect(await within(dialog).findByText("В тексте остались необезличенные фрагменты")).toBeInTheDocument();
      expect(within(dialog).getByText("Иванов И. И.")).toBeInTheDocument();
      expect(within(dialog).getByText("ООО Ромашка")).toBeInTheDocument();
      expect(within(dialog).getByLabelText("Текст пакета 1")).toHaveValue("Иванов И. И. работал");
      expect(within(dialog).getByRole("button", { name: "Подтвердить и отправить" })).toBeDisabled();
    });

    it("409: текст сообщения показывается, правка не теряется", async () => {
      const { dialog, box } = await open(() => json({ detail: "Прогон уже подтверждён" }, 409));
      await userEvent.type(box, "!");
      await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить правки пакета 1" }));
      expect(await within(dialog).findByText("Прогон уже подтверждён")).toBeInTheDocument();
      expect(within(dialog).getByLabelText("Текст пакета 1")).toHaveValue("ИСХОДНЫЙ ТЕКСТ!");
    });

    it("без editable текст только для чтения", async () => {
      const run = mkRun("ТОЛЬКО ЧТЕНИЕ");
      run.tasks[0].editable = false;
      serve((url) => {
        if (url.endsWith(`/matching/runs/${runId}`)) return json(run);
        if (url.includes("/needs/AN-1/match?"))
          return json(state({ status: "awaiting_confirmation", run_id: runId, items: [], total: 0, calculated_at: null }));
      });
      renderAt("/needs");
      await userEvent.click(await screen.findByRole("button", { name: "1 кандидат, AN-1" }));
      const dialog = await screen.findByRole("dialog", { name: /Предпросмотр/ });
      await within(dialog).findByText("ТОЛЬКО ЧТЕНИЕ");
      expect(within(dialog).queryByRole("textbox")).toBeNull();
    });
  });

  it("отмена прогона вызывает cancel", async () => {
    const run: RunOut = {
      run_id: "22222222-2222-2222-2222-222222222222",
      kind: "match",
      stage: "deep",
      status: "awaiting_confirmation",
      requires_confirmation: true,
      is_external: true,
      provider_name: "Внешняя",
      candidates: 1,
      replacements: {},
      tasks: [],
    };
    serve((url, method) => {
      if (url.endsWith("/cancel") && method === "POST") return json({ ...run, status: "failed" });
      if (url.endsWith(`/matching/runs/${run.run_id}`)) return json(run);
      if (url.includes("/needs/AN-1/match?"))
        return json(state({ status: "awaiting_confirmation", run_id: run.run_id, items: [], total: 0, calculated_at: null }));
    });
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "1 кандидат, AN-1" }));
    const dialog = await screen.findByRole("dialog", { name: /Предпросмотр/ });
    await userEvent.click(within(dialog).getByRole("button", { name: "Отменить расчёт" }));
    await waitFor(() => expect(called("POST", "/cancel")).toHaveLength(1));
    expect(called("POST", "/confirm")).toHaveLength(0);
  });

  it("таймаут 120 с: сообщение и «Повторить» вызывает retry задачи", async () => {
    serve((url, method) => {
      if (url.endsWith("/llm/tasks/t-9/retry") && method === "POST") return json({ id: "t-9", status: "queued" });
      if (url.includes("/needs/AN-1/match?"))
        return json(
          state({
            status: "failed",
            items: [],
            total: 0,
            calculated_at: null,
            tasks: [task({ task_id: "t-9", status: "timeout", batch_status: "failed" })],
          }),
        );
    });
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "1 кандидат, AN-1" }));
    expect(await screen.findByText(/не ответила за 120 с/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(called("POST", "/llm/tasks/t-9/retry")).toHaveLength(1));
  });

  it("пустой результат: сообщение и подсказка про «Кандидатов нет»", async () => {
    serve((url) => {
      if (url.includes("/needs/AN-1/match?")) return json(state({ items: [], total: 0, evaluated: 0, candidates_total: 0 }));
    });
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "1 кандидат, AN-1" }));
    expect(await screen.findByText(/Подходящих кандидатов нет/)).toBeInTheDocument();
  });
});

describe("Резюме: поиск по смыслу и технологии", () => {
  it("запрос короче 3 знаков не отправляется; поиск шлёт фильтры; fallback без процента, notice виден", async () => {
    serve((url, method) => {
      if (url.endsWith("/resume/semantic-search") && method === "POST")
        return json(
          search({
            mode: "fallback",
            status: "unavailable",
            notice: "LLM недоступна, показан список по технологиям",
            items: [sitem({ source: "fallback", percent: null, full_name: "Без процента" })],
            total: 1,
          }),
          202,
        );
    });
    renderAt("/resumes");
    const box = await screen.findByLabelText("Поиск по смыслу (LLM)");
    const btn = screen.getByRole("button", { name: "Найти по смыслу" });
    await userEvent.type(box, "ab");
    expect(btn).toBeDisabled();
    await userEvent.type(box, "c 1С");
    expect(btn).toBeEnabled();
    await userEvent.click(btn);
    expect(await screen.findByText(/LLM недоступна/)).toBeInTheDocument();
    const row = screen.getByText("Без процента").closest("tr")!;
    expect(within(row).getByText("без оценки")).toBeInTheDocument();
    expect(within(row).queryByText(/%/)).not.toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Назначить" })).not.toBeInTheDocument();
    const body = called("POST", "/resume/semantic-search")[0].body as Record<string, unknown>;
    expect(body.query).toBe("abc 1С");
    expect(body.match).toBe("all");
  });

  it("результат LLM: %, предварительная оценка скрининга, «Оценить следующих»", async () => {
    serve((url, method) => {
      if (url.endsWith("/resume/semantic-search/next") && method === "POST") return json(search({ status: "running" }), 202);
      if (url.endsWith("/resume/semantic-search") && method === "POST")
        return json(
          search({
            can_evaluate_next: true,
            remaining: 1,
            deep_size: 30,
            items: [
              sitem(),
              sitem({ employee_key: "EMP-2", full_name: "Второй", source: "screening", percent: null, screen_percent: 40 }),
            ],
          }),
          202,
        );
    });
    renderAt("/resumes");
    await userEvent.type(await screen.findByLabelText("Поиск по смыслу (LLM)"), "аналитик");
    await userEvent.click(screen.getByRole("button", { name: "Найти по смыслу" }));
    expect(await screen.findByText("90%")).toBeInTheDocument();
    const second = screen.getByText("Второй").closest("tr")!;
    expect(within(second).getByText("40%")).toBeInTheDocument();
    expect(within(second).getByText("предварительно")).toBeInTheDocument();
    // «Чего не хватает»: только у оценки LLM; у предварительной оценки блока нет
    expect(screen.getByText("Нажмите «Пересчитать», чтобы увидеть, чего не хватает")).toBeInTheDocument();
    expect(within(second).queryByText(/чего не хватает/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Оценить следующих/ }));
    await waitFor(() => expect(called("POST", "/semantic-search/next")).toHaveLength(1));
  });

  it("фильтр «Технологии»: подсказки, мульти-выбор, запрос к backend с match=all", async () => {
    serve((url) => {
      if (url.includes("/resume/employees?"))
        return json({ items: [{ employee_key: "EMP-1", full_name: "Тестов Тест", status: "Свободен", main_cv_file_id: 1, skills: ["1c", "rest"], skills_status: "ok" }], total: 1 });
    });
    renderAt("/resumes");
    const input = await screen.findByLabelText("Технологии");
    await userEvent.type(input, "1c{Enter}");
    await userEvent.type(input, "rest{Enter}");
    expect(screen.getByRole("button", { name: "Убрать 1c" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Убрать rest" })).toBeInTheDocument();
    // при 2+ технологиях виден переключатель «Все выбранные» (по умолчанию включён)
    expect(screen.getByRole("switch", { name: "Все выбранные" })).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Применить" }));
    await waitFor(() => {
      const last = calls.filter((c) => c.url.includes("/resume/employees?")).pop()!;
      expect(last.url).toContain("technology=1c");
      expect(last.url).toContain("technology=rest");
      expect(last.url).toContain("match=all");
    });
    expect(await screen.findByText("1c, rest")).toBeInTheDocument();
  });

  it("«Все выбранные» выключен: match=any; поиск по смыслу: счётчик N / 500, «Найти» от 3 знаков", async () => {
    serve((url) => {
      if (url.includes("/resume/employees?")) return json({ items: [], total: 0 });
    });
    renderAt("/resumes");
    const input = await screen.findByLabelText("Технологии");
    expect(screen.queryByRole("switch", { name: "Все выбранные" })).not.toBeInTheDocument();
    await userEvent.type(input, "1c{Enter}");
    await userEvent.type(input, "rest{Enter}");
    await userEvent.click(screen.getByRole("switch", { name: "Все выбранные" }));
    await userEvent.click(screen.getByRole("button", { name: "Применить" }));
    await waitFor(() => {
      const last = calls.filter((c) => c.url.includes("/resume/employees?")).pop()!;
      expect(last.url).toContain("match=any");
    });
    const go = screen.getByRole("button", { name: "Найти по смыслу" });
    expect(screen.getByText("0 / 500")).toBeInTheDocument();
    expect(go).toBeDisabled();
    const field = screen.getByLabelText("Поиск по смыслу (LLM)") as HTMLInputElement;
    expect(field.tagName).toBe("INPUT");
    expect(field.type).toBe("text");
    expect(field.maxLength).toBe(500);
    expect(field.parentElement).toBe(go.parentElement);
    await userEvent.type(screen.getByLabelText("Поиск по смыслу (LLM)"), "1с");
    expect(screen.getByText("2 / 500")).toBeInTheDocument();
    expect(go).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Поиск по смыслу (LLM)"), "а");
    expect(go).toBeEnabled();
  });
});

describe("Профиль сотрудника (FR-003)", () => {
  it("разделы CV и годы опыта из GET .../profile", async () => {
    serve((url) => {
      if (url.endsWith("/resume/employees/EMP-1/profile"))
        return json({
          employee_key: "EMP-1",
          full_name: "Тестов Тест",
          status: "Свободен",
          assignable: true,
          files: [],
          skills: ["1c"],
          sections: { stack: "1С, REST", projects: "Проект Альфа" },
          experience_years: 7,
          synced_at: "2026-10-09T10:00:00Z",
        });
      if (/\/cv\/employees\/EMP-1\/files$/.test(url)) return json({ employee_key: "EMP-1", files: [], archive: [] });
    });
    renderAt("/employees");
    await userEvent.click(await screen.findByRole("button", { name: /^Профиль: / }));
    expect(await screen.findByText("Основной стек")).toBeInTheDocument();
    expect(screen.getByText("Проект Альфа")).toBeInTheDocument();
    expect(screen.getByText(/Опыт: 7 лет/)).toBeInTheDocument();
  });
});
