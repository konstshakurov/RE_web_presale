import { useId, useState } from "react";
import type { KeyboardEvent } from "react";

import type { Technology } from "../../api/types";

interface Props {
  value: string[];
  onChange: (v: string[]) => void;
  suggestions: Technology[];
  match: "all" | "any";
  onMatch: (m: "all" | "any") => void;
}

// Фильтр «Технология» (FR-032): выбор нескольких, подсказки из технологий Основных CV (GET /resume/technologies).
export default function TechnologyPicker({ value, onChange, suggestions, match, onMatch }: Props) {
  const [text, setText] = useState("");
  const listId = useId();

  function add(raw: string) {
    const t = raw.trim().toLowerCase();
    if (t && !value.includes(t)) onChange([...value, t]);
    setText("");
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add(text);
    }
  }

  return (
    <div className="techpicker">
      <label>
        Технология (можно несколько)
        <input
          type="text"
          list={listId}
          value={text}
          placeholder="Начните вводить, Enter — добавить"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          onBlur={() => add(text)}
        />
        <datalist id={listId}>
          {suggestions
            .filter((s) => !value.includes(s.tag))
            .map((s) => (
              <option key={s.tag} value={s.tag}>
                {s.employees}
              </option>
            ))}
        </datalist>
      </label>
      {value.length > 0 && (
        <ul className="chips" aria-label="Выбранные технологии">
          {value.map((t) => (
            <li key={t} className="chip">
              {t}
              <button type="button" aria-label={`Убрать ${t}`} onClick={() => onChange(value.filter((x) => x !== t))}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {value.length > 1 && (
        <label>
          Совпадение
          <select value={match} onChange={(e) => onMatch(e.target.value === "any" ? "any" : "all")}>
            <option value="all">Все выбранные</option>
            <option value="any">Любая из выбранных</option>
          </select>
        </label>
      )}
    </div>
  );
}
