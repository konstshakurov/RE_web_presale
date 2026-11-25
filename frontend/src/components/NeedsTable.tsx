import { Fragment, useEffect, useState } from "react";

import { api } from "../api/client";
import type { DuplicateOut, NeedOut, NeedSort, SortOrder } from "../api/types";
import { candidatesLabel, contractTypeLabel, duplicatesLabel } from "../lib/matching";
import { statusClass } from "../lib/statusTone";
import { formatDateTime, formatRate } from "../lib/needs";
import ClientRules from "./ClientRules";
import { HintLabel } from "./HintButton";
import MatchingInfo from "./matching/MatchingInfo";
import NeedCandidates from "./matching/NeedCandidates";
import RosDialog from "./ros/RosDialog";
import StatusActions from "./StatusActions";
import { rosVisible } from "../lib/ros";

export interface NeedsSort {
  sort: NeedSort;
  order: SortOrder;
}

interface Props {
  items: NeedOut[];
  sort?: NeedsSort;
  onSort?: (sort: NeedSort) => void; // клик по заголовку «Создана»
  onChanged?: () => void; // статус изменён или кандидат назначен: страница перечитывает список
}

// Шеврон облачка (KON-165): на одной линии с текстом, поворот без анимации
function Chevron() {
  return (
    <span className="chev" aria-hidden="true">
      <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor"
        strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="m3.5 6 4.5 4.5L12.5 6" />
      </svg>
    </span>
  );
}

// Треугольник предупреждения (KON-165)
function WarnIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3 2.5 20h19Z" />
      <path d="M12 9.5v5" />
      <path d="M12 17.4v.01" />
    </svg>
  );
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

