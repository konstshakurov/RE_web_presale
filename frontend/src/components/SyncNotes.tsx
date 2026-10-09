import type { SyncStamp } from "../api/types";
import { formatDateTime, syncNotes } from "../lib/needs";

// Штамп «актуально на» и пометки состояния опроса (FR-028, FR-029).
export default function SyncNotes({ sync }: { sync: SyncStamp | null }) {
  if (!sync) return null;
  const at = formatDateTime(sync.last_success_at);
  return (
    <div className="sync">
      <span className="sync__stamp">{at ? `актуально на ${at}` : "ещё не синхронизировано"}</span>
      {syncNotes(sync).map((n) => (
        <span key={n.kind} className={`badge badge--${n.kind}`}>
          {n.text}
        </span>
      ))}
    </div>
  );
}
