import type { DeptHead } from "../api/types";
import type { DeptHeadsState } from "../lib/useDeptHeads";
import { NO_DEPT_HEAD, NO_DEPT_HEAD_LABEL, deptHeadLabel, sortDeptHeads } from "../lib/employeeFilters";

interface SelectProps {
  value: string;
  onChange: (value: string) => void;
  list: DeptHead[];
  state: DeptHeadsState;
  id?: string;
  field?: boolean; // разметка панели «Резюме» (.field), иначе «Сотрудники» (.fld)
}

interface HintProps {
  list: DeptHead[];
  state: DeptHeadsState;
  onRetry: () => void;
  id?: string;
}

// Поле «РО» (design/KON-134): «Все», имена по алфавиту, «Без РО» последним.
// Подсказка под панелью (загрузка / ошибка / нет РО) - <DeptHeadHint> с тем же id.
export default function DeptHeadSelect({ value, onChange, list, state, id = "dept-head", field = false }: SelectProps) {
  const none = state === "ready" && list.length === 0;
  const disabled = state !== "ready" || none;
  const stub = state === "loading" ? "Загрузка…" : state === "error" ? "Список недоступен" : "Нет данных";
  const Wrap = field ? "div" : "label";
  return (
    <Wrap className={field ? "field" : "fld"}>
      {field ? <label htmlFor={id}>РО</label> : <span>РО</span>}
      <select
        id={id}
        value={disabled ? "" : value}
        disabled={disabled}
        aria-describedby={`${id}-hint`}
        title={disabled ? stub : deptHeadLabel(value, list)}
        onChange={(e) => onChange(e.target.value)}
      >
        {disabled ? (
          <option value="">{stub}</option>
        ) : (
          <>
            <option value="">Все</option>
            {sortDeptHeads(list).map((d) => (
              <option key={d.login} value={d.login}>
                {d.display_name}
              </option>
            ))}
            <option value={NO_DEPT_HEAD}>{NO_DEPT_HEAD_LABEL}</option>
          </>
        )}
      </select>
    </Wrap>
  );
}

export function DeptHeadHint({ state, list, onRetry, id = "dept-head" }: HintProps) {
  const hid = `${id}-hint`;
  if (state === "error") {
    return (
      <p className="rohint err" id={hid} role="alert">
        Не удалось загрузить список РО. Остальные фильтры работают.{" "}
        <button type="button" className="linkbtn link" onClick={onRetry}>
          Повторить
        </button>
      </p>
    );
  }
  if (state === "loading") {
    return (
      <p className="rohint" id={hid} role="status">
        <span className="spinner" aria-hidden="true" /> Загружаем список РО…
      </p>
    );
  }
  if (list.length === 0) {
    return (
      <p className="rohint" id={hid}>
        В карточках сотрудников не указан руководитель отдела — фильтр недоступен.
      </p>
    );
  }
  return <span id={hid} />;
}
