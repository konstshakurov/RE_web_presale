import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ClientRules from "../components/ClientRules";

// Тезисы правил клиента (KON-160): ok с источником и ссылкой; regenerating - «Обновляется…»;
// пусто (комментариев нет) - нейтральная подпись; повтор никакого LLM не дёргает (кэш).

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const EPIC = {
  epic_key: "SA-4160", name: "Банк Компаньон", summary: null,
  rules: [{ id: "f1", name: "Есть NDA", value: "Да" }],
  stale: false, fetched_at: "2026-11-05T09:00:00", needs_total: 3, error: null,
};

const THESIS_OK = {
  status: "ok", need_key: "ANALYTICS-5938", epic_key: "SA-4160",
  theses: [{
    text: "CV отправлять только с фотографией", contradicts_rules: false,
    sources: [
      { id: "101", author: "Иванов Иван", date: "2026-11-03T10:00:00",
        url: "https://jira.example.com/browse/SA-4160?commentId=101#comment-101" },
    ],
  }],
  generated_at: "2026-11-05T09:05:00", provider: "Qwen (внутренняя)", task_submitted: null,
};

const THESIS_REGEN = { ...THESIS_OK, status: "regenerating", task_submitted: true };

let requests: { url: string; method: string }[] = [];
let epicPromise: Promise<Response> | null = null;
let thesesMocks: Array<() => Response> = [];

function setup() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    requests.push({ url, method });
    if (url.includes("/sync/epics/")) return (epicPromise ?? json(EPIC)) as unknown as Response;
    if (url.includes("/needs/ANALYTICS-5978/client-rules/theses")) return thesesMocks.shift()!();
    return json({ detail: "не найдено" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return render(<ClientRules epicKey="SA-4160" needKey="ANALYTICS-5978" />);
}

afterEach(() => {
  vi.unstubAllGlobals();
  requests = [];
  epicPromise = null;
  thesesMocks = [];
});

describe("ClientRules (KON-160 тезисы)", () => {
  it("вариант B: тезис + бейдж источников; источники открываются по клику, подпись LLM", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    userEvent.setup();
    thesesMocks = [() => json(THESIS_OK)];
    setup();
    await waitFor(() => expect(screen.getByText(/CV отправлять только с фотографией/)).toBeTruthy());
    // скрытые источники: ссылка недоступна до раскрытия
    expect(screen.queryByRole("link", { name: /Иванов Иван/ })).toBeNull();
    expect(screen.getByText("1 источник")).toBeTruthy();
    const open = screen.getByRole("button", { name: /CV отправлять только с фотографией/ });
    await userEvent.click(open);
    const link = await screen.findByRole("link", { name: /Иванов Иван/ });
    expect(link.getAttribute("href")).toContain("commentId=101");
    expect(screen.getByText(/Сгенерировано LLM/)).toBeTruthy();
    expect(screen.queryByText("Обновляется…")).toBeNull();
    // повторный клик — источники скрыты
    await userEvent.click(open);
    expect(screen.queryByRole("link", { name: /Иванов Иван/ })).toBeNull();
  });

  it("пусто (нет комментариев) - нейтральная подпись", async () => {
    thesesMocks = [() => json({ ...THESIS_OK, status: "empty", theses: [] })];
    setup();
    await waitFor(() => expect(screen.getByText(/клиент ещё не писал в эпик/)).toBeTruthy());
    expect(screen.queryByText(/Сгенерировано LLM/)).toBeNull();
  });

  it("regenerating: показывается «Обновляется…», кэш не дёргает LLM повторно", async () => {
    thesesMocks = [() => json(THESIS_REGEN)];
    setup();
    await waitFor(() => expect(screen.getByText("Обновляется…")).toBeTruthy());
    // только один GET theses: авто-поллинг начнётся позже (8 c), таймеры в тесте не ждём
    expect(requests.filter((r) => r.url.includes("/client-rules/theses")).length).toBeLessThanOrEqual(1);
    // CR-фix: в тезисы передаётся ключ ПОТРЕБНОСТИ, а не эпика
    expect(requests.map((r) => r.url)).toContain("/api/needs/ANALYTICS-5978/client-rules/theses");
  });

  it("409 (потребность без клиента) - понятное сообщение вместо ошибки", async () => {
    thesesMocks = [() =>
      json({ detail: { code: "need_no_client", message: "У потребности не указан клиент (эпик)" } }, 409)];
    setup();
    await waitFor(() => expect(screen.getByText(/не указан клиент/)).toBeTruthy());
  });
});
