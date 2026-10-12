import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { EmployeeOut, EmployeesPage, FileOut, SyncStamp } from "../api/types";

const ME = { login: "test.user", role: "user", jira_status: { state: "ok" }, sections: [], csrf_token: "FAKE_csrf" };

const STAMP: SyncStamp = {
  scope: "employees",
  state: "ok",
  stale: false,
  last_success_at: "2026-10-09T10:00:00Z",
  interval_sec: 3600,
};

const LIMITS = {
  max_file_mb: 10,
  max_file_bytes: 10 * 1024 * 1024,
  upload_kinds: { main_cv: "Основное CV", extra_cv: "Доп. CV", materials: "Материалы" },
  extensions_by_kind: { main_cv: ["doc", "docx"], extra_cv: ["doc", "docx"], materials: ["doc", "docx", "pdf", "png"] },
};

function emp(over: Partial<EmployeeOut> = {}): EmployeeOut {
  return {
    jira_key: "EMP-1",
    jira_url: "https://jira.example/browse/EMP-1",
    full_name: "Тестов Тест",
    status: "Свободен",
    assignable: true,
    vacation_plan: "10.11-24.11",
    extra_experience: "Работал с ERP",
    synced_at: "2026-10-09T10:00:00Z",
    ...over,
  };
}

