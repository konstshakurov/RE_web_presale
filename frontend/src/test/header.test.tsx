import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { MeResponse } from "../api/types";

// Шапка 64 px по design/KON-135 (Pre.UX-9, CR-013).
function me(role: string): MeResponse {
  return { login: "test.user", role, display_name: "Иванова Анастасия Петровна", jira_status: { state: "not_checked" }, sections: [], csrf_token: "FAKE_csrf" };
}
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const fetchMock = vi.fn();
const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );

beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("шапка: чип «Админ», вкладки, пользователь", () => {
  it("админ: чип и «Настройки» есть, имя и «Выйти» на месте", async () => {
    fetchMock.mockResolvedValue(json(200, me("admin")));
    renderAt("/needs");
    expect(await screen.findByText("Админ")).toHaveClass("topbar__badge");
    expect(screen.getByRole("link", { name: "Настройки" })).toBeInTheDocument();
    expect(screen.getByText("Иванова Анастасия")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Выйти" })).toBeInTheDocument();
  });

  it("не-админ: ни чипа, ни «Настройки»; имя и «Выйти» на месте", async () => {
    fetchMock.mockResolvedValue(json(200, me("user")));
    renderAt("/needs");
    expect(await screen.findByText("Иванова Анастасия")).toBeInTheDocument();
    expect(screen.queryByText("Админ")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Настройки" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Выйти" })).toBeInTheDocument();
  });

  it("активная вкладка помечена aria-current", async () => {
    fetchMock.mockResolvedValue(json(200, me("user")));
    renderAt("/employees");
    expect(await screen.findByRole("link", { name: "Сотрудники" })).toHaveAttribute("aria-current", "page");
  });
});

describe("шапка: стили (KON-135)", () => {
  let css = "";
  beforeAll(async () => {
    const fs = "node:fs";
    const { readFileSync } = (await import(/* @vite-ignore */ fs)) as { readFileSync: (p: string, e: string) => string };
    css = readFileSync("src/styles.css", "utf-8");
  });
  const rule = (sel: string) => {
    const start = css.indexOf(`\n${sel} { `) + 1;
    return css.slice(start, css.indexOf("}", start));
  };

  it("высота 64, scroll-padding 64, без компенсаций", () => {
    expect(rule(".topbar")).toContain("height: 64px");
    expect(css).toContain("scroll-padding-top: 64px");
    const bar = css.split("\n").filter((l) => l.startsWith(".topbar"));
    for (const l of bar) {
      expect(l).not.toMatch(/margin-top|margin-bottom|transform|margin: -/);
    }
  });

  it("box-sizing: border-box для шапки и всех её элементов, как в прототипе", () => {
    const l = css.split("\n").find((x) => x.startsWith(".topbar, .topbar *")) ?? "";
    for (const t of [".topbar *", ".topbar *::before", ".topbar *::after", "box-sizing: border-box"]) expect(l).toContain(t);
    // высота вкладки и wordbox 56 вместе с padding-bottom 8 - только при border-box
    expect(rule(".topbar__nav a")).toContain("height: calc(64px - 8px)");
    expect(rule(".topbar__wordbox")).toContain("padding-bottom: 8px");
  });

  it("активная вкладка --bg, вес 400; чип #3D94FF 22/11; без @font-face", () => {
    expect(rule(".topbar__nav a.active")).toContain("background: var(--bg)");
    expect(rule(".topbar__nav a")).toContain("font-weight: 400");
    const b = rule(".topbar__badge");
    for (const t of ["#3D94FF", "height: 22px", "border-radius: 11px", "color: #fff"]) expect(b).toContain(t);
    expect(css).not.toContain("@font-face");
  });
});
