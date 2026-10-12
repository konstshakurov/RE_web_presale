import { useState } from "react";

import AccessTab from "../components/settings/AccessTab";
import StatusGroupsTab from "../components/settings/StatusGroupsTab";

// Вкладки «Настроек» (FR-031). Реализованы «Доступ» (Dev-21) и «Группы статусов» (Dev-22);
// остальные - заглушки до своих задач.
const TABS = [
  { key: "access", title: "Доступ" },
  { key: "groups", title: "Группы статусов" },
  { key: "llm", title: "LLM", stub: true },
  { key: "jql", title: "JQL", stub: true },
  { key: "vat", title: "НДС", stub: true },
  { key: "files", title: "Лимит файла", stub: true },
] as const;

export default function SettingsPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("access");
  return (
    <section>
      <h1>Настройки</h1>
      <div className="tabs" role="tablist" aria-label="Настройки">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={t.key === tab}
            className={t.key === tab ? "tabs__item tabs__item--active" : "tabs__item"}
            onClick={() => setTab(t.key)}
          >
            {t.title}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "access" && <AccessTab />}
        {tab === "groups" && <StatusGroupsTab />}
        {TABS.some((t) => t.key === tab && "stub" in t) && (
          <p className="muted">Раздел в разработке: в Dev-10 и далее.</p>
        )}
      </div>
    </section>
  );
}
