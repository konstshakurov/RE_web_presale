import type { SyncStamp } from "../api/types";
import SyncNotes from "./SyncNotes";

interface Props {
  sync: SyncStamp | null;
  refreshing: boolean;
  onRefresh: () => void;
  error: string;
  info: string;
}

// Кнопка «Обновить» (опрос сотрудников по кнопке, FR-028) и штамп «актуально на».
export default function EmployeeSyncBar({ sync, refreshing, onRefresh, error, info }: Props) {
  return (
    <>
      <div className="toolbar">
        <button type="button" className="primary" onClick={onRefresh} disabled={refreshing}>
          {refreshing ? "Обновление…" : "Обновить"}
        </button>
        <SyncNotes sync={sync} />
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {info && <p className="muted">{info}</p>}
    </>
  );
}