function file(over: Partial<FileOut> = {}): FileOut {
  return {
    id: 10,
    employee_key: "EMP-1",
    kind: "main_cv",
    kind_title: "Основное CV",
    file_name: "tester_cv.docx",
    mime_type: "x",
    size_bytes: 2048,
    is_archived: false,
    uploaded_by: "test.user",
    uploaded_at: "2026-10-01T10:00:00Z",
    extraction_status: "ok",
    can_delete: false,
    can_restore: false,
    ...over,
  };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const fetchMock = vi.fn();
type Calls = { url: string; init?: RequestInit }[];
let calls: Calls;

function employeesPage(items: EmployeeOut[]): EmployeesPage {
  const counts = new Map<string, number>();
  items.forEach((e) => counts.set(e.status, (counts.get(e.status) ?? 0) + 1));
  return {
    items,
    total: items.length,
    limit: 500,
    offset: 0,
    statuses: [...counts].map(([status, count]) => ({ status, count })),
    sync: STAMP,
  };
}

interface World {
  employees?: EmployeeOut[];
  files?: FileOut[];
  archive?: FileOut[];
  extra?: (url: string, init?: RequestInit) => Response | undefined;
}

function setup(w: World, path: string) {
  calls = [];
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const r = w.extra?.(url, init);
    if (r) return r;
    if (url.endsWith("/auth/me")) return json(ME);
    if (url.includes("/sync/employees?")) return json(employeesPage(w.employees ?? [emp()]));
    if (url.endsWith("/cv/limits")) return json(LIMITS);
    if (url.endsWith("/cv/files")) return json({ items: w.files ?? [] });
    if (/\/cv\/employees\/[^/]+\/files$/.test(url)) {
      return json({ employee_key: "EMP-1", files: w.files ?? [], archive: w.archive ?? [] });
    }
    return json({ detail: "не найдено" }, 404);
  });
  return render(
    <MemoryRouter initialEntries={[path]}>
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

describe("Сотрудники", () => {
  it("карточка: ФИО, статус, отпуска; фильтр по статусу", async () => {
    setup(
      {
        employees: [
          emp(),
          emp({ jira_key: "EMP-2", full_name: "Другой Сотрудник", status: "Декрет", assignable: false, vacation_plan: null }),
        ],
      },
      "/employees",
    );
    const card = await screen.findByRole("listitem", { name: "Тестов Тест" });
    expect(within(card).getByText("Свободен")).toBeInTheDocument();
    expect(within(card).getByText(/10\.11-24\.11/)).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: "Другой Сотрудник" })).toBeInTheDocument();
    // 10 статусов без «Уволен» (он - тумблер) + «Все»
    expect(within(screen.getByRole("navigation", { name: "Статусы сотрудников" })).getAllByRole("button")).toHaveLength(10);

    await userEvent.click(screen.getByRole("button", { name: /Декрет/ }));
    expect(screen.queryByRole("listitem", { name: "Тестов Тест" })).not.toBeInTheDocument();
    expect(screen.getByText("не назначается")).toBeInTheDocument();
  });

  it("тумблер «Показывать уволенных»: скрыты по умолчанию, работает вместе со статусом и поиском", async () => {
    setup(
      {
        employees: [
          emp(),
          emp({ jira_key: "EMP-2", full_name: "Иванов Иван", status: "Декрет" }),
          emp({ jira_key: "EMP-3", full_name: "Иванов Пётр", status: "Уволен" }),
        ],
      },
      "/employees",
    );
    await screen.findByRole("listitem", { name: "Тестов Тест" });
    const nav = screen.getByRole("navigation", { name: "Статусы сотрудников" });
    expect(within(nav).queryByRole("button", { name: /Уволен/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: "Иванов Пётр" })).not.toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: /Все/ })).toHaveTextContent("2");

    await userEvent.click(screen.getByRole("switch", { name: "Показывать уволенных" }));
    expect(screen.getByRole("listitem", { name: "Иванов Пётр" })).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: /Все/ })).toHaveTextContent("3");
    // вместе с поиском
    await userEvent.type(screen.getByLabelText("Поиск по ФИО и ключу"), "иванов");
    expect(screen.queryByRole("listitem", { name: "Тестов Тест" })).not.toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: "Иванов Пётр" })).toBeInTheDocument();
    // вместе со статусом
    await userEvent.click(within(nav).getByRole("button", { name: /Декрет/ }));
    expect(screen.queryByRole("listitem", { name: "Иванов Пётр" })).not.toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: "Иванов Иван" })).toBeInTheDocument();
  });

  it("профиль: отпуска, опыт, файлы и история отправок из потребностей", async () => {
    setup(
      {
        files: [file()],
        extra: (u) =>
          u.includes("/sync/needs?")
            ? json({
                items: [
                  {
                    jira_key: "AN-5",
                    title: "Аналитик",
                    status: "Кандидат назначен",
                    employee_key: "EMP-1",
                    rate: { amount: 100, currency: "RUB" },
                    jira_created_at: "2026-10-01T08:00:00Z",
                    jira_updated_at: "2026-10-02T08:00:00Z",
                  },
                  {
                    jira_key: "AN-6",
                    title: "Чужая",
                    status: "Поиск",
                    employee_key: "EMP-9",
                    rate: {},
                    jira_created_at: "2026-10-01T08:00:00Z",
                    jira_updated_at: "2026-10-02T08:00:00Z",
                  },
                ],
                total: 2,
                limit: 500,
                offset: 0,
                sync: STAMP,
              })
            : undefined,
      },
      "/employees",
    );
    await userEvent.click(await screen.findByRole("button", { name: "Профиль" }));
    const dlg = await screen.findByRole("dialog");
    expect(await within(dlg).findByText("tester_cv.docx")).toBeInTheDocument();
    expect(await within(dlg).findByText(/AN-5/)).toBeInTheDocument();
    expect(within(dlg).queryByText(/AN-6/)).not.toBeInTheDocument();
    expect(within(dlg).getByText("Работал с ERP")).toBeInTheDocument();
  });

  it("окно CV: текст, поиск с переходом между совпадениями", async () => {
    setup(
      {
        extra: (u) =>
          u.endsWith("/main-cv")
            ? json({ file: file(), extraction_status: "ok", text: "1С ERP опыт. Работа с erp и УТ." })
            : undefined,
      },
      "/employees",
    );
    await userEvent.click(await screen.findByRole("button", { name: "Резюме" }));
    const dlg = await screen.findByRole("dialog");
    expect(await within(dlg).findByText(/1С ERP опыт/)).toBeInTheDocument();
    expect(within(dlg).getByRole("link", { name: /Скачать/ })).toHaveAttribute("href", "/api/cv/files/10/download");
    await userEvent.type(within(dlg).getByLabelText("Поиск в резюме"), "erp");
    expect(within(dlg).getByText("1 из 2")).toBeInTheDocument();
    await userEvent.click(within(dlg).getByRole("button", { name: "Следующее совпадение" }));
    expect(within(dlg).getByText("2 из 2")).toBeInTheDocument();
    await userEvent.type(within(dlg).getByLabelText("Поиск в резюме"), "zzz");
    expect(within(dlg).getByText("не найдено")).toBeInTheDocument();
  });

  it("CV без извлечённого текста: сообщение, окно не пустое", async () => {
    setup(
      {
        extra: (u) =>
          u.endsWith("/main-cv")
            ? json({
                file: file({ extraction_status: "error" }),
                extraction_status: "error",
                text: null,
                message: "Файл повреждён",
              })
            : undefined,
      },
      "/employees",
    );
    await userEvent.click(await screen.findByRole("button", { name: "Резюме" }));
    expect(await screen.findByText("Файл повреждён")).toBeInTheDocument();
  });

  it("отказ Jira при «Обновить»: текст как есть", async () => {
    setup(
      {
        extra: (u, init) =>
          u.endsWith("/sync/refresh") && init?.method === "POST"
            ? json({ detail: { state: "no_permission", code: "x", message: "Нет прав на чтение Epic" } }, 403)
            : undefined,
      },
      "/employees",
    );
    await userEvent.click(await screen.findByRole("button", { name: "Обновить" }));
    expect(await screen.findByText(/Нет прав в Jira: Нет прав на чтение Epic/)).toBeInTheDocument();
  });
});

