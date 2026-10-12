import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { AdminUser, JiraStatus, MeResponse } from "../api/types";

const FAKE_PASSWORD = "FAKE_jira_pw_for_tests";

function me(over: Partial<MeResponse> = {}): MeResponse {
  return {
    login: "boss",
    role: "admin",
    jira_status: { state: "ok" },
    sections: [],
    csrf_token: "FAKE_csrf",
    ...over,
  };
}

function user(login: string, over: Partial<AdminUser> = {}): AdminUser {
  return {
    login,
    role: "user",
    active: true,
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-02T10:00:00Z",
    ...over,
  };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const fetchMock = vi.fn();

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

type Handler = (url: string, init: RequestInit) => Response | undefined;

// me и список допущенных по умолчанию; handler перехватывает остальное.
function serve(opts: { me?: MeResponse; users?: AdminUser[]; handler?: Handler }) {
  fetchMock.mockImplementation((url: string, init: RequestInit = {}) => {
    const custom = opts.handler?.(url, init);
    if (custom) return Promise.resolve(custom);
    if (url === "/api/auth/me") return Promise.resolve(json(opts.me ?? me()));
    if (url === "/api/admin/users" && (init.method ?? "GET") === "GET")
      return Promise.resolve(json(opts.users ?? [user("boss", { role: "admin" }), user("ivan")]));
    return Promise.resolve(json({}, 404));
  });
}

function calls(url: string, method: string) {
  return fetchMock.mock.calls.filter((c) => c[0] === url && (c[1]?.method ?? "GET") === method);
}

beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("Настройки: допущенные", () => {
  it("показывает таблицу: логин, роль, статус, даты; заглушки остальных вкладок", async () => {
    serve({ users: [user("boss", { role: "admin" }), user("ivan", { active: false })] });
    renderAt("/settings");
    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(within(rows[1]).getByText("boss")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Администратор")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Отозван")).toBeInTheDocument();
    expect(within(rows[2]).getByRole("button", { name: "Вернуть доступ" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "НДС" }));
    expect(screen.getByText(/в Dev-10 и далее/)).toBeInTheDocument();
  });

  it("добавление: логин и роль, список обновляется без перезагрузки", async () => {
    let users = [user("boss", { role: "admin" })];
    serve({
      handler: (url, init) => {
        if (url === "/api/admin/users" && init.method === "POST") {
          users = [...users, user("petr")];
          return json(user("petr"), 201);
        }
        if (url === "/api/admin/users/petr" && init.method === "PATCH") {
          users = users.map((u) => (u.login === "petr" ? { ...u, role: "admin" } : u));
          return json(users[1]);
        }
        if (url === "/api/admin/users" && (init.method ?? "GET") === "GET") return json(users);
      },
    });
    renderAt("/settings");
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Добавить" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Логин"), "petr");
    await userEvent.selectOptions(within(dialog).getByLabelText("Роль"), "admin");
    await userEvent.click(within(dialog).getByRole("button", { name: "Добавить" }));

    expect(await screen.findByText("petr")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const post = calls("/api/admin/users", "POST")[0];
    expect(JSON.parse(post[1].body)).toEqual({ login: "petr" });
    expect(post[1].headers["X-CSRF-Token"]).toBe("FAKE_csrf");
    expect(JSON.parse(calls("/api/admin/users/petr", "PATCH")[0][1].body)).toEqual({ role: "admin" });
  });

  it("отзыв с подтверждением; отмена ничего не шлёт", async () => {
    let users = [user("boss", { role: "admin" }), user("ivan")];
    serve({
      handler: (url, init) => {
        if (url === "/api/admin/users/ivan" && init.method === "PATCH") {
          users = users.map((u) => (u.login === "ivan" ? { ...u, active: false } : u));
          return json(users[1]);
        }
        if (url === "/api/admin/users" && (init.method ?? "GET") === "GET") return json(users);
      },
    });
    renderAt("/settings");
    await screen.findByRole("table");
    const row = () => screen.getByText("ivan").closest("tr")!;

    await userEvent.click(within(row()).getByRole("button", { name: "Отозвать" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Отмена" }));
    expect(calls("/api/admin/users/ivan", "PATCH")).toHaveLength(0);

    await userEvent.click(within(row()).getByRole("button", { name: "Отозвать" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Подтвердить" }));
    await waitFor(() => expect(within(row()).getByText("Отозван")).toBeInTheDocument());
    expect(JSON.parse(calls("/api/admin/users/ivan", "PATCH")[0][1].body)).toEqual({ active: false });
    expect(within(row()).getByRole("button", { name: "Вернуть доступ" })).toBeInTheDocument();
  });

  it("ошибка сервера (последний админ) показывается как есть", async () => {
    const MSG = "Нельзя отозвать или понизить последнего активного администратора";
    serve({
      users: [user("boss", { role: "admin" })],
      handler: (url, init) =>
        url === "/api/admin/users/boss" && init.method === "PATCH" ? json({ detail: MSG }, 409) : undefined,
    });
    renderAt("/settings");
    await userEvent.click(await screen.findByRole("button", { name: "Отозвать" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Подтвердить" }));
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent(MSG);
  });
});

describe("Баннер Jira", () => {
  const REFUSAL = "Jira: требуется подтверждение входа (CAPTCHA)";
  const blocked: JiraStatus = { state: "blocked", code: "login_blocked", message: REFUSAL };

  it("нет баннера при ok; при отказе - текст как есть, разделы открыты", async () => {
    serve({ me: me({ jira_status: { state: "ok" } }) });
    const first = renderAt("/needs");
    await screen.findByRole("link", { name: "Резюме" });
    expect(screen.queryByText(/Работа с Jira недоступна/)).not.toBeInTheDocument();
    first.unmount();

    serve({ me: me({ jira_status: blocked }) });
    renderAt("/employees");
    expect(await screen.findByText(REFUSAL, { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/Работа с Jira недоступна/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Сотрудники" })).toBeInTheDocument();
  });

  it("повторный ввод: пароль уходит в /auth/jira-relogin и нигде не остаётся", async () => {
    serve({
      me: me({ jira_status: blocked }),
      handler: (url, init) =>
        url === "/api/auth/jira-relogin" && init.method === "POST" ? json(me({ jira_status: { state: "ok" } })) : undefined,
    });
    renderAt("/employees");
    const input = await screen.findByLabelText("Пароль Jira");
    await userEvent.type(input, FAKE_PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Ввести пароль повторно" }));
    await waitFor(() => expect(screen.queryByText(/Работа с Jira недоступна/)).not.toBeInTheDocument());

    const call = calls("/api/auth/jira-relogin", "POST")[0];
    expect(JSON.parse(call[1].body)).toEqual({ password: FAKE_PASSWORD });
    expect(call[1].headers["X-CSRF-Token"]).toBe("FAKE_csrf");
    expect(call[0]).not.toContain(FAKE_PASSWORD);
    expect(JSON.stringify(Object.entries(window.localStorage ?? {}))).not.toContain(FAKE_PASSWORD);
    expect(JSON.stringify(Object.entries(window.sessionStorage ?? {}))).not.toContain(FAKE_PASSWORD);
    expect(window.location.href).not.toContain(FAKE_PASSWORD);
    expect(document.body.innerHTML).not.toContain(FAKE_PASSWORD);
  });

  it("новый отказ обновляет текст баннера, поле пароля очищено", async () => {
    const NEW = "Неверный пароль Jira";
    serve({
      me: me({ jira_status: blocked }),
      handler: (url, init) =>
        url === "/api/auth/jira-relogin" && init.method === "POST"
          ? json(me({ jira_status: { state: "blocked", code: "auth_failed", message: NEW } }))
          : undefined,
    });
    renderAt("/needs");
    const input = await screen.findByLabelText("Пароль Jira");
    await userEvent.type(input, FAKE_PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Ввести пароль повторно" }));
    expect(await screen.findByText(NEW, { exact: false })).toBeInTheDocument();
    expect(screen.getByLabelText("Пароль Jira")).toHaveValue("");
  });
});

describe("401 на любой странице", () => {
  it("ведёт на вход, после входа возвращает на прежний маршрут", async () => {
    let authed = true;
    serve({
      handler: (url, init) => {
        if (url === "/api/auth/me") return authed ? json(me()) : json({ detail: "Требуется вход" }, 401);
        if (url === "/api/admin/users" && (init.method ?? "GET") === "GET")
          return authed ? json([user("boss", { role: "admin" })]) : json({ detail: "Требуется вход" }, 401);
        if (url === "/api/auth/login") {
          authed = true;
          return json(me());
        }
      },
    });
    renderAt("/settings");
    await screen.findByRole("table");

    // отозванный пользователь: следующий запрос получает 401
    authed = false;
    const { request } = await import("../api/client");
    await expect(request("GET", "/admin/users")).rejects.toThrow("Требуется вход");
    expect(await screen.findByRole("heading", { name: "Вход" })).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Логин"), "boss");
    await userEvent.type(screen.getByLabelText("Пароль"), "FAKE_pw");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByRole("tab", { name: "Доступ" })).toBeInTheDocument();
  });
});
