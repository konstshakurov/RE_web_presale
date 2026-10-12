import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { GroupOut, StatusGroupsData } from "../api/types";

const ME = {
  login: "boss",
  role: "admin",
  jira_status: { state: "ok" },
  sections: [],
  csrf_token: "FAKE_csrf",
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const fetchMock = vi.fn();

const ALL = ["Интервью", "Кандидатов нет", "Найм", "Отложено", "Поиск кандидата"];
const COUNTS: Record<string, number> = { Найм: 2, "Поиск кандидата": 3, "Кандидатов нет": 1 };

// Мини-сервер групп: хранит состав в памяти и считает «Прочие», как backend.
function server() {
  let nextId = 3;
  const groups: GroupOut[] = [
    { id: 1, name: "Без кандидата", sort_order: 1, statuses: ["Найм", "Поиск кандидата"], need_count: 5 },
    { id: 2, name: "Интервью", sort_order: 2, statuses: ["Интервью"], need_count: 0 },
  ];
  const recount = (g: GroupOut) => ({
    ...g,
    statuses: [...g.statuses].sort(),
    need_count: g.statuses.reduce((n, s) => n + (COUNTS[s] ?? 0), 0),
  });
  const data = (): StatusGroupsData => {
    const grouped = new Set(groups.flatMap((g) => g.statuses));
    const infos = ALL.map((s) => ({
      status: s,
      count: COUNTS[s] ?? 0,
      group_ids: groups.filter((g) => g.statuses.includes(s)).map((g) => g.id),
    }));
    const other = infos.filter((i) => !grouped.has(i.status));
    return {
      groups: groups.map(recount),
      statuses: infos,
      other,
      other_count: other.reduce((n, i) => n + i.count, 0),
      reserved: ["Прочие", "Все статусы"],
    };
  };
  const handler = (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const body = init.body ? JSON.parse(init.body as string) : {};
    const path = url.replace("/api", "");
    if (path === "/auth/me") return json(ME);
    if (path === "/admin/users") return json([]);
    if (path === "/admin/status-groups" && method === "GET") return json(data());
    if (path === "/admin/status-groups" && method === "POST") {
      if (groups.some((g) => g.name.toLowerCase() === body.name.toLowerCase()))
        return json({ detail: "Группа с таким названием уже есть" }, 409);
      groups.push({ id: nextId++, name: body.name, sort_order: groups.length + 1, statuses: [], need_count: 0 });
      return json(groups[groups.length - 1], 201);
    }
    if (path === "/admin/status-groups/order" && method === "PUT") {
      const order: number[] = body.ids;
      groups.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
      return json(groups);
    }
    let m = path.match(/^\/admin\/status-groups\/(\d+)\/statuses(\?status=(.*))?$/);
    if (m) {
      const g = groups.find((x) => x.id === Number(m![1]))!;
      if (method === "POST") g.statuses.push(body.status);
      else g.statuses = g.statuses.filter((s) => s !== decodeURIComponent(m![3]));
      return json(recount(g));
    }
    m = path.match(/^\/admin\/status-groups\/(\d+)$/);
    if (m && method === "PATCH") {
      const g = groups.find((x) => x.id === Number(m![1]))!;
      g.name = body.name;
      return json(recount(g));
    }
    if (m && method === "DELETE") {
      groups.splice(groups.findIndex((x) => x.id === Number(m![1])), 1);
      return new Response(null, { status: 204 });
    }
    return json({}, 404);
  };
  return handler;
}

function calls(match: (url: string, method: string) => boolean) {
  return fetchMock.mock.calls.filter((c) => match(c[0], c[1]?.method ?? "GET"));
}

async function openTab() {
  render(
    <MemoryRouter initialEntries={["/settings"]}>
      <App />
    </MemoryRouter>,
  );
  await userEvent.click(await screen.findByRole("tab", { name: "Группы статусов" }));
  await screen.findByRole("region", { name: "Без кандидата" });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  const h = server();
  fetchMock.mockImplementation((url: string, init?: RequestInit) => Promise.resolve(h(url, init)));
});
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("Настройки: группы статусов", () => {
  it("показывает группы со счётчиками и статусы «Прочих»", async () => {
    await openTab();
    const other = screen.getByRole("region", { name: "Прочие" });
    expect(within(other).getByText("Кандидатов нет")).toBeInTheDocument();
    expect(within(other).getByText("Отложено")).toBeInTheDocument();
    expect(within(other).queryByText("Найм")).not.toBeInTheDocument();
    const group = screen.getByRole("region", { name: "Без кандидата" });
    expect(within(group).getByText("Найм")).toBeInTheDocument();
    expect(within(group).getByText("5")).toBeInTheDocument();
  });

  it("добавление статуса: статус уходит из «Прочих» в группу", async () => {
    await openTab();
    const group = screen.getByRole("region", { name: "Без кандидата" });
    await userEvent.selectOptions(within(group).getByLabelText(/Статус для группы/), "Отложено");
    await userEvent.click(within(group).getByRole("button", { name: "Добавить статус" }));
    await waitFor(() =>
      expect(within(screen.getByRole("region", { name: "Без кандидата" })).getByText("Отложено")).toBeInTheDocument(),
    );
    expect(within(screen.getByRole("region", { name: "Прочие" })).queryByText("Отложено")).not.toBeInTheDocument();
    const post = calls((u, m) => u === "/api/admin/status-groups/1/statuses" && m === "POST")[0];
    expect(JSON.parse(post[1].body)).toEqual({ status: "Отложено" });
    expect(post[1].headers["X-CSRF-Token"]).toBe("FAKE_csrf");
  });

  it("удаление статуса из группы возвращает его в «Прочие»", async () => {
    await openTab();
    await userEvent.click(screen.getByRole("button", { name: "Убрать статус «Найм» из группы «Без кандидата»" }));
    await waitFor(() =>
      expect(within(screen.getByRole("region", { name: "Прочие" })).getByText("Найм")).toBeInTheDocument(),
    );
    const del = calls((u, m) => m === "DELETE" && u.startsWith("/api/admin/status-groups/1/statuses"));
    expect(del[0][0]).toBe(`/api/admin/status-groups/1/statuses?status=${encodeURIComponent("Найм")}`);
  });

  it("создание, переименование и удаление группы", async () => {
    await openTab();
    await userEvent.type(screen.getByLabelText("Новая группа"), "Моя группа");
    await userEvent.click(screen.getByRole("button", { name: "Создать группу" }));
    const mine = await screen.findByRole("region", { name: "Моя группа" });

    await userEvent.click(within(mine).getByRole("button", { name: "Переименовать" }));
    const input = within(mine).getByLabelText("Название группы");
    await userEvent.clear(input);
    await userEvent.type(input, "Другая");
    await userEvent.click(within(mine).getByRole("button", { name: "Сохранить" }));
    const renamed = await screen.findByRole("region", { name: "Другая" });

    await userEvent.click(within(renamed).getByRole("button", { name: "Удалить" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Подтвердить" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Другая" })).not.toBeInTheDocument());
  });

  it("ошибка сервера (имя занято, зарезервировано) показывается как есть", async () => {
    await openTab();
    await userEvent.type(screen.getByLabelText("Новая группа"), "интервью");
    await userEvent.click(screen.getByRole("button", { name: "Создать группу" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Группа с таким названием уже есть");
  });

  it("порядок: «Ниже» шлёт новый порядок id", async () => {
    await openTab();
    await userEvent.click(screen.getByRole("button", { name: "Ниже: Без кандидата" }));
    await waitFor(() => {
      const put = calls((u, m) => u === "/api/admin/status-groups/order" && m === "PUT");
      expect(put).toHaveLength(1);
      expect(JSON.parse(put[0][1].body)).toEqual({ ids: [2, 1] });
    });
    expect(screen.getByRole("button", { name: "Выше: Без кандидата" })).toBeEnabled();
  });
});
