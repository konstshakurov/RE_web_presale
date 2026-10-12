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
  // Чего не хватает до 100%: undefined - блок не показываем (нет оценки LLM), null - не рассчитано
  gaps?: string[] | null;
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

export const GAPS_TITLE = "Чего не хватает до 100%";
export const GAPS_FULL = "Полное совпадение";
export const GAPS_RECALC_HINT = "Нажмите «Пересчитать», чтобы увидеть, чего не хватает";

// Блок «Чего не хватает до 100%» под обоснованием (CR KON-38/KON-41).
export function GapsBlock({ gaps }: { gaps?: string[] | null }) {
  if (gaps === undefined) return null;
  if (gaps === null) return <p className="muted gaps gaps--unknown">{GAPS_RECALC_HINT}</p>;
  if (gaps.length === 0) return <p className="gaps gaps--full">{GAPS_FULL}</p>;
  return (
    <div className="gaps">
      <div className="gaps__title">{GAPS_TITLE}</div>
      <ul>
        {gaps.map((g, i) => (
          <li key={i}>{g}</li>
        ))}
      </ul>
    </div>
  );
}

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
                    <GapsBlock gaps={r.gaps} />
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
