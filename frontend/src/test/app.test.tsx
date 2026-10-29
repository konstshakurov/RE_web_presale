import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { MeResponse } from "../api/types";

const FAKE_PASSWORD = "FAKE_pw_for_tests";

function me(role: string): MeResponse {
  return {
    login: "test.user",
    role,
    jira_status: { state: "not_checked" },
    sections: [],
    csrf_token: "FAKE_csrf",
  };
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const fetchMock = vi.fn();

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("защищённые маршруты", () => {
  it("без сессии ведут на вход, логотип в шапке", async () => {
    fetchMock.mockResolvedValue(json(401, { detail: "Требуется вход" }));
    renderAt("/employees");
    expect(await screen.findByRole("heading", { name: "Вход" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "regoose" })).toHaveLength(1);
    expect(document.querySelectorAll(".topbar__brand img")).toHaveLength(2);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByText(/Сессия истекла/)).not.toBeInTheDocument();
  });
});

describe("вход", () => {
  it("успешный вход открывает «Потребности», пароль не в хранилище", async () => {
    fetchMock.mockResolvedValueOnce(json(401, {})).mockResolvedValueOnce(json(200, me("user")));
    renderAt("/login");
    await userEvent.type(await screen.findByLabelText("Логин"), "test.user");
    await userEvent.type(screen.getByLabelText("Пароль"), FAKE_PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByRole("heading", { name: "Потребности" })).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/auth/login");
    expect(JSON.parse(init.body)).toEqual({ login: "test.user", password: FAKE_PASSWORD });
    expect(JSON.stringify(Object.entries(window.localStorage ?? {}))).not.toContain(FAKE_PASSWORD);
    expect(JSON.stringify(Object.entries(window.sessionStorage ?? {}))).not.toContain(FAKE_PASSWORD);
    expect(window.location.href).not.toContain(FAKE_PASSWORD);
  });

  it("форма распознаётся менеджером паролей; пароль не очищается до ответа сервера (CR-011)", async () => {
    let release!: (r: Response) => void;
    fetchMock
      .mockResolvedValueOnce(json(401, {}))
      .mockReturnValueOnce(new Promise<Response>((r) => (release = r)));
    renderAt("/login");
    const loginInput = await screen.findByLabelText("Логин");
    const pwInput = screen.getByLabelText("Пароль");
    expect(loginInput.closest("form")).toBe(pwInput.closest("form"));
    expect(loginInput).toHaveAttribute("autocomplete", "username");
    expect(pwInput).toHaveAttribute("autocomplete", "current-password");
    await userEvent.type(loginInput, "test.user");
    await userEvent.type(pwInput, FAKE_PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    await screen.findByRole("button", { name: "Входим…" });
    expect(pwInput).toHaveValue(FAKE_PASSWORD);
    release(json(401, { detail: "Неверный логин или пароль, либо нет доступа" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(pwInput).toHaveValue("");
  });

  it("отказ показывает текст ошибки и очищает пароль", async () => {
    fetchMock
      .mockResolvedValueOnce(json(401, {}))
      .mockResolvedValueOnce(json(401, { detail: "Неверный логин или пароль, либо нет доступа" }));
    renderAt("/login");
    await userEvent.type(await screen.findByLabelText("Логин"), "test.user");
    await userEvent.type(screen.getByLabelText("Пароль"), FAKE_PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Неверный логин или пароль");
    expect(screen.getByLabelText("Пароль")).toHaveValue("");
    expect(screen.getByLabelText("Пароль")).toHaveAttribute("autocomplete", "current-password");
  });
});

describe("передача пароля менеджеру паролей (CR-011)", () => {
  class FakePasswordCredential {
    constructor(public init: { id: string; password: string; name?: string }) {}
  }
  const store = vi.fn();

  async function submit() {
    renderAt("/login");
    await userEvent.type(await screen.findByLabelText("Логин"), "test.user");
    await userEvent.type(screen.getByLabelText("Пароль"), FAKE_PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
  }

  beforeEach(() => {
    store.mockReset().mockResolvedValue(undefined);
    vi.stubGlobal("PasswordCredential", FakePasswordCredential);
    Object.defineProperty(navigator, "credentials", { value: { store }, configurable: true });
  });
  afterEach(() => {
    Reflect.deleteProperty(navigator, "credentials");
  });

  it("успешный вход: store вызван один раз с логином и паролем", async () => {
    fetchMock.mockResolvedValueOnce(json(401, {})).mockResolvedValueOnce(json(200, me("user")));
    await submit();
    expect(await screen.findByRole("heading", { name: "Потребности" })).toBeInTheDocument();
    expect(store).toHaveBeenCalledTimes(1);
    const cred = store.mock.calls[0][0] as FakePasswordCredential;
    expect(cred.init).toEqual({ id: "test.user", password: FAKE_PASSWORD, name: "test.user" });
  });

  it.each([401, 503])("отказ %i: store не вызывается", async (status) => {
    fetchMock.mockResolvedValueOnce(json(401, {})).mockResolvedValueOnce(json(status, { detail: "Отказ" }));
    await submit();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(store).not.toHaveBeenCalled();
  });

  it("ошибка store не мешает входу", async () => {
    store.mockRejectedValue(new Error("denied"));
    fetchMock.mockResolvedValueOnce(json(401, {})).mockResolvedValueOnce(json(200, me("user")));
    await submit();
    expect(await screen.findByRole("heading", { name: "Потребности" })).toBeInTheDocument();
  });

  it("без API вход проходит без ошибок", async () => {
    vi.stubGlobal("PasswordCredential", undefined);
    Reflect.deleteProperty(navigator, "credentials");
    fetchMock.mockResolvedValueOnce(json(401, {})).mockResolvedValueOnce(json(200, me("user")));
    await submit();
    expect(await screen.findByRole("heading", { name: "Потребности" })).toBeInTheDocument();
    expect(store).not.toHaveBeenCalled();
  });
});

describe("вход: состояния (KON-81)", () => {
  it("пустые поля: подсказки, aria-invalid, запрос не уходит", async () => {
    fetchMock.mockResolvedValueOnce(json(401, {}));
    renderAt("/login");
    const submit = await screen.findByRole("button", { name: "Войти" });
    expect(submit).toHaveClass("primary");
    await userEvent.click(submit);
    expect(screen.getByText("Укажите логин")).toBeInTheDocument();
    expect(screen.getByText("Укажите пароль")).toBeInTheDocument();
    expect(screen.getByLabelText("Логин")).toHaveAttribute("aria-invalid", "true");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("отправка: поля readOnly (не disabled), кнопка недоступна, повторный submit не шлёт запрос", async () => {
    let release!: (r: Response) => void;
    fetchMock
      .mockResolvedValueOnce(json(401, {}))
      .mockReturnValueOnce(new Promise<Response>((r) => (release = r)));
    renderAt("/login");
    await userEvent.type(await screen.findByLabelText("Логин"), "test.user");
    await userEvent.type(screen.getByLabelText("Пароль"), FAKE_PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    const busy = await screen.findByRole("button", { name: "Входим…" });
    expect(busy).toBeDisabled();
    for (const label of ["Логин", "Пароль"]) {
      const input = screen.getByLabelText(label);
      expect(input).toHaveAttribute("readonly");
      expect(input).not.toBeDisabled();
    }
    fireEvent.submit(busy.closest("form")!);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    release(json(401, { detail: "Неверный логин или пароль, либо нет доступа" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

describe("токены стилей (KON-81, D1)", () => {
  // vitest отдаёт пустой CSS через ?raw, поэтому читаем файл напрямую от корня frontend (в проекте нет типов node).
  let css = "";
  beforeAll(async () => {
    const fs = "node:fs";
    const { readFileSync } = (await import(/* @vite-ignore */ fs)) as { readFileSync: (p: string, e: string) => string };
    css = readFileSync("src/styles.css", "utf-8");
  });
  it("нет литералов #0b8a5c и nav-цветов вне :root", () => {
    const body = css.replace(/:root\s*\{[^}]*\}/g, "");
    for (const lit of ["#0b8a5c", "#3e8ef7", "#cbd2d9", "#52606d"]) expect(body).not.toContain(lit);
  });

  it("--primary и --primary-hover заданы в обеих темах", () => {
    expect(css).toMatch(/--primary: #2870d4; --primary-hover: #235fc0;/);
    expect(css).toMatch(/--primary: #2470c8; --primary-hover: #1e5fad;/);
  });

  it("--accent: светлая #2368b8 (KON-150), тёмная #3e8ef7 без изменений", () => {
    expect(css).toMatch(/--accent: #3e8ef7;/);
    expect(css).toMatch(/--accent: #2368b8;/);
    expect(css).not.toMatch(/--accent: #1f6fd6;/);
  });
});

describe("палитра А2 и чипы статусов (KON-150)", () => {
  let css = "";
  beforeAll(async () => {
    const fs = "node:fs";
    const { readFileSync } = (await import(/* @vite-ignore */ fs)) as { readFileSync: (p: string, e: string) => string };
    css = readFileSync("src/styles.css", "utf-8");
  });

  it("токены светлой темы по decision.md п. 1, нет прежних литералов", () => {
    const light = css.slice(css.indexOf("prefers-color-scheme: light"), css.indexOf("html {"));
    for (const v of ["--bg: #f7f8fa", "--panel-2: #eef1f5", "--border: #dde3ea", "--text: #1e293b", "--muted: #5a6a7f", "--nav: #202b36", "--warn: #8f5c10"])
      expect(light).toContain(v);
    expect(css).not.toMatch(/#fdfcf9|#f5f3ee|#e4e0d7/);
    expect(css).toContain("--secondary: #2368b8; --secondary-hover-bg: rgba(35, 104, 184, 0.08)");
    expect(light).toContain("--ok: #17724f");
    expect(css.slice(0, css.indexOf("@media (prefers-color-scheme: light)"))).toContain("--ok: #34c38f");
    expect(css).toContain(".hit--active { background: var(--warn); color: #000; }");
    expect(css).toContain("@media (prefers-color-scheme: light) { .hit--active { color: #fff; } }");
    expect(css).toContain(':root[data-theme="light"] .hit--active { color: #fff; }');
  });

  it("чип-токены и заливка строк: светлые значения, в тёмной = прежний вид", () => {
    const dark = css.slice(0, css.indexOf("@media (prefers-color-scheme: light)"));
    for (const t of ["ok", "info", "err", "warn", "neu"])
      expect(dark).toContain(`--chip-${t}-bg: var(--panel-2); --chip-${t}-fg: var(--ok)`);
    expect(dark).toContain("--row-hover: var(--panel-2); --row-sel: var(--panel-2)");
    expect(css).toContain("--chip-ok-bg: #dcf4e7; --chip-ok-fg: #12633f");
    expect(css).toContain("--row-hover: #f1f4f9; --row-sel: #e9eefc");
    expect(css).toContain(".needs tbody tr.is-selected { background: var(--row-sel); box-shadow: inset 3px 0 0 var(--primary); }");
    expect(css).toContain(".needs tbody tr:hover { background: var(--row-hover); }");
    for (const m of ["ok", "info", "err", "warn"]) expect(css).toContain(`.status--${m} {`);
  });
});

describe("модалки без горизонтального скролла (KON-85, D5)", () => {
  let css = "";
  beforeAll(async () => {
    const fs = "node:fs";
    const { readFileSync } = (await import(/* @vite-ignore */ fs)) as { readFileSync: (p: string, e: string) => string };
    css = readFileSync("src/styles.css", "utf-8");
  });
  const rule = (sel: string) => css.split(/\r?\n/).find((l) => l.startsWith(sel + " {")) ?? "";

  it("тело модалки: overflow-x hidden, перенос слов, min-width 0", () => {
    const r = rule(".modal__body");
    expect(r).toContain("overflow-x: hidden");
    expect(r).toContain("overflow-wrap: anywhere");
    expect(r).toContain("min-width: 0");
  });

  it("сетка профиля: колонка значений minmax(0, 1fr), ширина 720px", () => {
    expect(css).toMatch(/\.modal--profile \.profile \{ grid-template-columns: 150px minmax\(0, 1fr\)/);
    expect(rule(".modal--profile")).toContain("min(720px, 92%)");
  });

  it("узкая модалка, .pre и .cv-text переносят длинные строки", () => {
    expect(rule(".modal")).toContain("overflow-wrap: anywhere");
    expect(css).toMatch(/\.modal \.pre, \.modal \.cv-text \{[^}]*overflow-wrap: anywhere/);
  });
});

describe("«Настройки»", () => {
  it("не-админ: пункта нет, прямой переход даёт «Нет доступа»", async () => {
    fetchMock.mockResolvedValue(json(200, me("user")));
    renderAt("/settings");
    expect(await screen.findByRole("heading", { name: "Нет доступа" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Настройки" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Резюме" })).toBeInTheDocument();
  });

  it("админ: пункт виден, все разделы открываются", async () => {
    fetchMock.mockResolvedValue(json(200, me("admin")));
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("link", { name: "Настройки" }));
    expect(await screen.findByRole("heading", { name: "Настройки" })).toBeInTheDocument();
    for (const t of ["Потребности", "Сотрудники", "Резюме"]) {
      await userEvent.click(screen.getByRole("link", { name: t }));
      expect(await screen.findByRole("heading", { name: t })).toBeInTheDocument();
    }
  });
});

describe("API-клиент", () => {
  it("«Выйти» шлёт CSRF-заголовок и ведёт на вход", async () => {
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(url === "/api/auth/logout" ? new Response(null, { status: 204 }) : json(200, me("user"))),
    );
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "Выйти" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Вход" })).toBeInTheDocument());
    const call = fetchMock.mock.calls.find((c) => c[0] === "/api/auth/logout")!;
    expect(call[1].method).toBe("POST");
    expect(call[1].headers["X-CSRF-Token"]).toBe("FAKE_csrf");
  });

  it("шапка: ФИО с title «логин · роль», «Выйти» с aria-label и title", async () => {
    fetchMock.mockResolvedValue(json(200, { ...me("admin"), display_name: "Иванова Анастасия Петровна" }));
    renderAt("/needs");
    const name = await screen.findByText("Иванова Анастасия");
    expect(name).toHaveAttribute("title", "test.user · администратор");
    const out = screen.getByRole("button", { name: "Выйти" });
    expect(out).toHaveAttribute("title", "Выйти");
    expect(out).toHaveAttribute("aria-label", "Выйти");
  });

  it("после выхода сообщения «Сессия истекла» нет", async () => {
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(url === "/api/auth/logout" ? new Response(null, { status: 204 }) : json(200, me("user"))),
    );
    renderAt("/needs");
    await userEvent.click(await screen.findByRole("button", { name: "Выйти" }));
    await screen.findByRole("heading", { name: "Вход" });
    expect(screen.queryByText(/Сессия истекла/)).not.toBeInTheDocument();
  });

  it("401 на защищённом запросе уводит на вход с сообщением «Сессия истекла»", async () => {
    const { request } = await import("../api/client");
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(url === "/api/auth/me" ? json(200, me("user")) : json(401, { detail: "Требуется вход" })),
    );
    renderAt("/needs");
    await screen.findByRole("heading", { name: "Потребности" });
    await expect(request("GET", "/admin/users")).rejects.toThrow("Требуется вход");
    expect(await screen.findByRole("heading", { name: "Вход" })).toBeInTheDocument();
    expect(
      screen.getByText("Сессия истекла. Войдите снова — вы вернётесь на прежнюю страницу."),
    ).toBeInTheDocument();
  });
});
