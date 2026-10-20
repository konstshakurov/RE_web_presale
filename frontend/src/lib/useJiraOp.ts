import { useCallback, useRef, useState } from "react";

import { useAuth } from "../auth/AuthContext";
import { toFailure } from "./assign";
import type { OpFailure } from "./assign";

// Запись в Jira с отказами по FR-029: текст как есть, введённое остаётся у вызывающего,
// истёкшая сессия (409 needs_reauthorization) - пароль в том же окне. Пароль не в state: читается из поля и очищается.
export function useJiraOp() {
  const { relogin } = useAuth();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<OpFailure | null>(null);
  const [loginError, setLoginError] = useState("");
  const pwd = useRef<HTMLInputElement>(null);

  // true - операция прошла
  const run = useCallback(async (op: () => Promise<unknown>, fallback: string): Promise<boolean> => {
    setBusy(true);
    setLoginError("");
    try {
      await op();
      setFailure(null);
      return true;
    } catch (e) {
      setFailure(toFailure(e, fallback));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  // true - Jira приняла пароль, можно повторять операцию
  const reauth = useCallback(async (): Promise<boolean> => {
    const input = pwd.current;
    const typed = input?.value ?? "";
    if (input) input.value = "";
    if (!typed) {
      setLoginError("Введите пароль Jira");
      return false;
    }
    setBusy(true);
    setLoginError("");
    try {
      const me = await relogin(typed);
      if (me.jira_status.state !== "ok") {
        setLoginError(me.jira_status.message || "Jira не приняла пароль");
        return false;
      }
      return true;
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : "Не удалось отправить пароль");
      return false;
    } finally {
      setBusy(false);
    }
  }, [relogin]);

  return { busy, failure, loginError, pwd, run, reauth, clear: () => setFailure(null) };
}
