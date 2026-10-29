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
  deptHeads?: { login: string; display_name: string }[] | "fail";
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
    if (url.endsWith("/sync/employees/dept-heads")) {
      return w.deptHeads === "fail" ? json({ detail: "x" }, 500) : json(w.deptHeads ?? []);
    }
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
    expect(within(screen.getByRole("navigation", { name: /^Статусы сотрудников/ })).getAllByRole("button")).toHaveLength(10);

    await userEvent.click(screen.getByRole("button", { name: /Декрет/ }));
    expect(screen.queryByRole("listitem", { name: "Тестов Тест" })).not.toBeInTheDocument();
    expect(screen.getByText("не назначается")).toBeInTheDocument();
  });

  it("тумблер «Уволенные»: скрыты по умолчанию, работает вместе со статусом и поиском", async () => {
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
    const nav = screen.getByRole("navigation", { name: /^Статусы сотрудников/ });
    expect(within(nav).queryByRole("button", { name: /Уволен/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: "Иванов Пётр" })).not.toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: /Все/ })).toHaveTextContent("2");

    await userEvent.click(screen.getByRole("switch", { name: "Уволенные" }));
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
    await userEvent.click(await screen.findByRole("button", { name: /^Профиль: / }));
    const dlg = await screen.findByRole("dialog");
    expect(await within(dlg).findByText("tester_cv.docx")).toBeInTheDocument();
    expect(await within(dlg).findByText(/AN-5/)).toBeInTheDocument();
    expect(within(dlg).queryByText(/AN-6/)).not.toBeInTheDocument();
    expect(within(dlg).getByText("Работал с ERP")).toBeInTheDocument();
  });

  it("карточка без ключа (в т.ч. без jira_url); заголовки «Профиля» и CV - ФИО; поиск по ключу работает", async () => {
    setup(
      {
        employees: [emp({ jira_url: null }), emp({ jira_key: "EMP-2", full_name: "Другой Сотрудник" })],
        extra: (u) =>
          u.endsWith("/main-cv") ? json({ file: file(), extraction_status: "ok", text: "Текст резюме" }) : undefined,
      },
      "/employees",
    );
    const card = await screen.findByRole("listitem", { name: "Тестов Тест" });
    expect(within(card).queryByText("EMP-1")).not.toBeInTheDocument();
    expect(within(card).queryByRole("link", { name: "Тестов Тест" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("listitem", { name: "Другой Сотрудник" })).queryByText("EMP-2")).not.toBeInTheDocument();

    await userEvent.click(within(card).getByRole("button", { name: /^Профиль: / }));
    const prof = await screen.findByRole("dialog");
    expect(within(prof).getByRole("heading", { name: "Тестов Тест" })).toBeInTheDocument();
    await userEvent.click(within(prof).getByRole("button", { name: "Закрыть" }));

    await userEvent.click(within(card).getByRole("button", { name: /^Резюме: / }));
    const cv = await screen.findByRole("dialog");
    expect(within(cv).getByRole("heading", { name: /^Тестов Тест — / })).toBeInTheDocument();
  });

  it("профиль (M2): статус в шапке, нет строки «Статус», футер: Резюме/Файлы и «Закрыть», Esc и × закрывают", async () => {
    setup({}, "/employees");
    await userEvent.click(await screen.findByRole("button", { name: /^Профиль: / }));
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).queryByText("Статус", { selector: "dt" })).not.toBeInTheDocument();
    expect(within(dlg).getByText(/доступен для назначения|на потребность не назначается/)).toBeInTheDocument();
    expect(within(dlg).getByRole("button", { name: "Резюме" })).toHaveClass("act");
    expect(within(dlg).getByRole("link", { name: "Файлы" })).toHaveClass("act");
    await userEvent.click(within(dlg).getByRole("button", { name: "Закрыть" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /^Профиль: / }));
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /^Профиль: / }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Закрыть окно" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
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
    await userEvent.click(await screen.findByRole("button", { name: /^Резюме: / }));
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
    await userEvent.click(await screen.findByRole("button", { name: /^Резюме: / }));
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
  it("таблица: файлы, содержание, заметка без автора, поле поиска по смыслу доступно", async () => {
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
    expect(within(row).getAllByRole("button", { name: "Просмотр" }).length).toBeGreaterThan(0);
    // 4 столбца, без «Содержание»; ключ в «Сотрудник» не показывается
    const heads = within(row.closest("table")!).getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual(["Сотрудник", "Файлы", "Описание", "Заметка"]);
    expect(row.closest("table")!.querySelectorAll("col")).toHaveLength(4);
    expect(within(row).queryByText("EMP-1")).not.toBeInTheDocument();
    // «Файлы»: только дата-время, без размера и автора
    expect(row.querySelector(".file__meta")!.textContent).toMatch(/^\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}$/);
    // поиск по смыслу: однострочное поле, 500 знаков, поле и «Найти» в одном ряду
    const sem = screen.getByLabelText("Поиск по смыслу (LLM)") as HTMLInputElement;
    expect(sem.tagName).toBe("INPUT");
    expect(sem.type).toBe("text");
    expect(sem.maxLength).toBe(500);
    expect(sem.parentElement).toBe(screen.getByRole("button", { name: "Найти по смыслу" }).parentElement);
    expect(sem.parentElement!.className).toContain("sa__row");
    expect(within(row).getByText("Не подключать к клиенту X")).toBeInTheDocument();
    expect(within(row).queryByText(/test.user/)).not.toBeInTheDocument(); // автор и дата только в окне (KON-115)
    expect(within(row).getByRole("button", { name: "Изменить заметку: Тестов Тест" })).toBeEnabled();
    expect(screen.getByLabelText("Поиск по смыслу (LLM)")).toBeEnabled();
    expect(screen.getByLabelText("Технологии")).toBeEnabled();
    // значок «i» с описанием логики подбора рядом с поиском по смыслу
    await userEvent.click(screen.getByRole("button", { name: "Как подбираются кандидаты" }));
    expect(screen.getByRole("dialog", { name: "Как подбираются кандидаты" })).toBeInTheDocument();
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
    await user.click(await screen.findByRole("button", { name: /^Добавить файл: / }));
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
    await user.click(await screen.findByRole("button", { name: /^Добавить файл: / }));
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
    await user.click(await screen.findByRole("button", { name: /^Добавить файл: / }));
    const dlg = await screen.findByRole("dialog");
    await user.selectOptions(within(dlg).getByLabelText("Тип файла"), "materials");
    await user.upload(within(dlg).getByLabelText("Файл"), new File(["abc"], "pic.png"));
    await user.click(within(dlg).getByRole("button", { name: "Загрузить" }));
    expect(await within(dlg).findByRole("alert")).toHaveTextContent("Содержимое файла не соответствует");
  });

  const RHEADS = [
    { login: "orlov", display_name: "Павел Орлов" },
    { login: "anna", display_name: "Анна Яблокова" },
  ];
  const RWORLD = [
    emp({ jira_key: "E-1", full_name: "Алексеев А", status: "Свободен", dept_head_login: "orlov" }),
    emp({ jira_key: "E-2", full_name: "Борисов Б", status: "Декрет", dept_head_login: "orlov" }),
    emp({ jira_key: "E-3", full_name: "Васильев В", status: "Свободен", dept_head_login: "anna" }),
    emp({ jira_key: "E-4", full_name: "Григорьев Г", status: "Внутренний проект", dept_head_login: null }),
    emp({ jira_key: "E-5", full_name: "Дмитриев Д", status: "Уволен", dept_head_login: null }),
  ];
  const rchip = (name: RegExp) => within(screen.getByRole("navigation", { name: /Статусы сотрудников/ })).getByRole("button", { name });
  const shown = (name: string) => screen.queryByText(name);

  it("«Резюме»: чипы статусов над блоком фильтров, без списка статусов и «Применить»; мультивыбор мгновенный (ИЛИ)", async () => {
    setup({ employees: RWORLD, deptHeads: RHEADS }, "/resumes");
    await screen.findByText("Алексеев А");
    expect(screen.queryByLabelText("Статус сотрудника")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Применить" })).not.toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Статусы сотрудников (можно выбрать несколько)" });
    expect(nav.compareDocumentPosition(screen.getByRole("search")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(rchip(/^Все 4/)).toHaveAttribute("aria-pressed", "true"); // уволенный не входит
    await userEvent.click(rchip(/^Декрет/));
    expect(shown("Алексеев А")).not.toBeInTheDocument();
    expect(shown("Борисов Б")).toBeInTheDocument();
    await userEvent.click(rchip(/^Внутренний проект/));
    expect(shown("Борисов Б")).toBeInTheDocument();
    expect(shown("Григорьев Г")).toBeInTheDocument();
    expect(shown("Васильев В")).not.toBeInTheDocument();
    await userEvent.click(rchip(/^Все/));
    expect(shown("Васильев В")).toBeInTheDocument();
  });

  it("«Резюме»: РО - «Все», по алфавиту, «Без РО»; счётчики чипов учитывают РО; по И с чипами", async () => {
    setup({ employees: RWORLD, deptHeads: RHEADS }, "/resumes");
    await screen.findByText("Алексеев А");
    const ro = screen.getByLabelText("РО");
    expect(within(ro).getAllByRole("option").map((o) => o.textContent)).toEqual(["Все", "Анна Яблокова", "Павел Орлов", "Без РО"]);
    await userEvent.selectOptions(ro, "orlov");
    expect(shown("Васильев В")).not.toBeInTheDocument();
    expect(shown("Алексеев А")).toBeInTheDocument();
    expect(rchip(/^Все 2/)).toBeInTheDocument();
    await userEvent.click(rchip(/^Декрет/));
    expect(shown("Алексеев А")).not.toBeInTheDocument();
    expect(shown("Борисов Б")).toBeInTheDocument();
    await userEvent.selectOptions(ro, "__none__");
    expect(screen.getByText("По выбранным фильтрам резюме нет.")).toBeInTheDocument();
  });

  it("«Резюме»: поиск применяется с задержкой, «Уволенные» - сразу", async () => {
    setup({ employees: RWORLD, deptHeads: RHEADS }, "/resumes");
    await screen.findByText("Алексеев А");
    await userEvent.type(screen.getByPlaceholderText("ФИО, ключ или имя файла"), "борис");
    expect(shown("Алексеев А")).toBeInTheDocument(); // до задержки список прежний
    expect(await screen.findByText("Борисов Б")).toBeInTheDocument();
    await waitFor(() => expect(shown("Алексеев А")).not.toBeInTheDocument());
    await userEvent.clear(screen.getByPlaceholderText("ФИО, ключ или имя файла"));
    await userEvent.click(screen.getByRole("switch", { name: "Уволенные" }));
    expect(await screen.findByText("Дмитриев Д")).toBeInTheDocument();
    expect(rchip(/^Все 5/)).toBeInTheDocument();
  });

  it("«Резюме»: «Сбросить» неактивна без фильтров блока, чипы её не включают; сбрасывает всё кроме чипов, фокус в «Поиск»", async () => {
    setup({ employees: RWORLD, deptHeads: RHEADS }, "/resumes");
    await screen.findByText("Алексеев А");
    const reset = screen.getByRole("button", { name: "Сбросить" });
    expect(reset).toBeDisabled();
    await userEvent.click(rchip(/^Декрет/));
    expect(reset).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("РО"), "orlov");
    expect(reset).toBeEnabled();
    await userEvent.click(screen.getByRole("switch", { name: "Уволенные" }));
    await userEvent.type(screen.getByPlaceholderText("ФИО, ключ или имя файла"), "б");
    await userEvent.click(reset);
    expect(screen.getByLabelText("РО")).toHaveValue("");
    expect(screen.getByRole("switch", { name: "Уволенные" })).not.toBeChecked();
    expect(screen.getByPlaceholderText("ФИО, ключ или имя файла")).toHaveValue("");
    expect(screen.getByPlaceholderText("ФИО, ключ или имя файла")).toHaveFocus();
    expect(reset).toBeDisabled();
    expect(rchip(/^Декрет/)).toHaveAttribute("aria-pressed", "true"); // чипы остались
    expect(shown("Борисов Б")).toBeInTheDocument();
    expect(shown("Васильев В")).not.toBeInTheDocument();
  });

  it("«Резюме»: пустой результат - текст и «Все статусы»; ошибка списка РО не блокирует остальное", async () => {
    setup({ employees: RWORLD, deptHeads: "fail" }, "/resumes");
    await screen.findByText("Алексеев А");
    expect(screen.getByLabelText("РО")).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("Не удалось загрузить список РО. Остальные фильтры работают.");
    await userEvent.click(rchip(/^Декрет/));
    await userEvent.type(screen.getByPlaceholderText("ФИО, ключ или имя файла"), "васильев");
    expect(await screen.findByText("По выбранным фильтрам резюме нет.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Все статусы" }));
    expect(await screen.findByText("Васильев В")).toBeInTheDocument();
    expect(rchip(/^Все/)).toHaveAttribute("aria-pressed", "true");
  });

  it("«Резюме»: поиск по смыслу получает область из всех фильтров только при «Найти»", async () => {
    setup(
      {
        employees: RWORLD,
        deptHeads: RHEADS,
        extra: (u) => (u.includes("/resume/semantic-search") ? json({ status: "done", items: [], total: 0 }) : undefined),
      },
      "/resumes",
    );
    await screen.findByText("Алексеев А");
    await userEvent.click(rchip(/^Свободен/));
    await userEvent.click(rchip(/^Декрет/));
    await userEvent.selectOptions(screen.getByLabelText("РО"), "orlov");
    expect(document.getElementById("semantic-q-hint")).toHaveTextContent("статусы: Свободен, Декрет · РО: Павел Орлов");
    expect(calls.filter((c) => c.url.includes("/resume/semantic-search") && c.init?.method === "POST")).toHaveLength(0);
    await userEvent.type(screen.getByLabelText("Поиск по смыслу (LLM)"), "аналитик 1С");
    await userEvent.click(screen.getByRole("button", { name: "Найти по смыслу" }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === "POST" && c.url.includes("/resume/semantic-search"))).toBe(true));
    const post = calls.find((c) => c.init?.method === "POST" && c.url.includes("/resume/semantic-search"))!;
    const body = JSON.parse(String(post.init!.body));
    expect(body).toMatchObject({ statuses: ["Свободен", "Декрет"], dept_head: "orlov", include_dismissed: false, technology: [] });
  });

  it("переход из «Сотрудников»: ?q= подставляется в поиск", async () => {
    setup({ employees: [emp(), emp({ jira_key: "EMP-2", full_name: "Иванов Иван" })] }, "/resumes?q=EMP-2");
    expect(await screen.findByText("Иванов Иван")).toBeInTheDocument();
    expect(screen.queryByText("Тестов Тест")).not.toBeInTheDocument();
  });
});

