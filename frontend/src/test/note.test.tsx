import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { EmployeeOut } from "../api/types";
import { noteLength, pluralSign } from "../lib/note";

const ME = { login: "test.user", role: "user", jira_status: { state: "ok" }, sections: [], csrf_token: "FAKE_csrf" };
const STAMP = { scope: "employees", state: "ok", stale: false, last_success_at: "2026-10-09T10:00:00Z", interval_sec: 3600 };
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function emp(over: Partial<EmployeeOut> = {}): EmployeeOut {
  return {
    jira_key: "EMP-1",
    full_name: "Тестов Тест",
    status: "Свободен",
    assignable: true,
    synced_at: "2026-10-09T10:00:00Z",
    ...over,
  };
}

const fetchMock = vi.fn();
let puts: { url: string; body: { note: string } }[];
let putResponse: () => Response | Promise<Response>;

function setup(e: EmployeeOut) {
  puts = [];
  putResponse = () => json({ note: "x", note_author: "test.user", note_updated_at: "2026-10-21T10:00:00Z" });
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      puts.push({ url, body: JSON.parse(String(init.body)) });
      return putResponse();
    }
    if (url.endsWith("/auth/me")) return json(ME);
    if (url.includes("/sync/employees?"))
      return json({ items: [e], total: 1, limit: 500, offset: 0, statuses: [{ status: e.status, count: 1 }], sync: STAMP });
    if (url.endsWith("/cv/limits")) return json({ max_file_mb: 10, max_file_bytes: 1, upload_kinds: {}, extensions_by_kind: {} });
    if (url.endsWith("/cv/files")) return json({ items: [] });
    return json({ detail: "не найдено" }, 404);
  });
  return render(
    <MemoryRouter initialEntries={["/resumes"]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const openEditor = async () => {
  const btn = await screen.findByRole("button", { name: "Изменить заметку: Тестов Тест" });
  await userEvent.click(btn);
  return { btn, dlg: await screen.findByRole("dialog") };
};

describe("lib/note", () => {
  it("лимит считается кодпоинтами, как на сервере (эмодзи = 1 знак)", () => {
    expect(noteLength("a😀b")).toBe(3);
    expect("a😀b".length).toBe(4);
    expect(noteLength("")).toBe(0);
  });
  it("склонение знак/знака/знаков", () => {
    expect([1, 2, 5, 11, 14, 21, 22, 25, 111].map(pluralSign)).toEqual([
      "знак", "знака", "знаков", "знаков", "знаков", "знак", "знака", "знаков", "знаков",
    ]);
  });
});

describe("Резюме: редактор заметки (KON-114)", () => {
  it("открытие: заголовок, автор и дата в окне, фокус в поле, сохранить недоступна; Отмена закрывает и возвращает фокус", async () => {
    setup(emp({ note: "Старая", note_author: "boss", note_updated_at: "2026-10-05T10:00:00Z" }));
    const { btn, dlg } = await openEditor();
    expect(within(dlg).getByRole("heading", { name: "Заметка: Тестов Тест" })).toBeInTheDocument();
    expect(within(dlg).getByText(/^Изменена: boss, 05\.10\.26, \d{2}:\d{2}$/)).toBeInTheDocument();
    const ta = within(dlg).getByLabelText("Заметка") as HTMLTextAreaElement;
    expect(ta).toHaveFocus();
    expect(ta.value).toBe("Старая");
    expect(ta.selectionStart).toBe(6);
    expect(within(dlg).getByText("6 / 2000")).toBeInTheDocument();
    expect(within(dlg).getByText("Учитывается при сопоставлении с потребностью")).toBeInTheDocument();
    expect(within(dlg).getByRole("button", { name: "Сохранить" })).toBeDisabled();
    await userEvent.click(within(dlg).getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(puts).toHaveLength(0);
    expect(btn).toHaveFocus();
  });

  it("пустая заметка: «Заметки пока нет», «Очистить» недоступна", async () => {
    setup(emp());
    const { dlg } = await openEditor();
    expect(within(dlg).getByText("Заметки пока нет")).toBeInTheDocument();
    expect(within(dlg).getByRole("button", { name: "Очистить" })).toBeDisabled();
  });

  it("лимит: 2000 можно сохранить, 2001 блокируется с текстом и склонением", async () => {
    setup(emp());
    const { dlg } = await openEditor();
    const ta = within(dlg).getByLabelText("Заметка");
    fireEvent.change(ta, { target: { value: "а".repeat(2000) } });
    expect(within(dlg).getByText("2000 / 2000")).toBeInTheDocument();
    expect(within(dlg).getByRole("button", { name: "Сохранить" })).toBeEnabled();
    fireEvent.change(ta, { target: { value: "а".repeat(2001) } });
    expect(within(dlg).getByText("Сократите текст на 1 знак")).toBeInTheDocument();
    expect(within(dlg).queryByText("Учитывается при сопоставлении с потребностью")).toBeNull();
    expect(within(dlg).getByRole("button", { name: "Сохранить" })).toBeDisabled();
    fireEvent.change(ta, { target: { value: "а".repeat(2005) } });
    expect(within(dlg).getByText("Сократите текст на 5 знаков")).toBeInTheDocument();
    fireEvent.change(ta, { target: { value: "😀".repeat(2000) } }); // эмодзи - один знак
    expect(within(dlg).getByRole("button", { name: "Сохранить" })).toBeEnabled();
    fireEvent.change(ta, { target: { value: "а".repeat(1900) } });
    expect(within(dlg).getByText("1900 / 2000")).toHaveClass("is-warn");
  });

  it("сохранение: PUT с текстом, окно закрыто, в таблице новый текст без автора, фокус на «Изменить», озвучка", async () => {
    setup(emp());
    putResponse = () => json({ note: "Новая", note_author: "test.user", note_updated_at: "2026-10-21T10:00:00Z" });
    const { btn, dlg } = await openEditor();
    await userEvent.type(within(dlg).getByLabelText("Заметка"), "Новая");
    await userEvent.click(within(dlg).getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(puts).toHaveLength(1);
    expect(puts[0].url).toContain("/cv/employees/EMP-1/note");
    expect(puts[0].body).toEqual({ note: "Новая" });
    const cell = btn.closest("td")!;
    expect(cell.textContent).toContain("Новая");
    expect(cell.textContent).not.toContain("test.user");
    expect(btn).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent("Сохранено");
    await userEvent.click(btn); // повторное открытие показывает автора и дату из ответа
    expect(await screen.findByText(/^Изменена: test\.user, 21\.10\.26/)).toBeInTheDocument();
  });

  it("Ctrl+Enter сохраняет только при активной кнопке", async () => {
    setup(emp());
    const { dlg } = await openEditor();
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    expect(puts).toHaveLength(0);
    await userEvent.type(within(dlg).getByLabelText("Заметка"), "текст");
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    await waitFor(() => expect(puts).toHaveLength(1));
  });

  it("очистка двухшаговая: «Очистить» не шлёт запрос, затем «Очистить заметку» шлёт пустую строку", async () => {
    setup(emp({ note: "Была", note_author: "boss", note_updated_at: "2026-10-05T10:00:00Z" }));
    putResponse = () => json({ note: "", note_author: "test.user", note_updated_at: "2026-10-21T10:00:00Z" });
    const { btn, dlg } = await openEditor();
    await userEvent.click(within(dlg).getByRole("button", { name: "Очистить" }));
    expect(puts).toHaveLength(0);
    expect((within(dlg).getByLabelText("Заметка") as HTMLTextAreaElement).value).toBe("");
    expect(within(dlg).queryByRole("button", { name: "Сохранить" })).toBeNull();
    await userEvent.click(within(dlg).getByRole("button", { name: "Очистить заметку" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(puts[0].body).toEqual({ note: "" });
    expect(btn.closest("td")!.textContent).toContain("нет заметки");
    expect(screen.getByRole("status")).toHaveTextContent("Заметка очищена");
  });

  it("ошибка 404: текст ответа как есть, введённое сохранено, окно открыто, повтор возможен", async () => {
    setup(emp());
    putResponse = () => json({ detail: "Сотрудник не найден" }, 404);
    const { dlg } = await openEditor();
    const ta = within(dlg).getByLabelText("Заметка") as HTMLTextAreaElement;
    await userEvent.type(ta, "Введено");
    await userEvent.click(within(dlg).getByRole("button", { name: "Сохранить" }));
    expect(await within(dlg).findByRole("alert")).toHaveTextContent("Сотрудник не найден");
    expect(ta.value).toBe("Введено");
    expect(within(dlg).getByRole("button", { name: "Сохранить" })).toBeEnabled();
    await waitFor(() => expect(ta).toHaveFocus());
  });

  it("во время сохранения Esc, × и кнопки недоступны; подложка при изменённом тексте не закрывает", async () => {
    setup(emp());
    let release: (r: Response) => void = () => undefined;
    putResponse = () => new Promise<Response>((res) => (release = res));
    const { dlg } = await openEditor();
    await userEvent.type(within(dlg).getByLabelText("Заметка"), "abc");
    fireEvent.mouseDown(dlg.parentElement!);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.click(within(dlg).getByRole("button", { name: "Сохранить" }));
    expect(within(dlg).getByText(/Сохраняем…/)).toBeInTheDocument();
    expect(within(dlg).getByRole("button", { name: "Закрыть" })).toBeDisabled();
    expect(within(dlg).getByRole("button", { name: "Отмена" })).toBeDisabled();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await act(async () => release(json({ note: "abc", note_author: "u", note_updated_at: "2026-10-21T10:00:00Z" })));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("Esc и подложка закрывают при неизменённом тексте", async () => {
    setup(emp());
    const { dlg } = await openEditor();
    fireEvent.mouseDown(dlg.parentElement!);
    expect(screen.queryByRole("dialog")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Изменить заметку: Тестов Тест" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