describe("Резюме", () => {
  it("таблица: файлы, содержание, заглушки технологий и поиска по смыслу, заметка с автором", async () => {
    setup(
      {
        employees: [
          emp({ note: "Не подключать к клиенту X", note_author: "test.user", note_updated_at: "2026-10-05T10:00:00Z" }),
        ],
        files: [file(), file({ id: 11, kind: "extra_cv", kind_title: "Доп. CV", file_name: "extra.docx", can_delete: true })],
      },
      "/resumes",
    );
    const row = (await screen.findByText("tester_cv.docx")).closest("tr")!;
    expect(within(row).getByText("extra.docx")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Показать текст" })).toBeInTheDocument();
    expect(within(row).getByText("Не подключать к клиенту X")).toBeInTheDocument();
    expect(within(row).getByText("test.user, 05.10.2026, 14:00")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Запрос на естественном языке")).toBeDisabled();
    expect(screen.getByPlaceholderText("Выбор технологий")).toBeDisabled();
  });

  it("«Удалить» только у файлов с can_delete: актуальное основное CV не удаляется", async () => {
    setup(
      {
        files: [
          file(),
          file({
            id: 11,
            kind: "materials",
            kind_title: "Материалы",
            file_name: "works.pdf",
            extraction_status: "none",
            can_delete: true,
          }),
        ],
      },
      "/resumes",
    );
    const row = (await screen.findByText("tester_cv.docx")).closest("tr")!;
    expect(within(row).getAllByRole("button", { name: "Удалить" })).toHaveLength(1);
    const li = within(row).getByText("works.pdf").closest("li")!;
    expect(within(li).getByRole("button", { name: "Удалить" })).toBeInTheDocument();
  });

  it("удаление: подтверждение, DELETE с CSRF, список перечитывается", async () => {
    let deleted = false;
    setup(
      {
        files: [file(), file({ id: 11, kind: "extra_cv", kind_title: "Доп. CV", file_name: "extra.docx", can_delete: true })],
        extra: (u, init) => {
          if (u.endsWith("/cv/files/11") && init?.method === "DELETE") {
            deleted = true;
            return new Response(null, { status: 204 });
          }
          return undefined;
        },
      },
      "/resumes",
    );
    await screen.findByText("extra.docx");
    await userEvent.click(screen.getByRole("button", { name: "Удалить" }));
    const dlg = await screen.findByRole("dialog");
    await userEvent.click(within(dlg).getByRole("button", { name: "Удалить" }));
    await waitFor(() => expect(deleted).toBe(true));
    const del = calls.find((c) => c.init?.method === "DELETE")!;
    expect((del.init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("FAKE_csrf");
    await waitFor(() =>
      expect(calls.filter((c) => c.url.endsWith("/cv/files") && c.init?.method === "GET").length).toBeGreaterThan(1),
    );
  });

  it("архив: восстановление по can_restore", async () => {
    let restored = "";
    setup(
      {
        files: [file()],
        archive: [
          file({
            id: 9,
            is_archived: true,
            archived_at: "2026-09-20T10:00:00Z",
            file_name: "old_cv.docx",
            can_restore: true,
            can_delete: true,
          }),
        ],
        extra: (u, init) => {
          if (u.endsWith("/restore") && init?.method === "POST") {
            restored = u;
            return json({ employee_key: "EMP-1", files: [], archive: [] });
          }
          return undefined;
        },
      },
      "/resumes",
    );
    await userEvent.click(await screen.findByRole("button", { name: "Архив основного CV" }));
    expect(await screen.findByText("old_cv.docx")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Восстановить" }));
    await waitFor(() => expect(restored).toContain("/cv/files/9/restore"));
  });

  it("загрузка: без типа кнопка недоступна; недопустимый формат не уходит на сервер", async () => {
    const user = userEvent.setup({ applyAccept: false });
    setup({ files: [file()] }, "/resumes");
    await user.click(await screen.findByRole("button", { name: "Добавить" }));
    const dlg = await screen.findByRole("dialog");
    const submit = within(dlg).getByRole("button", { name: "Загрузить" });
    expect(submit).toBeDisabled();

    await user.upload(within(dlg).getByLabelText("Файл"), new File(["x"], "cv.pdf"));
    expect(submit).toBeDisabled(); // тип не выбран
    await user.selectOptions(within(dlg).getByLabelText("Тип файла"), "main_cv");
    expect(within(dlg).getByText(/Допустимые форматы: \.doc, \.docx/)).toBeInTheDocument();
    expect(within(dlg).getByText(/Текущее основное CV будет заменено/)).toBeInTheDocument();
    await user.upload(within(dlg).getByLabelText("Файл"), new File(["x"], "cv.pdf"));
    await user.click(submit);
    expect(await within(dlg).findByRole("alert")).toHaveTextContent(/\.pdf не поддерживается/);
    expect(calls.some((c) => c.init?.method === "POST")).toBe(false);
  });

  it("загрузка: тело = файл, тип и имя в query, список перечитывается; один файл (без multiple)", async () => {
    const user = userEvent.setup({ applyAccept: false });
    let posted: { url: string; init?: RequestInit } | null = null;
    setup(
      {
        files: [],
        extra: (u, init) => {
          if (init?.method === "POST" && u.includes("/files?kind=")) {
            posted = { url: u, init };
            return json(
              { file: file({ id: 20, kind: "extra_cv", kind_title: "Доп. CV", file_name: "extra.docx" }), replaced_file_id: null },
              201,
            );
          }
          return undefined;
        },
      },
      "/resumes",
    );
    await user.click(await screen.findByRole("button", { name: "Добавить" }));
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getByLabelText("Файл")).not.toHaveAttribute("multiple");
    await user.selectOptions(within(dlg).getByLabelText("Тип файла"), "extra_cv");
    const f = new File(["abc"], "extra.docx");
    await user.upload(within(dlg).getByLabelText("Файл"), f);
    await user.click(within(dlg).getByRole("button", { name: "Загрузить" }));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted!.url).toContain("/api/cv/employees/EMP-1/files?kind=extra_cv&file_name=extra.docx");
    expect(posted!.init?.body).toBe(f);
    expect((posted!.init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("FAKE_csrf");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText(/загружен/)).toBeInTheDocument();
  });

  it("отказ сервера при загрузке (415) показывается в окне, окно остаётся", async () => {
    const user = userEvent.setup({ applyAccept: false });
    setup(
      {
        extra: (u, init) =>
          init?.method === "POST" && u.includes("/files?kind=")
            ? json({ detail: "Содержимое файла не соответствует его расширению" }, 415)
            : undefined,
      },
      "/resumes",
    );
    await user.click(await screen.findByRole("button", { name: "Добавить" }));
    const dlg = await screen.findByRole("dialog");
    await user.selectOptions(within(dlg).getByLabelText("Тип файла"), "materials");
    await user.upload(within(dlg).getByLabelText("Файл"), new File(["abc"], "pic.png"));
    await user.click(within(dlg).getByRole("button", { name: "Загрузить" }));
    expect(await within(dlg).findByRole("alert")).toHaveTextContent("Содержимое файла не соответствует");
  });

  it("фильтры: «Применить» по статусу и поиску, «Сбросить»", async () => {
    setup({ employees: [emp(), emp({ jira_key: "EMP-2", full_name: "Иванов Иван", status: "Декрет" })] }, "/resumes");
    await screen.findByText("Тестов Тест");
    await userEvent.selectOptions(screen.getByLabelText("Статус сотрудника"), "Декрет");
    expect(screen.getByText("Тестов Тест")).toBeInTheDocument(); // до «Применить» не меняется
    await userEvent.click(screen.getByRole("button", { name: "Применить" }));
    expect(screen.queryByText("Тестов Тест")).not.toBeInTheDocument();
    expect(screen.getByText("Иванов Иван")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Сбросить" }));
    expect(screen.getByText("Тестов Тест")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("ФИО, ключ или имя файла"), "иванов{enter}");
    expect(screen.queryByText("Тестов Тест")).not.toBeInTheDocument();
  });

  it("статус «Уволен» убран из списка статусов; тумблер показывает уволенных вместе с поиском и статусом, «Сбросить» выключает", async () => {
    setup(
      {
        employees: [
          emp(),
          emp({ jira_key: "EMP-2", full_name: "Иванов Иван", status: "Декрет" }),
          emp({ jira_key: "EMP-3", full_name: "Иванов Пётр", status: "Уволен" }),
        ],
      },
      "/resumes",
    );
    await screen.findByText("Тестов Тест");
    const select = screen.getByLabelText("Статус сотрудника");
    expect(within(select).queryByRole("option", { name: "Уволен" })).not.toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "Декрет" })).toBeInTheDocument();
    // по умолчанию уволенные скрыты
    expect(screen.queryByText("Иванов Пётр")).not.toBeInTheDocument();
    const toggle = screen.getByRole("switch", { name: "Показывать уволенных" });
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);
    expect(screen.queryByText("Иванов Пётр")).not.toBeInTheDocument(); // до «Применить» не меняется
    await userEvent.click(screen.getByRole("button", { name: "Применить" }));
    expect(screen.getByText("Иванов Пётр")).toBeInTheDocument();
    expect(screen.getByText("Тестов Тест")).toBeInTheDocument();
    // вместе с поиском
    await userEvent.type(screen.getByPlaceholderText("ФИО, ключ или имя файла"), "иванов");
    await userEvent.click(screen.getByRole("button", { name: "Применить" }));
    expect(screen.getByText("Иванов Пётр")).toBeInTheDocument();
    expect(screen.getByText("Иванов Иван")).toBeInTheDocument();
    expect(screen.queryByText("Тестов Тест")).not.toBeInTheDocument();
    // вместе со статусом
    await userEvent.selectOptions(select, "Декрет");
    await userEvent.click(screen.getByRole("button", { name: "Применить" }));
    expect(screen.queryByText("Иванов Пётр")).not.toBeInTheDocument();
    expect(screen.getByText("Иванов Иван")).toBeInTheDocument();
    // сброс выключает тумблер
    await userEvent.click(screen.getByRole("button", { name: "Сбросить" }));
    expect(screen.getByRole("switch", { name: "Показывать уволенных" })).not.toBeChecked();
    expect(screen.queryByText("Иванов Пётр")).not.toBeInTheDocument();
    expect(screen.getByText("Тестов Тест")).toBeInTheDocument();
  });

  it("переход из «Сотрудников»: ?q= подставляется в поиск", async () => {
    setup({ employees: [emp(), emp({ jira_key: "EMP-2", full_name: "Иванов Иван" })] }, "/resumes?q=EMP-2");
    expect(await screen.findByText("Иванов Иван")).toBeInTheDocument();
    expect(screen.queryByText("Тестов Тест")).not.toBeInTheDocument();
  });
});
