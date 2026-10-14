import { render, screen, waitFor } from "@testing-library/react";
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
    expect(screen.getAllByAltText("regoose")).toHaveLength(1);
    expect(screen.getAllByRole("img")).toHaveLength(1);
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

  it("загрузка: «Входим…», поля и кнопка заблокированы", async () => {
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
    expect(screen.getByLabelText("Логин")).toBeDisabled();
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
    expect(css).toMatch(/--primary: #1f6fd6; --primary-hover: #1a5bb8;/);
  });

  it("--accent: светлая #1b66c9 (текст >=4.5:1, KON-86), тёмная #3e8ef7 без изменений", () => {
    expect(css).toMatch(/--accent: #3e8ef7;/);
    expect(css).toMatch(/--accent: #1b66c9;/);
    expect(css).not.toMatch(/--accent: #1f6fd6;/);
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
