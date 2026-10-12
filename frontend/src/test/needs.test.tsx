import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type { NeedOut, NeedsPage, SyncStamp } from "../api/types";

const ME = {
  login: "test.user",
  role: "user",
  jira_status: { state: "ok" },
  sections: [],
  csrf_token: "FAKE_csrf",
};

const STAMP: SyncStamp = {
  scope: "needs",
  state: "ok",
  stale: false,
  last_success_at: "2026-10-09T10:00:00Z",
  paused: false,
  code: "",
  message: "",
  interval_sec: 300,
};

const GROUPS = {
  groups: [
    { key: "all", id: null, name: "Все статусы", count: 2 },
    { key: "1", id: 1, name: "Без кандидата", count: 1 },
    { key: "other", id: null, name: "Прочие", count: 1 },
  ],
  statuses: [],
  sync: STAMP,
};

function need(over: Partial<NeedOut> = {}): NeedOut {
  return {
    jira_key: "AN-1",
    title: "Аналитик 1С",
    status: "Поиск кандидата",
    epic_key: "AN-100",
    epic_name: "Клиент А",
    contract_type: "Прямой контракт",
    rate: { amount: 2750, currency: "RUB", includes_vat: false, unit: "час", proposer: "РО" },
    jira_created_at: "2026-10-01T08:30:00Z",
    jira_updated_at: "2026-10-09T09:00:00Z",
    ...over,
  };
}

function page(items: NeedOut[], sync: Partial<SyncStamp> = {}): NeedsPage {
  return { items, total: items.length, limit: 100, offset: 0, sync: { ...STAMP, ...sync } };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

type Route = (url: string, init?: RequestInit) => Response | undefined;
const fetchMock = vi.fn();

// Маршрутизатор: /auth/me и группы по умолчанию, остальное задаёт тест.
function route(extra: Route) {
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const r = extra(url, init);
    if (r) return r;
    if (url.endsWith("/auth/me")) return json(ME);
    if (url.endsWith("/sync/needs/groups")) return json(GROUPS);
    return json({ detail: "не найдено" }, 404);
  });
}

function renderNeeds() {
  return render(
    <MemoryRouter initialEntries={["/needs"]}>
      <App />
    </MemoryRouter>,
  );
}

const store = new Map<string, string>();
const storageStub = {
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, v),
};