export default function NeedsTable({ items, sort, onSort, onChanged }: Props) {
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
  // Окно «ROS специалиста» (FR-041, KON-161): ничего не сохраняет
  const [rosNeed, setRosNeed] = useState<NeedOut | null>(null);
  // Блок дублей (FR-014, KON-165): открыт для ключа потребности
  const [dupOpen, setDupOpen] = useState<string | null>(null);
  // Классификация «Похожая/Дубль» (CR-023): optimistic, при ошибке - откат
  const [cls, setCls] = useState<Record<string, "similar" | "duplicate">>({});
  const [clsOpen, setClsOpen] = useState<string | null>(null);
  const classify = (needKey: string, d: DuplicateOut, value: "similar" | "duplicate") => {
    const id = `${needKey}:${d.jira_key}`;
    const prev = cls[id] ?? d.classification;
    if (prev === value) return;
    setCls((c) => ({ ...c, [id]: value }));
    api.setDuplicateClassification(needKey, d.jira_key, value).catch(() => {
      setCls((c) => ({ ...c, [id]: prev }));
    });
  };
  return (
    <>
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
            <HintLabel>
              Кандидаты
              <MatchingInfo />
            </HintLabel>
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
              <tr className={expanded ? "is-selected" : undefined}>
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
                <td>
                  <span className="need-title">{n.title}</span>
                  {(n.duplicates?.length ?? 0) > 0 && (
                    /* Индикатор дублей (FR-014, KON-165): новая строка, статичное облачко */
                    <span className="dup-line">
                      <button
                        type="button"
                        className={dupOpen === n.jira_key ? "dup-pill is-open" : "dup-pill"}
                        aria-expanded={dupOpen === n.jira_key}
                        aria-label={`Дубли: ${duplicatesLabel(n.duplicates!.length)}, ${n.jira_key}`}
                        onClick={() => setDupOpen(dupOpen === n.jira_key ? null : n.jira_key)}
                      >
                        <WarnIcon />
                        {duplicatesLabel(n.duplicates!.length)}
                        <Chevron />
                      </button>
                    </span>
                  )}
                </td>
                <td>
                  <span className={statusClass(n.status)}>{n.status}</span>
                  <StatusActions
                    need={n}
                    onChanged={() => {
                      onChanged?.();
                      setRefresh((x) => x + 1);
                    }}
                  />
                </td>
                <td>
                  {n.epic_name || n.epic_key || "—"}
                  {n.epic_key && (
                    <button
                      type="button"
                      className="link"
                      title="Правила клиента"
                      aria-expanded={expanded}
                      onClick={() => setOpen(expanded ? null : n.jira_key)}
                    >
                      {expanded ? "Скрыть правила" : "Правила"}
                    </button>
                  )}
                </td>
                <td>{contractTypeLabel(n.contract_type)}</td>
                <td className="rate">
                  <strong>{rate.main}</strong>
                  {rate.vat && <span className="rate__vat">{rate.vat}</span>}
                  {rate.proposer && (
                    <div className="muted rate__proposer" title={rate.proposer}>
                      {rate.proposer === "от производства" ? "от произв." : rate.proposer}
                    </div>
                  )}
                </td>
                <td>
                  {cand?.key === n.jira_key ? (
                    <button
                      type="button"
                      className="link"
                      title="Скрыть кандидатов"
                      aria-label="Скрыть кандидатов"
                      aria-expanded
                      onClick={() => {
                        setCand(null);
                        setRefresh((x) => x + 1);
                      }}
                    >
                      Скрыть
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
                  {/* ROS: контурная кнопка под «Сопоставить» (FR-039, placement KON-161) */}
                  {rosVisible(n.status) && (
                    <button
                      type="button"
                      className="ros-small"
                      aria-label={`ROS ${n.jira_key}`}
                      title="ROS специалиста"
                      onClick={() => setRosNeed(n)}
                    >
                      ROS
                    </button>
                  )}
                </td>
                <td>{formatDateTime(n.jira_created_at) || "—"}</td>
              </tr>
              {expanded && n.epic_key && (
                <tr className="needs__details is-selected">
                  <td colSpan={9}>
                    <ClientRules epicKey={n.epic_key} needKey={n.jira_key} />
                  </td>
                </tr>
              )}
              {dupOpen === n.jira_key && (n.duplicates?.length ?? 0) > 0 && (
                /* Блок дублей (FR-014, KON-165): ключ на линии «Название», классификация (CR-023) */
                <tr className="needs__details dup-block">
                  <td colSpan={9}>
                    {n.duplicates!.map((d) => {
                      const id = `${n.jira_key}:${d.jira_key}`;
                      const value = cls[id] ?? d.classification;
                      return (
                        <div className="dup-item" key={d.jira_key}>
                          {d.jira_url ? (
                            <a className="dup-key" href={d.jira_url} target="_blank" rel="noreferrer">
                              {shortKey(d.jira_key)}
                            </a>
                          ) : (
                            <span className="dup-key">{shortKey(d.jira_key)}</span>
                          )}
                          <span className="dup-title" title={d.title}>{d.title}</span>
                          <span className="dup-type">{contractTypeLabel(d.contract_type)}</span>
                          <span className="dup-rate">
                            {d.rate_amount == null ? "—" : d.rate_amount.toLocaleString("ru-RU")}
                          </span>
                          <span className="dup-pct">{d.match_percent}%</span>
                          <span className="cls">
                            <button
                              type="button"
                              className="cls-btn"
                              aria-haspopup="listbox"
                              aria-expanded={clsOpen === id}
                              onClick={() => setClsOpen(clsOpen === id ? null : id)}
                            >
                              {value === "duplicate" ? "Дубль" : "Похожая"}
                              <Chevron />
                            </button>
                            {clsOpen === id && (
                              <span className="cls-menu" role="listbox" aria-label="Классификация связи">
                                {(["similar", "duplicate"] as const).map((v) => (
                                  <button
                                    type="button"
                                    key={v}
                                    onClick={() => { classify(n.jira_key, d, v); setClsOpen(null); }}
                                  >
                                    <span className="mark">{value === v ? "✓" : ""}</span>
                                    {v === "duplicate" ? "Дубль" : "Похожая"}
                                  </button>
                                ))}
                              </span>
                            )}
                          </span>
                        </div>
                      );
                    })}
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
                      need={n}
                      onAssigned={() => {
                        onChanged?.();
                        setRefresh((x) => x + 1);
                      }}
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
      {rosNeed && <RosDialog need={rosNeed} onClose={() => setRosNeed(null)} />}
    </>
  );
}
