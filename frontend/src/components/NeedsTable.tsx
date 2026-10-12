import { Fragment, useState } from "react";

import type { NeedOut, NeedSort, SortOrder } from "../api/types";
import { formatDateTime, formatRate } from "../lib/needs";
import ClientRules from "./ClientRules";
import NeedCandidates from "./matching/NeedCandidates";

export interface NeedsSort {
  sort: NeedSort;
  order: SortOrder;
}

interface Props {
  items: NeedOut[];
  sort?: NeedsSort;
  onSort?: (sort: NeedSort) => void; // клик по заголовку «Создана» / «Обновлена»
}

function SortHeader({ field, label, sort, onSort }: { field: NeedSort; label: string } & Pick<Props, "sort" | "onSort">) {
  const active = sort?.sort === field;
  const dir = active ? sort.order : undefined;
  return (
    <th aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}>
      <button type="button" className="sort" onClick={() => onSort?.(field)}>
        {label}
        <span aria-hidden="true">{dir === "asc" ? " ▲" : dir === "desc" ? " ▼" : ""}</span>
      </button>
    </th>
  );
}

export default function NeedsTable({ items, sort, onSort }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  // Блок «Кандидаты»: start - открыт кнопкой «Сопоставить» (сразу расчёт), view - только сохранённый результат
  const [cand, setCand] = useState<{ key: string; start: boolean } | null>(null);
  return (
    <table className="needs">
      <thead>
        <tr>
          <th>Ключ</th>
          <th>Название</th>
          <th>Статус</th>
          <th>Клиент / эпик</th>
          <th>Тип контракта</th>
          <th>Ставка</th>
          <th>Кандидаты</th>
          <SortHeader field="created" label="Создана" sort={sort} onSort={onSort} />
          <SortHeader field="updated" label="Обновлена" sort={sort} onSort={onSort} />
        </tr>
      </thead>
      <tbody>
        {items.map((n) => {
          const rate = formatRate(n.rate);
          const expanded = open === n.jira_key;
          return (
            <Fragment key={n.jira_key}>
              <tr>
                <td>
                  {n.jira_url ? (
                    <a href={n.jira_url} target="_blank" rel="noreferrer">
                      {n.jira_key}
                    </a>
                  ) : (
                    n.jira_key
                  )}
                </td>
                <td>{n.title}</td>
                <td>
                  <span className="status">{n.status}</span>
                </td>
                <td>
                  {n.epic_name || n.epic_key || "—"}
                  {n.epic_key && (
                    <button
                      type="button"
                      className="link"
                      aria-expanded={expanded}
                      onClick={() => setOpen(expanded ? null : n.jira_key)}
                    >
                      {expanded ? "Скрыть правила" : "Правила клиента"}
                    </button>
                  )}
                </td>
                <td>{n.contract_type || "—"}</td>
                <td className="rate">
                  <strong>{rate.main}</strong>
                  {rate.vat && <span className="rate__vat"> {rate.vat}</span>}
                  {rate.proposer && <div className="muted">{rate.proposer}</div>}
                </td>
                <td>
                  {cand?.key === n.jira_key ? (
                    <button type="button" className="link" aria-expanded onClick={() => setCand(null)}>
                      Скрыть кандидатов
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="primary-small"
                        aria-label={`Сопоставить ${n.jira_key}`}
                        onClick={() => setCand({ key: n.jira_key, start: true })}
                      >
                        Сопоставить
                      </button>
                      <button
                        type="button"
                        className="link"
                        aria-label={`Кандидаты ${n.jira_key}`}
                        onClick={() => setCand({ key: n.jira_key, start: false })}
                      >
                        Кандидаты
                      </button>
                    </>
                  )}
                </td>
                <td>{formatDateTime(n.jira_created_at) || "—"}</td>
                <td>{formatDateTime(n.jira_updated_at) || "—"}</td>
              </tr>
              {expanded && n.epic_key && (
                <tr className="needs__details">
                  <td colSpan={9}>
                    <ClientRules epicKey={n.epic_key} />
                  </td>
                </tr>
              )}
              {cand?.key === n.jira_key && (
                <tr className="needs__details">
                  <td colSpan={9}>
                    <NeedCandidates key={`${n.jira_key}:${cand.start}`} needKey={n.jira_key} autoStart={cand.start} />
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
