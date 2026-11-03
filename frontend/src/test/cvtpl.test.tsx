import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import CvTemplateDialog from "../components/CvTemplateDialog";
import { AuthProvider } from "../auth/AuthContext";
import { setCsrfToken } from "../api/client";

// Диалог «CV по шаблону клиента» (FR-017, KON-80, decision.md approved):
// генерация без подтверждения запрещена; публикация письма - только после явного апрува.

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const CTX = {
  need_key: "ANALYTICS-5938",
  need_title: "Аналитик Банк Компаньон",
  client_epic_key: "SA-4160",
  client_name: "Банк Компаньон",
  main_cv: {
    id: 7, kind: "main_cv", file_name: "CV_Власова.docx", client_epic_key: null,
    is_archived: false, uploaded_at: "2026-11-01T10:00:00", extraction_status: "ok",
  },
  repeat: null,
  saved: [],
};

const RESULT = JSON.stringify({
  cv_blocks: [
    { type: "heading", text: "Резюме кандидата" },
    { type: "para", text: "АНА ВЛАСОВА\nАналитик…" },
  ],
  recommendations: ["Подчеркнуть банковский домен"],
  letter: "Здравствуйте! Презентую кандидата…",
});

function doneTask(result: string, over: Record<string, unknown> = {}) {
  return {
    id: "t-1", purpose: "cv_template", status: "done", provider_id: 2, provider_name: "OpenCode",
    is_external: true, requires_confirmation: false, timeout_sec: 120, attempts: 1, result, ...over,
  };
}

const calls: { url: string; method?: string; body?: unknown }[] = [];
let llmTaskResponses: unknown[] = [];
let generateResponse: () => Response | Promise<Response>;
let publishResponse: () => Response | Promise<Response>;
let saveResponse: () => Response | Promise<Response>;
let fetchMock: ReturnType<typeof vi.fn>;

