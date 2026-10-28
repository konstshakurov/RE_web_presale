import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { DeptHead } from "../api/types";

export type DeptHeadsState = "loading" | "error" | "ready";

// Список РО для фильтра (GET /sync/employees/dept-heads); общий для «Сотрудников» и «Резюме».
export function useDeptHeads() {
  const [list, setList] = useState<DeptHead[]>([]);
  const [state, setState] = useState<DeptHeadsState>("loading");
  const seq = useRef(0);

  const load = useCallback(async () => {
    const id = ++seq.current;
    setState("loading");
    try {
      const r = await api.deptHeads();
      if (id !== seq.current) return;
      if (!Array.isArray(r)) throw new Error("bad response");
      setList(r);
      setState("ready");
    } catch {
      if (id !== seq.current) return;
      setState("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return { list, state, retry: load };
}
