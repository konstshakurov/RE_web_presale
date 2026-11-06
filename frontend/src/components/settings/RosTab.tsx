import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ApiError, api } from "../../api/client";
import type { RosSettings } from "../../api/types";

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Не удалось выполнить действие";
}

// Вкладка «ROS» в Настройках (FR-041, decision.md KON-161 п. 1.4): 7 параметров модели,
// редактирует любой админ, значения общие для всех; эталон = ROS_model_v2 (спека FR-041).
// Подписи полей — решение владельца 06.11: коротко, не переносятся на вторую строку.
const FIELDS: { key: keyof RosSettings; label: string; min: number; max: number }[] = [
  { key: "billable_hours", label: "Billable ч/мес", min: 1, max: 744 },
  { key: "indirect_cost", label: "Косвенные расходы, руб/мес", min: 0, max: 10_000_000 },
  { key: "profit_tax_rate", label: "Налог на прибыль, %", min: 0, max: 100 },
  { key: "vat_rate", label: "Ставка НДС, %", min: 0, max: 100 },
  { key: "insurance_before", label: "Страх. взносы до лимита, %", min: 0, max: 100 },
  { key: "insurance_after", label: "Страх. взносы после лимита, %", min: 0, max: 100 },
  { key: "fot_limit", label: "Лимит ФОТ, руб", min: 1, max: 1_000_000_000 },
];

export default function RosTab() {
  const [values, setValues] = useState<RosSettings | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    try {
      setValues(await api.rosSettings());
      setError("");
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (ev: FormEvent) => {
    ev.preventDefault();
    if (!values) return;
    const bad = FIELDS.find((f) => !(Number(values[f.key]) >= f.min && Number(values[f.key]) <= f.max));
    if (bad) {
      setError(`Проверьте «${bad.label}»: число от ${bad.min} до ${bad.max}`);
      return;
    }
    setSaving(true);
    try {
      const saved = await api.saveRosSettings(values);
      setValues(saved);
      setNote("Сохранено: значения общие для всех пользователей, расчёт ROS использует их сразу.");
      setError("");
    } catch (e) {
      setNote("");
      setError(errText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="llm-settings">
      <form className="group-card" onSubmit={save}>
        <div className="group-card__head">
          <h3>Модель ROS</h3>
          <span className="group-card__spacer" />
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {!values && !error && <p className="muted">Загрузка…</p>}
        {values && (
          <>
            <div className="ros-grid">
              {FIELDS.map((f) => (
                <label key={f.key} className="field">
                  {f.label}
                  <input
                    type="number"
                    required
                    min={f.min}
                    max={f.max}
                    step="any"
                    value={String(values[f.key] ?? "")}
                    onChange={(e) =>
                      setValues((v) => (v ? { ...v, [f.key]: Number(e.target.value) } : v))
                    }
                  />
                </label>
              ))}
              <div className="modal__actions">
                <button type="submit" className="primary" disabled={saving}>
                  Сохранить
                </button>
              </div>
              {note && <p className="muted">{note}</p>}
            </div>
          </>
        )}
      </form>
    </div>
  );
}
