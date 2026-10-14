import { useId, useState } from "react";
import type { KeyboardEvent } from "react";

import type { Technology } from "../../api/types";
import { HintButton } from "../HintButton";
import Switch from "../Switch";

const MATCH_HINT = "Включено — резюме содержит все выбранные технологии; выключено — любую из них";

interface Props {
  value: string[];
  onChange: (v: string[]) => void;
  suggestions: Technology[];
  match: "all" | "any";
  onMatch: (m: "all" | "any") => void;
}

// Фильтр «Технологии» (FR-032, UX-1 п. 6: чипы и переключатель «Все выбранные» в одной строке): выбор нескольких, подсказки из технологий Основных CV (GET /resume/technologies).
export default function TechnologyPicker({ value, onChange, suggestions, match, onMatch }: Props) {
  const [text, setText] = useState("");
  const listId = useId();
  const inputId = useId();

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
    <div className="field techpicker">
      <label htmlFor={inputId}>Технологии</label>
      <input
          id={inputId}
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
      {value.length > 0 && (
        <div className="tsub">
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
          {value.length > 1 && (
            <span className="match">
              <Switch
                label="Все выбранные"
                checked={match === "all"}
                onChange={(e) => onMatch(e.target.checked ? "all" : "any")}
              />
              <HintButton label={MATCH_HINT} />
            </span>
          )}
        </div>
      )}
    </div>
  );
}
