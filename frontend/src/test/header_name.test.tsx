import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { MeResponse } from "../api/types";

function me(display_name?: string | null): MeResponse {
  return {
    login: "test.user",
    display_name,
    role: "user",
    jira_status: { state: "not_checked" },
    sections: [],
    csrf_token: "FAKE_csrf",
  };
}

const fetchMock = vi.fn();

function renderNeeds() {
  return render(
    <MemoryRouter initialEntries={["/needs"]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

function ok(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("имя пользователя в шапке", () => {
  it("показывает ФИО, если оно есть (в том числе после перезагрузки: /auth/me)", async () => {
    fetchMock.mockResolvedValue(ok(me("Тестовый Пользователь")));
    renderNeeds();
    expect(await screen.findByText("Тестовый Пользователь")).toBeInTheDocument();
    expect(screen.queryByText("test.user")).not.toBeInTheDocument();
  });

  it.each([null, undefined, ""])("без ФИО (%s) показывает логин", async (name) => {
    fetchMock.mockResolvedValue(ok(me(name)));
    renderNeeds();
    expect(await screen.findByText("test.user")).toBeInTheDocument();
  });
});
