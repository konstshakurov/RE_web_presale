import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { ApiError, api } from "../api/client";
import type { NeedsPage as NeedsData, StatusGroup, SyncStamp } from "../api/types";
import NeedsTable from "../components/NeedsTable";
import SyncNotes from "../components/SyncNotes";
import { refreshErrorText } from "../lib/needs";

const GROUP_KEY = "needs.group"; // выбор фильтра запоминается (FR-035)
const LIMITS = [50, 100, 200, 500]; // API: limit <= 500

function savedGroup(): string | null {
  try {
    return localStorage.getItem(GROUP_KEY);
  } catch {
    return null;
  }
}

// Стартовая группа - «Без кандидата», если есть; иначе первая реальная, иначе «Все статусы».
function startGroup(groups: StatusGroup[]): string {
  const saved = savedGroup();
  if (saved && groups.some((g) => g.key === saved)) return saved;
  const real = groups.filter((g) => g.id !== null && g.id !== undefined);
  return (real.find((g) => g.name === "Без кандидата") ?? real[0])?.key ?? "all";
}

export default function NeedsPage() {
  const [groups, setGroups] = useState<StatusGroup[] | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const [data, setData] = useState<NeedsData | null>(null);
  const [sync, setSync] = useState<SyncStamp | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState("");
  const [info, setInfo] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(100);
  const [offset, setOffset] = useState(0);
  const seq = useRef(0);

  const loadGroups = useCallback(async (): Promise<StatusGroup[]> => {
    try {
      const g = await api.needGroups();
      const list = g.groups ?? [];
      setGroups(list);
      setSync(g.sync);
      return list;
    } catch {
      setGroups([]); // без групп работает «Все статусы»
      return [];
    }
  }, []);

  useEffect(() => {
    void loadGroups().then((g) => setGroup(startGroup(g)));
  }, [loadGroups]);

  const loadNeeds = useCallback(async () => {
    if (group === null) return;
    const id = ++seq.current;
    setLoading(true);
    setError("");
    try {
      const page = await api.needs({ group, q: query, limit, offset });
      if (id !== seq.current) return;
      if (!Array.isArray(page?.items)) throw new Error("bad response");
      setData(page);
      setSync(page.sync);
    } catch (e) {
      if (id !== seq.current) return;
      setError(e instanceof ApiError ? e.message : "Не удалось загрузить потребности");
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, [group, query, limit, offset]);

  useEffect(() => {
    void loadNeeds();
  }, [loadNeeds]);

  function pickGroup(key: string) {
    try {
      localStorage.setItem(GROUP_KEY, key);
    } catch {
      /* хранилище недоступно - выбор не запоминается */
    }
    setOffset(0);
    setGroup(key);
  }

  function onSearch(e: FormEvent) {
    e.preventDefault();
    setOffset(0);
    setQuery(search.trim());
  }

  async function onRefresh() {
    setRefreshing(true);
    setRefreshError("");
    setInfo("");
    try {
      const r = await api.refresh("needs");
      setSync(r.sync);
      if (r.busy) setInfo("Опрос уже идёт, повторно не запускался");
      await Promise.all([loadGroups(), loadNeeds()]);
    } catch (e) {
      if (!(e instanceof ApiError)) setRefreshError("Не удалось обновить данные");
      else if (e.status !== 401) setRefreshError(refreshErrorText(e.detail?.state, e.message)); // 401 - конец сессии, уводит ProtectedRoute
    } finally {
      setRefreshing(false);
    }
  }

  const total = data?.total ?? 0;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  const paused = sync?.paused || sync?.state === "paused";

  return (
    <section>
      <h1>Потребности</h1>

      <nav className="filter" aria-label="Группы статусов">
        {(groups ?? []).map((g) => (
          <button
            key={g.key}
            type="button"
            className={g.key === group ? "filter__item filter__item--active" : "filter__item"}
            aria-pressed={g.key === group}
            onClick={() => pickGroup(g.key)}
          >
            {g.name} <span className="filter__count">{g.count}</span>
          </button>
        ))}
      </nav>

      <div className="toolbar">
        <button type="button" className="primary" onClick={() => void onRefresh()} disabled={refreshing}>
          {refreshing ? "Обновление…" : "Обновить"}
        </button>
        <SyncNotes sync={sync} />
        <form className="search" onSubmit={onSearch} role="search">
          <input
            type="search"
            aria-label="Поиск по ключу и названию"
            placeholder="Ключ или название"
            value={search}
            maxLength={200}
            onChange={(e) => setSearch(e.target.value)}
          />
          <button type="submit">Найти</button>
        </form>
      </div>

      {refreshError && (
        <p className="error" role="alert">
          {refreshError}
        </p>
      )}
      {info && <p className="muted">{info}</p>}

      {loading && !data && <p className="muted">Загрузка…</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {data && !error && (
        <>
          {data.items.length === 0 ? (
            <div className="empty">
              <p>Нет данных</p>
              {paused && (
                <p className="muted">
                  Опрос Jira приостановлен: нет активных пользователей. Нажмите «Обновить», чтобы получить данные сейчас.
                </p>
              )}
            </div>
          ) : (
            <NeedsTable items={data.items} />
          )}
          <div className="pager">
            <span>
              {from}–{to} из {total}
            </span>
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
              Назад
            </button>
            <button type="button" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>
              Вперёд
            </button>
            <label>
              На странице
              <select
                value={limit}
                onChange={(e) => {
                  setLimit(Number(e.target.value));
                  setOffset(0);
                }}
              >
                {LIMITS.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </>
      )}
    </section>
  );
}