describe("Сотрудники: фильтры статусов и РО (CR-012)", () => {
  const HEADS = [
    { login: "orlov", display_name: "Павел Орлов" },
    { login: "anna", display_name: "Анна Яблокова" },
    { login: "boris", display_name: "Борис Белов" },
  ];
  const WORLD = [
    emp({ jira_key: "E-1", full_name: "Алексеев А", status: "Свободен", dept_head_login: "orlov" }),
    emp({ jira_key: "E-2", full_name: "Борисов Б", status: "Декрет", dept_head_login: "orlov" }),
    emp({ jira_key: "E-3", full_name: "Васильев В", status: "Свободен", dept_head_login: "anna" }),
    emp({ jira_key: "E-4", full_name: "Григорьев Г", status: "Внутренний проект", dept_head_login: null }),
    emp({ jira_key: "E-5", full_name: "Дмитриев Д", status: "Уволен", dept_head_login: null }),
  ];
  const card = (name: string) => screen.queryByRole("listitem", { name });
  const chip = (name: RegExp) => screen.getByRole("button", { name });

  async function open(over: Partial<World> = {}) {
    setup({ employees: WORLD, deptHeads: HEADS, ...over }, "/employees");
    await screen.findByRole("listitem", { name: "Алексеев А" });
  }

  it("мультивыбор статусов (ИЛИ), снятие одного и всех; «Все» нажат при нуле выбранных", async () => {
    await open();
    expect(chip(/^Все/)).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(chip(/^Свободен/));
    await userEvent.click(chip(/^Декрет/));
    expect(chip(/^Все/)).toHaveAttribute("aria-pressed", "false");
    expect(chip(/^Свободен/)).toHaveAttribute("aria-pressed", "true");
    expect(card("Алексеев А")).toBeInTheDocument();
    expect(card("Борисов Б")).toBeInTheDocument();
    expect(card("Васильев В")).toBeInTheDocument();
    expect(card("Григорьев Г")).not.toBeInTheDocument();
    expect(screen.getByText(/Показано/)).toHaveTextContent("Показано 3 из 4");

    await userEvent.click(chip(/^Свободен/)); // снять один
    expect(card("Алексеев А")).not.toBeInTheDocument();
    expect(card("Борисов Б")).toBeInTheDocument();

    await userEvent.click(chip(/^Декрет/)); // снят последний - «Все»
    expect(chip(/^Все/)).toHaveAttribute("aria-pressed", "true");
    expect(card("Григорьев Г")).toBeInTheDocument();

    await userEvent.click(chip(/^Свободен/));
    await userEvent.click(chip(/^Декрет/));
    await userEvent.click(chip(/^Все/)); // «Все» снимает все выбранные
    expect(chip(/^Декрет/)).toHaveAttribute("aria-pressed", "false");
    expect(card("Григорьев Г")).toBeInTheDocument();
  });

  it("«РО»: по умолчанию «Все», имена по алфавиту, «Без РО» последним; счётчики учитывают РО", async () => {
    await open();
    const select = screen.getByLabelText("РО") as HTMLSelectElement;
    expect(select.value).toBe("");
    expect([...select.options].map((o) => o.text)).toEqual(["Все", "Анна Яблокова", "Борис Белов", "Павел Орлов", "Без РО"]);

    await userEvent.selectOptions(select, "orlov");
    expect(card("Алексеев А")).toBeInTheDocument();
    expect(card("Борисов Б")).toBeInTheDocument();
    expect(card("Васильев В")).not.toBeInTheDocument();
    expect(chip(/^Все/)).toHaveTextContent("2");
    expect(chip(/^Внутренний проект/)).toHaveTextContent("0");

    await userEvent.selectOptions(select, "");
    expect(card("Васильев В")).toBeInTheDocument();
  });

  it("«Без РО» и И-комбинации: статус, поиск, «Уволенные»; сброс возвращает всё", async () => {
    await open();
    await userEvent.selectOptions(screen.getByLabelText("РО"), "__none__");
    expect(card("Григорьев Г")).toBeInTheDocument();
    expect(card("Алексеев А")).not.toBeInTheDocument();
    expect(card("Дмитриев Д")).not.toBeInTheDocument(); // уволенный скрыт

    await userEvent.click(screen.getByRole("switch", { name: "Уволенные" }));
    expect(card("Дмитриев Д")).toBeInTheDocument();
    await userEvent.click(chip(/^Внутренний проект/));
    expect(card("Дмитриев Д")).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Поиск по ФИО и ключу"), "нет такого");
    expect(screen.getByText("По выбранным фильтрам сотрудников нет.")).toBeInTheDocument();
    const sum = screen.getByText(/Показано/).closest("p")!;
    expect(sum).toHaveTextContent("статус: Внутренний проект · РО: Без РО · поиск: «нет такого» · включая уволенных");

    await userEvent.click(within(sum).getByRole("button", { name: "Сбросить фильтры" }));
    expect((screen.getByLabelText("РО") as HTMLSelectElement).value).toBe("");
    expect(screen.getByRole("switch", { name: "Уволенные" })).not.toBeChecked();
    expect(screen.getByLabelText("Поиск по ФИО и ключу")).toHaveValue("");
    expect(chip(/^Все/)).toHaveAttribute("aria-pressed", "true");
    expect(card("Васильев В")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сбросить фильтры" })).not.toBeInTheDocument();
    expect(screen.getByText(/Показано/)).toHaveTextContent("Показано 4 из 4");
  });

  it("РО загружаются: поле отключено, «Загружаем список РО…»", async () => {
    let release: (r: Response) => void = () => {};
    const gate = new Promise<Response>((r) => (release = r));
    setup(
      { employees: WORLD, extra: (u) => (u.endsWith("/dept-heads") ? (gate as unknown as Response) : undefined) },
      "/employees",
    );
    await screen.findByRole("listitem", { name: "Алексеев А" });
    expect(screen.getByLabelText("РО")).toBeDisabled();
    expect(screen.getByLabelText("РО")).toHaveDisplayValue("Загрузка…");
    expect(screen.getByText(/Загружаем список РО/)).toBeInTheDocument();
    release(json(HEADS));
    await waitFor(() => expect(screen.getByLabelText("РО")).toBeEnabled());
  });

  it("ошибка списка РО: поле отключено, остальные фильтры работают, «Повторить» перезагружает", async () => {
    await open({ deptHeads: "fail" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить список РО. Остальные фильтры работают.");
    expect(screen.getByLabelText("РО")).toBeDisabled();
    expect(screen.getByLabelText("РО")).toHaveDisplayValue("Список недоступен");
    await userEvent.click(chip(/^Декрет/));
    expect(card("Борисов Б")).toBeInTheDocument();
    expect(card("Алексеев А")).not.toBeInTheDocument();
  });

  it("нет РО ни у кого: поле отключено, «Нет данных» и подсказка", async () => {
    await open({ deptHeads: [] });
    await waitFor(() => expect(screen.getByLabelText("РО")).toHaveDisplayValue("Нет данных"));
    expect(screen.getByLabelText("РО")).toBeDisabled();
    expect(screen.getByText(/фильтр недоступен/)).toBeInTheDocument();
  });

  it("пусто без фильтров - «Нет данных»", async () => {
    setup({ employees: [], deptHeads: HEADS }, "/employees");
    expect(await screen.findByText("Нет данных", { selector: "p" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сбросить фильтры" })).not.toBeInTheDocument();
  });
});
