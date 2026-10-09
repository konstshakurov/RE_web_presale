import { Fragment, useState } from "react";

import type { NeedOut } from "../api/types";
import { formatRate } from "../lib/needs";
import ClientRules from "./ClientRules";

export default function NeedsTable({ items }: { items: NeedOut[] }) {
  const [open, setOpen] = useState<string | null>(null);
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
              </tr>
              {expanded && n.epic_key && (
                <tr className="needs__details">
                  <td colSpan={6}>
                    <ClientRules epicKey={n.epic_key} />
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
