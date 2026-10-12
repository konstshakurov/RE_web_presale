import { useEffect, useRef } from "react";

import { POLL_MS } from "./matching";

// Периодический вызов tick, пока active; перекрывающиеся вызовы пропускаются. Расчёт идёт на сервере -
// опрос не блокирует интерфейс и прекращается при закрытии блока.
export function usePolling(active: boolean, tick: () => Promise<void>, ms = POLL_MS) {
  const ref = useRef(tick);
  ref.current = tick;
  useEffect(() => {
    if (!active) return;
    let busy = false;
    const id = setInterval(() => {
      if (busy) return;
      busy = true;
      ref.current().finally(() => {
        busy = false;
      });
    }, ms);
    return () => clearInterval(id);
  }, [active, ms]);
}