beforeEach(() => {
  store.clear();
  vi.stubGlobal("localStorage", storageStub);
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("Потребности", () => {
  it("таблица: ключ, статус как есть, клиент, контракт, ставка полностью", async () => {
    route((u) => (u.includes("/sync/needs?") ? json(page([need()])) : undefined));
    renderNeeds();
    const row = (await screen.findByText("AN-1")).closest("tr")!;
    expect(within(row).getByText("Аналитик 1С")).toBeInTheDocument();
    expect(within(row).getByText("Поиск кандидата")).toBeInTheDocument();
    expect(within(row).getByText(/Клиент А/)).toBeInTheDocument();
    expect(within(row).getByText("Прямой контракт")).toBeInTheDocument();
    expect(within(row).getByText(/2\s750 RUB \/ час/)).toBeInTheDocument();
    expect(within(row).getByText(/без НДС/)).toBeInTheDocument();
    expect(within(row).getByText("РО")).toBeInTheDocument();
    expect(within(row).queryByText(/предлагает/)).toBeNull();
  });

  it("колонки: «Автор» вторая после «Ключ», «Обновлена» нет; пустой автор - «—»", async () => {
    route((u) =>
      u.includes("/sync/needs?")
        ? json(page([need({ author: "Автор Тестовый" }), need({ jira_key: "AN-2", author: null })]))
        : undefined,
    );
    renderNeeds();
    await screen.findByText("AN-1");
    const heads = screen.getAllByRole("columnheader").map((h) => h.textContent?.trim());
    expect(heads.slice(0, 3)).toEqual(["Ключ", "Автор", "Название"]);
    expect(heads.some((h) => h?.includes("Обновлена"))).toBe(false);
    const r1 = screen.getByText("AN-1").closest("tr")!;
    expect(within(r1).getAllByRole("cell")[1]).toHaveTextContent("Автор Тестовый");
    const r2 = screen.getByText("AN-2").closest("tr")!;
    expect(within(r2).getAllByRole("cell")[1]).toHaveTextContent("—");
    expect(within(r1).getAllByRole("cell")).toHaveLength(heads.length);
  });

  it("пустая ставка - «не указана»; с НДС", async () => {
    route((u) =>
      u.includes("/sync/needs?")
        ? json(page([need({ rate: {} }), need({ jira_key: "AN-2", rate: { amount: 10, currency: "USD", includes_vat: true } })]))
        : undefined,
    );
    renderNeeds();
    expect(await screen.findByText("не указана")).toBeInTheDocument();
    expect(screen.getByText(/10 USD/)).toBeInTheDocument();
    expect(screen.getByText(/с НДС/)).toBeInTheDocument();
  });

  it("фильтр по группам: стартовая «Без кандидата», клик меняет запрос и запоминается", async () => {
    const urls: string[] = [];
    route((u) => {
      if (u.includes("/sync/needs?")) {
        urls.push(u);
        return json(page([need()]));
      }
    });
    renderNeeds();
    await screen.findByText("AN-1");
    expect(urls[0]).toContain("group=1");
    expect(urls[0]).toContain("limit=100");
    await userEvent.click(screen.getByRole("button", { name: /Прочие/ }));
    await waitFor(() => expect(urls[urls.length - 1]).toContain("group=other"));
    expect(localStorage.getItem("needs.group")).toBe("other");
    expect(screen.getByRole("button", { name: /Все статусы/ })).toBeInTheDocument();
  });

  it("поиск и лимит передаются в API (≤ 500)", async () => {
    const urls: string[] = [];
    route((u) => {
      if (u.includes("/sync/needs?")) {
        urls.push(u);
        return json(page([need()]));
      }
    });
    renderNeeds();
    await screen.findByText("AN-1");
    await userEvent.type(screen.getByLabelText("Поиск по ключу и названию"), "abc{enter}");
    await waitFor(() => expect(urls[urls.length - 1]).toContain("q=abc"));
    await userEvent.selectOptions(screen.getByLabelText("На странице"), "500");
    await waitFor(() => expect(urls[urls.length - 1]).toContain("limit=500"));
  });

  it("колонка «Создана»: дата и время; сортировка по клику asc/desc, сброс на 1-ю страницу", async () => {
    const urls: string[] = [];
    route((u) => {
      if (u.includes("/sync/needs?")) {
        urls.push(u);
        return json(page([need()]));
      }
    });
    renderNeeds();
    await screen.findByText("AN-1");
    const th = screen.getByRole("columnheader", { name: /Создана/ });
    expect(th).toHaveAttribute("aria-sort", "descending"); // по умолчанию новые сверху
    expect(urls[0]).toContain("sort=created");
    expect(urls[0]).toContain("order=desc");
    expect(screen.getByText(/01\.10\.2026/)).toBeInTheDocument();
    await userEvent.click(within(th).getByRole("button"));
    await waitFor(() => expect(urls[urls.length - 1]).toContain("order=asc"));
    expect(screen.getByRole("columnheader", { name: /Создана/ })).toHaveAttribute("aria-sort", "ascending");
    await userEvent.click(within(screen.getByRole("columnheader", { name: /Создана/ })).getByRole("button"));
    await waitFor(() => expect(urls[urls.length - 1]).toContain("order=desc"));
    // сортировка по «Обновлена» из интерфейса убрана
    expect(screen.queryByRole("columnheader", { name: /Обновлена/ })).toBeNull();
  });

  it("фильтр «Создана с / по» уходит в API вместе с группой и поиском; сброс убирает даты", async () => {
    const urls: string[] = [];
    route((u) => {
      if (u.includes("/sync/needs?")) {
        urls.push(u);
        return json(page([need()]));
      }
    });
    renderNeeds();
    await screen.findByText("AN-1");
    expect(urls[0]).not.toContain("created_from");
    expect(screen.getByRole("button", { name: "Сбросить даты" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Создана с"), "2026-10-01");
    await userEvent.type(screen.getByLabelText("по"), "2026-10-09");
    await waitFor(() => expect(urls[urls.length - 1]).toContain("created_to=2026-10-09"));
    const last = urls[urls.length - 1];
    expect(last).toContain("created_from=2026-10-01");
    expect(last).toContain("group=1");
    await userEvent.click(screen.getByRole("button", { name: /Прочие/ }));
    await waitFor(() => expect(urls[urls.length - 1]).toContain("group=other"));
    expect(urls[urls.length - 1]).toContain("created_from=2026-10-01"); // диапазон держится в любой группе
    await userEvent.click(screen.getByRole("button", { name: "Сбросить даты" }));
    await waitFor(() => expect(urls[urls.length - 1]).not.toContain("created_from"));
    expect(urls[urls.length - 1]).not.toContain("created_to");
    expect(screen.getByLabelText("Создана с")).toHaveValue("");
  });

  it("диапазон «с» позже «по»: предупреждение, запрос не уходит", async () => {
    const urls: string[] = [];
    route((u) => {
      if (u.includes("/sync/needs?")) {
        urls.push(u);
        return json(page([need()]));
      }
    });
    renderNeeds();
    await screen.findByText("AN-1");
    const before = urls.length;
    await userEvent.type(screen.getByLabelText("по"), "2026-10-01");
    await waitFor(() => expect(urls.length).toBeGreaterThan(before));
    const n = urls.length;
    await userEvent.type(screen.getByLabelText("Создана с"), "2026-10-09");
    expect(await screen.findByText(/позже даты «по»/)).toBeInTheDocument();
    expect(urls.length).toBe(n);
  });

  it("«Обновить»: POST scope=needs, перезагрузка и штамп", async () => {
    let refreshBody = "";
    route((u, init) => {
      if (u.endsWith("/sync/refresh")) {
        refreshBody = String(init?.body);
        return json({ scope: "needs", ok: true, sync: STAMP });
      }
      if (u.includes("/sync/needs?")) return json(page([need()]));
    });
    renderNeeds();
    await screen.findByText("AN-1");
    expect(screen.getByText(/актуально на/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Обновить" }));
    await waitFor(() => expect(refreshBody).toBe('{"scope":"needs"}'));
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter((c) => String(c[0]).includes("/sync/needs?")).length).toBeGreaterThan(1),
    );
  });

  it.each([
    [409, "needs_reauthorization", "Нужна повторная авторизация"],
    [403, "no_permission", "Нет прав в Jira"],
    [503, "unavailable", "Jira недоступна"],
    [502, "blocked", "Jira отклонила запрос"],
  ])("ошибка «Обновить» %s по detail.state=%s", async (status, state, label) => {
    route((u) => {
      if (u.endsWith("/sync/refresh")) return json({ detail: { state, code: "c", message: "Текст Jira как есть" } }, status);
      if (u.includes("/sync/needs?")) return json(page([need()]));
    });
    renderNeeds();
    await screen.findByText("AN-1");
    await userEvent.click(screen.getByRole("button", { name: "Обновить" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(label);
    expect(alert).toHaveTextContent("Текст Jira как есть");
    expect(screen.getByText("AN-1")).toBeInTheDocument(); // кэш остаётся
  });

  it("пометки sync: stale, paused, needs_reauthorization и message как есть", async () => {
    route((u) =>
      u.includes("/sync/needs?")
        ? json(page([need()], { stale: true, paused: true, state: "needs_reauthorization", message: "Сессия Jira истекла" }))
        : undefined,
    );
    renderNeeds();
    await screen.findByText("AN-1");
    expect(screen.getByText("опрос приостановлен")).toBeInTheDocument();
    expect(screen.getByText("нужна повторная авторизация")).toBeInTheDocument();
    expect(screen.getByText("данные могут отставать от Jira")).toBeInTheDocument();
    expect(screen.getByText("Сессия Jira истекла")).toBeInTheDocument();
  });

  it("пусто: «Нет данных», при paused - объяснение", async () => {
    route((u) => (u.includes("/sync/needs?") ? json(page([], { paused: true, state: "paused", last_success_at: null })) : undefined));
    renderNeeds();
    expect(await screen.findByText("Нет данных")).toBeInTheDocument();
    expect(screen.getByText(/Опрос Jira приостановлен/)).toBeInTheDocument();
    expect(screen.getByText("ещё не синхронизировано")).toBeInTheDocument();
  });

  it("ошибка списка", async () => {
    route((u) => (u.includes("/sync/needs?") ? json({ detail: "Сервис недоступен" }, 500) : undefined));
    renderNeeds();
    expect(await screen.findByRole("alert")).toHaveTextContent("Сервис недоступен");
  });

  it("правила клиента при раскрытии: только заполненные", async () => {
    route((u) => {
      if (u.includes("/sync/needs?")) return json(page([need()]));
      if (u.includes("/sync/epics/AN-100"))
        return json({
          epic_key: "AN-100",
          name: "Клиент А",
          stale: false,
          needs_total: 1,
          rules: [
            { id: "1", name: "Формат резюме", value: "Корпоративный" },
            { id: "2", name: "Пустое правило", value: "  " },
          ],
        });
    });
    renderNeeds();
    await screen.findByText("AN-1");
    await userEvent.click(screen.getByRole("button", { name: "Правила клиента" }));
    expect(await screen.findByText("Формат резюме")).toBeInTheDocument();
    expect(screen.getByText("Корпоративный")).toBeInTheDocument();
    expect(screen.queryByText("Пустое правило")).not.toBeInTheDocument();
  });

  it("401 приложения - конец сессии: переход на вход, без сообщения об ошибке", async () => {
    route((u) => {
      if (u.includes("/sync/needs?")) return json(page([need()]));
      if (u.endsWith("/sync/refresh")) return json({ detail: "Требуется вход" }, 401);
    });
    renderNeeds();
    await screen.findByText("AN-1");
    await userEvent.click(screen.getByRole("button", { name: "Обновить" }));
    await waitFor(() => expect(screen.queryByText("AN-1")).not.toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