function setup(over: Partial<Record<keyof typeof CTX, unknown>> = {}) {
  calls.length = 0;
  llmTaskResponses = [doneTask(RESULT)];
  generateResponse = () => json({ task_id: "t-1", status: "queued", requires_confirmation: false });
  publishResponse = () => json({ published: true, need_key: CTX.need_key });
  saveResponse = () => json({ file_id: 99, file_name: "CV 99.docx", client_epic_key: CTX.client_epic_key });
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({
      url, method,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    });
    if (url.includes("/cvtpl/context")) return json({ ...CTX, ...over });
    if (url.endsWith("/llm/providers"))
      return json([
        { id: 1, name: "Qwen (внутренняя)", is_external: false, is_default: true },
        { id: 2, name: "OpenCode", is_external: true, is_default: false },
      ]);
    if (url.includes("/cvtpl/generate")) return generateResponse();
    if (url.includes("/llm/tasks/")) {
      const next = llmTaskResponses.shift();
      return json(next === undefined ? doneTask(RESULT) : next);
    }
    if (url.includes("/cvtpl/task/t-1/save")) return saveResponse();
    if (url.includes("/cvtpl/task/t-1/publish-letter")) return publishResponse();
    return json({ detail: "не найдено" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  setCsrfToken("FAKE_csrf");
  return render(
    <AuthProvider>
      <CvTemplateDialog
        needKey="ANALYTICS-5938"
        employeeKey="ANALYTICS-96"
        employeeName="Власова Анна Борисовна"
        onClose={() => undefined}
        onSaved={() => undefined}
      />
    </AuthProvider>,
  );
}

beforeEach(() => {
  try {
    localStorage.removeItem("cvtpl.providerId");
  } catch {
    /* окружение без storage */
  }
});

afterEach(() => {
  try {
    fetchMock?.mockClear();
    vi.unstubAllGlobals();
    localStorage.removeItem("cvtpl.providerId");
  } catch {
    /* окружение без storage */
  }
});

function exists(match: string) {
  return (_: unknown, el: Element | null) => (el?.textContent ?? "").includes(match);
}

async function confirmInputs() {
  const input = screen.getByLabelText("Файл шаблона клиента") as HTMLInputElement;
  await userEvent.upload(input, new File(["x"], "Шаблон_Клиент.docx"));
  await userEvent.click(screen.getByRole("checkbox", { name: "Использую как шаблон" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Подтверждаю вход" }));
}

describe("CV по шаблону (KON-80)", () => {
  it("генерация недоступна, пока не подтверждены оба входа (критерий п. 2)", async () => {
    setup();
    await screen.findByText("Входы");
    const btn = screen.getByRole("button", { name: "Сгенерировать" }) as HTMLButtonElement;
    expect(btn).toBeDisabled();
    await confirmInputs();
    expect(btn).toBeEnabled();
  });

  it("результат: предпросмотр, рекомендации; публикация письма после апрува (п. 7)", async () => {
    setup();
    await screen.findByText("Входы");
    await confirmInputs();
    await userEvent.click(screen.getByRole("button", { name: "Сгенерировать" }));
    console.log("[dbg] calls:", calls.map((c) => c.method + " " + c.url));
    await waitFor(() => expect(screen.getAllByText(exists("АНА ВЛАСОВА")).length).toBeGreaterThan(0));
    expect(screen.getAllByText(exists("Подчеркнуть банковский домен")).length).toBeGreaterThan(0);

    const publish = screen.getByRole("button", { name: "Опубликовать письмо в Jira" }) as HTMLButtonElement;
    expect(publish.disabled).toBe(true);
    await userEvent.click(screen.getByRole("checkbox", { name: /Я проверил\(а\) письмо/ }));
    expect(publish.disabled).toBe(false);

    await userEvent.click(publish);
    await waitFor(() => expect(screen.getByText("Опубликовано")).toBeInTheDocument());
    expect(calls.find((c) => c.url.includes("/publish-letter"))?.body).toEqual({
      need_key: "ANALYTICS-5938", letter: "Здравствуйте! Презентую кандидата…", confirmed: true,
    });
  });

  it("сохранение CV файлом (п. 8)", async () => {
    setup();
    await screen.findByText("Входы");
    await confirmInputs();
    await userEvent.click(screen.getByRole("button", { name: "Сгенерировать" }));
    await waitFor(
      () => expect(screen.getAllByText(exists("АНА ВЛАСОВА")).length).toBeGreaterThan(0),
    );
    const save = screen.getByRole("button", { name: "Сохранить CV" }) as HTMLButtonElement;
    await userEvent.click(save);
    await waitFor(
      () => expect(screen.getAllByText(exists("Файл сохранён")).length).toBeGreaterThan(0),
      { timeout: 5000 },
    );
    expect(save.disabled).toBe(true);
    expect(calls.find((c) => c.url.includes("/save"))?.body).toEqual({
      need_key: "ANALYTICS-5938", employee_key: "ANALYTICS-96",
    });
  });

  it("повтор по паре (BR-007): баннер показан и не блокирует генерацию", async () => {
    setup({
      ...CTX,
      repeat: { file_id: 55, file_name: "CV стар.docx", uploaded_at: "2026-09-18T00:00:00" },
      saved: [{
        id: 55, kind: "client_template_cv", file_name: "CV стар.docx", client_epic_key: "SA-4160",
        is_archived: false, uploaded_at: "2026-09-18T00:00:00", extraction_status: "ok",
      }],
    } as unknown as typeof CTX);
    await screen.findByText(/уже есть CV по шаблону/);
    await confirmInputs();
    expect((screen.getByRole("button", { name: "Сгенерировать" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("ошибка LLM: текст как есть, возврат к шагу файлов (п. 6)", async () => {
    setup();
    await screen.findByText("Входы");
    generateResponse = () => json({ task_id: "t-1", status: "queued", requires_confirmation: false });
    llmTaskResponses = [{
      ...(doneTask("")), status: "failed", error_code: "provider_error",
      error_message: "Провайдер LLM вернул ошибку HTTP 402.",
    }];
    await confirmInputs();
    await userEvent.click(screen.getByRole("button", { name: "Сгенерировать" }));
    await waitFor(
      () => expect(screen.getAllByText(exists("Провайдер LLM вернул ошибку HTTP 402")).length).toBeGreaterThan(0),
      { timeout: 8000 },
    );
    expect(screen.getByText("Входы")).toBeInTheDocument();
  }, 20000);

  it("внешний провайдер (FR-023): явное подтверждение обезличенной отправки", async () => {
    setup();
    await screen.findByText("Входы");
    generateResponse = () => json({ task_id: "t-1", status: "awaiting_confirmation", requires_confirmation: true });
    llmTaskResponses = [doneTask(RESULT)];
    await confirmInputs();
    await userEvent.click(screen.getByRole("button", { name: "Сгенерировать" }));
    // шаг подтверждения: ничего не отправилось до явного клика
    await screen.findByText(/Отправка во внешнюю LLM/);
    expect(calls.find((c) => c.url.includes("/confirm"))).toBeFalsy();
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить и отправить" }));
    await waitFor(
      () => expect(screen.getAllByText(exists("АНА ВЛАСОВА")).length).toBeGreaterThan(0),
      { timeout: 5000 },
    );
    expect(calls.find((c) => c.url.includes("/confirm"))).toBeTruthy();
  });

  it("не-JSON ответ LLM: показан как CV, письма нет - публикация недоступна", async () => {
    setup();
    await screen.findByText("Входы");
    llmTaskResponses = [doneTask("Вот готовый CV без Jason-обёртки")];
    await confirmInputs();
    await userEvent.click(screen.getByRole("button", { name: "Сгенерировать" }));
    await waitFor(
      () => expect(screen.getAllByText(exists("Вот готовый CV")).length).toBeGreaterThan(0),
      { timeout: 5000 },
    );
    expect((screen.getByRole("button", { name: "Опубликовать письмо в Jira" }) as HTMLButtonElement).disabled)
      .toBe(true);
  });
});
