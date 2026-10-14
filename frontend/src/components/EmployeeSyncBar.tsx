import type { SyncStamp } from "../api/types";
import SyncNotes from "./SyncNotes";

interface Props {
  title: string;
  sync: SyncStamp | null;
  refreshing: boolean;
  onRefresh: () => void;
  error: string;
  info: string;
}

// Строка заголовка страницы: h1 слева, справа блок «Обновить» (опрос по кнопке, FR-028):
// штамп «актуально на» и пометки слева, кнопка у правого края. Сообщения - строкой ниже.
export default function EmployeeSyncBar({ title, sync, refreshing, onRefresh, error, info }: Props) {
  return (
    <>
      <div className="page-head">
        <h1>{title}</h1>
        <div className="head-sync">
          <SyncNotes sync={sync} />
          <button type="button" className="btn-secondary" onClick={onRefresh} disabled={refreshing}>
            {refreshing ? "Обновление…" : "Обновить"}
          </button>
        </div>
      </div>
      {error && (
        <p className="error head-msg" role="alert">
          {error}
        </p>
      )}
      {info && <p className="muted head-msg">{info}</p>}
    </>
  );
}
