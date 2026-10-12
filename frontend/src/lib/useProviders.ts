import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { ProviderPublic } from "../api/types";

// Провайдеры LLM для выбора пользователем (GET /llm/providers). Ошибка не критична: расчёт пойдёт по умолчанию.
export function useProviders() {
  const [providers, setProviders] = useState<ProviderPublic[]>([]);
  const [providerId, setProviderId] = useState<number | null>(null); // null - провайдер по умолчанию
  useEffect(() => {
    let alive = true;
    api
      .publicProviders()
      .then((list) => alive && Array.isArray(list) && setProviders(list))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  return { providers, providerId, setProviderId };
}
