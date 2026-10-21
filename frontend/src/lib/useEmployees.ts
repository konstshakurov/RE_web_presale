import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import type { EmployeeOut, SyncStamp } from "../api/types";
import { refreshErrorText } from "./needs";

// Сотрудники из кэша Jira (все страницы) + обновление по кнопке (FR-028); общий для «Сотрудников» и «Резюме».
export function useEmployees() {
  const [items, setItems] = useState<EmployeeOut[] | null>(null);
  const [counts, setCounts] = useState<{ status: string; count: number }[]>([]);
  const [sync, setSync] = useState<SyncStamp | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState("");
  const [info, setInfo] = useState("");
  const seq = useRef(0);

  const load = useCallback(async () => {
    const id = ++seq.current;
    try {
      const page = await api.allEmployees();
      if (id !== seq.current) return;
      if (!Array.isArray(page?.items)) throw new Error("bad response");
      setItems(page.items);
      setCounts(page.statuses ?? []);
      setSync(page.sync);
      setError("");
    } catch (e) {
      if (id !== seq.current) return;
      setError(e instanceof ApiError ? e.message : "Не удалось загрузить сотрудников");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setRefreshError("");
    setInfo("");
    try {
      const r = await api.refresh("employees");
      setSync(r.sync);
      if (r.busy) setInfo("Опрос уже идёт, повторно не запускался");
      await load();
    } catch (e) {
      if (!(e instanceof ApiError)) setRefreshError("Не удалось обновить данные");
      else if (e.status !== 401) setRefreshError(refreshErrorText(e.detail?.state, e.message));
    } finally {
      setRefreshing(false);
    }
  }, [load]);

  // Локально подставляет ответ сохранения заметки (FR-033), без перезагрузки списка
  const patch = useCallback((key: string, p: Partial<EmployeeOut>) => {
    setItems((cur) => cur && cur.map((e) => (e.jira_key === key ? { ...e, ...p } : e)));
  }, []);

  return { patch, items, counts, sync, error, refreshing, refreshError, info, refresh };
}
