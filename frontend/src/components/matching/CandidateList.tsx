import { Fragment, useState } from "react";

import { formatDateTime } from "../../lib/needs";
import { percentTone } from "../../lib/matching";

// Общий вид строки кандидата для сопоставления (FR-036) и поиска по смыслу (FR-032).
export interface CandidateRow {
  key: string;
  name: string;
  status: string;
  percent: number | null; // нет процента (fallback) - показываем «без оценки»
  preliminary?: boolean; // предварительная оценка скрининга
  sourceLabel?: string;
  rationale: string;
  note?: string | null;
  noteAuthor?: string | null;
  noteUpdatedAt?: string | null;
  stale: boolean;
  tags: string[]; // совпавшие технологии
}

interface Props {
  rows: CandidateRow[];
  onResume: (key: string) => void;
  assign?: boolean; // кнопка «Назначить» (только в потребностях)
  assignHint?: string;
}

// Назначение кандидата (FR-008) - этап 2 (KON-44): кнопка пока заглушка.
const ASSIGN_STUB = "Назначение появится на этапе 2";

export default function CandidateList({ rows, onResume, assign = true, assignHint = ASSIGN_STUB }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <table className="needs candidates">
      <thead>
        <tr>
          <th>Кандидат</th>
          <th>Совпадение</th>
          <th>Заметка</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const expanded = open === r.key;
          return (
            <Fragment key={r.key}>
              <tr>
                <td>
                  {r.name}
                  <div className="muted">{r.key}</div>
                  <span className="status">{r.status}</span>
                  {r.stale && (
                    <span className="badge badge--stale" title="Потребность, заметка или модель изменились">
                      устарело
                    </span>
                  )}
                </td>
                <td>
                  <button
                    type="button"
                    className={r.percent === null ? "pct pct--none" : `pct pct--${percentTone(r.percent)}`}
                    aria-expanded={expanded}
                    title="Показать обоснование"
                    onClick={() => setOpen(expanded ? null : r.key)}
                  >
                    {r.percent === null ? "без оценки" : `${r.percent}%`}
                  </button>
                  {r.preliminary && <div className="muted">предварительно</div>}
                  {r.sourceLabel && <div className="muted">{r.sourceLabel}</div>}
                  {r.tags.length > 0 && <div className="muted">{r.tags.join(", ")}</div>}
                </td>
                <td>
                  {r.note ? (
                    <span title={[r.noteAuthor, formatDateTime(r.noteUpdatedAt)].filter(Boolean).join(", ")}>
                      <span className="note-icon" aria-hidden="true">
                        ✎
                      </span>{" "}
                      {r.note}
                    </span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>
                  <div className="card__actions">
                    <button type="button" onClick={() => onResume(r.key)}>
                      Резюме
                    </button>
                    {assign && (
                      <button type="button" disabled title={assignHint}>
                        Назначить
                      </button>
                    )}
                  </div>
                </td>
              </tr>
              {expanded && (
                <tr className="needs__details">
                  <td colSpan={4}>
                    <div className="rationale">{r.rationale || "Обоснование не указано"}</div>
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
