import { Fragment, useEffect, useState } from "react";

import { api } from "../api/client";
import type { NeedOut, NeedSort, SortOrder } from "../api/types";
import { candidatesLabel } from "../lib/matching";
import { formatDateTime, formatRate } from "../lib/needs";
import ClientRules from "./ClientRules";
import MatchingInfo from "./matching/MatchingInfo";
import NeedCandidates from "./matching/NeedCandidates";

export interface NeedsSort {
  sort: NeedSort;
  order: SortOrder;
}

interface Props {
  items: NeedOut[];
  sort?: NeedsSort;
  onSort?: (sort: NeedSort) => void; // клик по заголовку «Создана»
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

// В колонке «Ключ» показываем только номер: ANALYTICS-5903 -> 5903 (ссылка по-прежнему ведёт в Jira)
export function shortKey(key: string): string {
  return key.replace(/^[A-Z][A-Z0-9_]*-(?=\d+$)/, "");
}

export default function NeedsTable({ items, sort, onSort }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  // Число кандидатов в сохранённом результате: нет ключа - расчёта не было, показываем «Сопоставить»
  const [counts, setCounts] = useState<Record<string, number>>({});
  const keysId = items.map((n) => n.jira_key).join(",");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!keysId) return;
    let alive = true;
    api
      .matchCounts(keysId.split(","))
      .then((r) => alive && setCounts(r.counts))
      .catch(() => alive && setCounts({})); // без счётчиков остаётся «Сопоставить» - расчёт откроет сохранённый результат
    return () => {
      alive = false;
    };
  }, [keysId, refresh]);
  // Блок «Кандидаты»: start - открыт кнопкой «Сопоставить» (сразу расчёт), view - только сохранённый результат
  const [cand, setCand] = useState<{ key: string; start: boolean } | null>(null);
  return (
    <table className="needs">
      <thead>
        <tr>
          <th>Ключ</th>
          <th>Автор</th>
          <th>Название</th>
          <th>Статус</th>
          <th>Клиент</th>
          <th>Контракт</th>
          <th>Ставка</th>
          <th>
            Кандидаты <MatchingInfo />
          </th>
          <SortHeader field="created" label="Создана" sort={sort} onSort={onSort} />
        </tr>
      </thead>
      <tbody>
        {items.map((n) => {
          const rate = formatRate(n.rate, false);
          const expanded = open === n.jira_key;
          return (
            <Fragment key={n.jira_key}>
              <tr>
                <td>
                  {n.jira_url ? (
                    <a href={n.jira_url} target="_blank" rel="noreferrer">
                      {shortKey(n.jira_key)}
                    </a>
                  ) : (
                    shortKey(n.jira_key)
                  )}
                </td>
                <td>{n.author || "—"}</td>
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
                  {rate.vat && <span className="rate__vat">{rate.vat}</span>}
                  {rate.proposer && <div className="muted">{rate.proposer}</div>}
                </td>
                <td>
                  {cand?.key === n.jira_key ? (
                    <button
                      type="button"
                      className="link"
                      aria-expanded
                      onClick={() => {
                        setCand(null);
                        setRefresh((x) => x + 1);
                      }}
                    >
                      Скрыть кандидатов
                    </button>
                  ) : counts[n.jira_key] !== undefined ? (
                    <button
                      type="button"
                      className="link"
                      aria-label={`${candidatesLabel(counts[n.jira_key])}, ${n.jira_key}`}
                      onClick={() => setCand({ key: n.jira_key, start: false })}
                    >
                      {candidatesLabel(counts[n.jira_key])}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="primary-small"
                      aria-label={`Сопоставить ${n.jira_key}`}
                      onClick={() => setCand({ key: n.jira_key, start: true })}
                    >
                      Сопоставить
                    </button>
                  )}
                </td>
                <td>{formatDateTime(n.jira_created_at) || "—"}</td>
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
                    <NeedCandidates
                      key={`${n.jira_key}:${cand.start}`}
                      needKey={n.jira_key}
                      autoStart={cand.start}
                      onTotal={(total) => setCounts((c) => (c[n.jira_key] === total ? c : { ...c, [n.jira_key]: total }))}
                    />
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
