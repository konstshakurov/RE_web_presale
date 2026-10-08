import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
    expect(screen.getByAltText("RE")).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
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

  it("401 на защищённом запросе уводит на вход", async () => {
    const { request } = await import("../api/client");
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(url === "/api/auth/me" ? json(200, me("user")) : json(401, { detail: "Требуется вход" })),
    );
    renderAt("/needs");
    await screen.findByRole("heading", { name: "Потребности" });
    await expect(request("GET", "/admin/users")).rejects.toThrow("Требуется вход");
    expect(await screen.findByRole("heading", { name: "Вход" })).toBeInTheDocument();
  });
});
