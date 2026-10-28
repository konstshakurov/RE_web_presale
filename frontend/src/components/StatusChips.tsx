import type { StatusOption } from "../lib/employees";
import { toggleStatus } from "../lib/employeeFilters";

interface Props {
  options: StatusOption[];
  selected: string[];
  total: number; // число в «Все N»
  onChange: (selected: string[]) => void;
}

// Чипы статусов с мультивыбором (design/KON-134): «Все» нажат, когда ничего не выбрано; галочка у выбранных - в CSS.
export default function StatusChips({ options, selected, total, onChange }: Props) {
  return (
    <nav className="filter filter--multi" aria-label="Статусы сотрудников (можно выбрать несколько)">
      <button type="button" className="filter__item" aria-pressed={selected.length === 0} onClick={() => onChange([])}>
        Все <span className="filter__count">{total}</span>
      </button>
      {options.map((o) => (
        <button
          key={o.status}
          type="button"
          className="filter__item"
          aria-pressed={selected.includes(o.status)}
          onClick={() => onChange(toggleStatus(selected, o.status))}
        >
          {o.status} <span className="filter__count">{o.count}</span>
        </button>
      ))}
    </nav>
  );
}
