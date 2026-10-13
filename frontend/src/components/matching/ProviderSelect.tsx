import type { ProviderPublic } from "../../api/types";

interface Props {
  providers: ProviderPublic[];
  value: number | null;
  onChange: (id: number | null) => void;
  disabled?: boolean;
}

// Выбор провайдера/модели для расчёта; одного провайдера выбирать не из чего - список не показываем.
export default function ProviderSelect({ providers, value, onChange, disabled }: Props) {
  if (providers.length < 2) return null;
  return (
    <label className="provider-select">
      <select
        aria-label="Модель"
        value={value === null ? "" : String(value)}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      >
        <option value="">LLM по умолчанию</option>
        {providers.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
            {p.is_external ? " (внешняя, с обезличиванием)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
