import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { ProviderOut } from "../api/types";

const FAKE_KEY = "FAKE_api_key_for_tests";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const fetchMock = vi.fn();
type Call = { url: string; method: string; body: Record<string, unknown> | undefined };
let calls: Call[];

function provider(over: Partial<ProviderOut> = {}): ProviderOut {
  return {
    id: 1,
    name: "Корпоративная Qwen",
    is_external: false,
    is_default: true,
    url: "http://llm.internal/v1",
    timeout_sec: 120,
    model: "qwen",
    has_key: true,
    updated_at: "2026-10-09T10:00:00Z",
    ...over,
  };
}

const SETTINGS = {
  deep_size: 30,
  deep_size_min: 5,
  deep_size_max: 100,
  screen_batch: 50,
  screen_batch_min: 10,
  screen_batch_max: 100,
};

let providers: ProviderOut[];

function setup(role = "admin") {
  providers = [provider()];
  calls = [];
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body });
    if (url === "/api/auth/me")
      return json({ login: "boss", role, jira_status: { state: "ok" }, sections: [], csrf_token: "FAKE_csrf" });
    if (url === "/api/admin/users") return json([]);
    if (url === "/api/admin/llm/providers" && method === "GET") return json(providers);
    if (url === "/api/admin/llm/providers" && method === "POST") {
      const p = provider({ id: 2, name: String(body?.name), is_external: true, is_default: false, has_key: !!body?.api_key });
      providers = [...providers, p];
      return json(p, 201);
    }
    if (url === "/api/admin/llm/providers/1" && method === "PATCH") return json(provider({ model: String(body?.model) }));
    if (url === "/api/admin/llm/providers/2" && method === "DELETE") {
      providers = providers.filter((p) => p.id !== 2);
      return json(undefined, 204);
    }
    if (url === "/api/admin/llm/dictionary" && method === "GET")
      return json({ persons: ["Иван Иванов"], companies: [], projects: [], locations: [] });
    if (url === "/api/admin/llm/dictionary" && method === "PUT") return json(body);
    if (url === "/api/admin/matching/settings" && method === "GET") return json(SETTINGS);
    if (url === "/api/admin/matching/settings" && method === "PUT") return json({ ...SETTINGS, ...body });
    return json({ detail: "не найдено" }, 404);
  });
  return render(
    <MemoryRouter initialEntries={["/settings"]}>
      <App />
    </MemoryRouter>,
  );
}

const called = (method: string, url: string) => calls.filter((c) => c.method === method && c.url === url);

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("Настройки: LLM", () => {
  it("список провайдеров: тип, модель, таймаут, ключ только как «задан»", async () => {
    setup();
    await userEvent.click(await screen.findByRole("tab", { name: "LLM" }));
    const row = (await screen.findByText("Корпоративная Qwen")).closest("tr")!;
    expect(within(row).getByText("Внутренний")).toBeInTheDocument();
    expect(within(row).getByText("задан")).toBeInTheDocument();
    expect(within(row).getByText("120")).toBeInTheDocument();
  });

  it("не-админу вкладка недоступна", async () => {
    setup("user");
    expect(await screen.findByRole("heading", { name: "Нет доступа" })).toBeInTheDocument();
    expect(called("GET", "/api/admin/llm/providers")).toHaveLength(0);
  });

  it("внешний провайдер по http отклоняется без запроса; по https создаётся, ключ не возвращается в поле", async () => {
    setup();
    await userEvent.click(await screen.findByRole("tab", { name: "LLM" }));
    await userEvent.click(await screen.findByRole("button", { name: "Добавить провайдера" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Название"), "Внешняя");
    await userEvent.type(within(dialog).getByLabelText(/^URL/), "http://api.example.test/v1");
    await userEvent.type(within(dialog).getByLabelText("Ключ API"), FAKE_KEY);
    await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/только HTTPS/);
    expect(called("POST", "/api/admin/llm/providers")).toHaveLength(0);

    const url = within(dialog).getByLabelText(/^URL/);
    await userEvent.clear(url);
    await userEvent.type(url, "https://api.example.test/v1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(called("POST", "/api/admin/llm/providers")).toHaveLength(1));
    const sent = called("POST", "/api/admin/llm/providers")[0].body!;
    expect(sent).toMatchObject({ name: "Внешняя", url: "https://api.example.test/v1", is_external: true, timeout_sec: 120 });
    expect(sent.api_key).toBe(FAKE_KEY);
    expect(await screen.findByText("Внешняя")).toBeInTheDocument();
    // ключ нигде не отображается
    expect(document.body.textContent).not.toContain(FAKE_KEY);
  });

  it("изменение без нового ключа не шлёт api_key", async () => {
    setup();
    await userEvent.click(await screen.findByRole("tab", { name: "LLM" }));
    const row = (await screen.findByText("Корпоративная Qwen")).closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Изменить" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/Новый ключ API/)).toHaveValue("");
    await userEvent.clear(within(dialog).getByLabelText("Модель"));
    await userEvent.type(within(dialog).getByLabelText("Модель"), "qwen-2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.url).toBe("/api/admin/llm/providers/1");
    expect(patch.body).toMatchObject({ model: "qwen-2" });
    expect(patch.body).not.toHaveProperty("api_key");
  });

  it("словарь обезличивания: PUT с очищенными терминами; настройки сопоставления с границами", async () => {
    setup();
    await userEvent.click(await screen.findByRole("tab", { name: "LLM" }));
    const persons = await screen.findByLabelText("Персоны (ФИО)");
    await waitFor(() => expect(persons).toHaveValue("Иван Иванов"));
    await userEvent.type(persons, "{Enter}  Пётр Петров {Enter}иван иванов");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить словарь" }));
    await waitFor(() => expect(called("PUT", "/api/admin/llm/dictionary")).toHaveLength(1));
    expect(called("PUT", "/api/admin/llm/dictionary")[0].body).toMatchObject({ persons: ["Иван Иванов", "Пётр Петров"] });

    const deep = await screen.findByLabelText(/Размер глубокой оценки/);
    await userEvent.clear(deep);
    await userEvent.type(deep, "500");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить настройки" }));
    expect(await screen.findByText(/от 5 до 100/)).toBeInTheDocument();
    expect(called("PUT", "/api/admin/matching/settings")).toHaveLength(0);

    await userEvent.clear(deep);
    await userEvent.type(deep, "40");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить настройки" }));
    await waitFor(() => expect(called("PUT", "/api/admin/matching/settings")).toHaveLength(1));
    expect(called("PUT", "/api/admin/matching/settings")[0].body).toEqual({ deep_size: 40, screen_batch: 50 });
  });
});
