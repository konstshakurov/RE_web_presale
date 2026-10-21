import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { EmployeeOut, NoteOut } from "../../api/types";
import { NOTE_MAX, NOTE_WARN, formatNoteStamp, noteLength, pluralSign } from "../../lib/note";
import Modal from "../Modal";

interface Props {
  emp: EmployeeOut;
  onClose: () => void;
  onSaved: (r: NoteOut) => void;
}

const FOCUSABLE = "button:not(:disabled), textarea, [href], [tabindex]:not([tabindex='-1'])";

// Редактор заметки сотрудника (FR-033, KON-115 вариант B): модальное окно, автор и дата - только здесь.
export default function NoteDialog({ emp, onClose, onSaved }: Props) {
  const saved = emp.note ?? "";
  const [text, setText] = useState(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);

  const len = noteLength(text);
  const over = len > NOTE_MAX;
  const changed = text !== saved;
  const clearing = saved !== "" && text === "";
  const canSave = changed && !over && !busy;

  useEffect(() => {
    const a = area.current;
    if (!a) return;
    a.focus();
    a.setSelectionRange(a.value.length, a.value.length);
  }, []);

  async function save() {
    if (!canSave) return;
    setBusy(true);
    setError("");
    try {
      onSaved(await api.saveNote(emp.jira_key, text));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось сохранить заметку");
      setBusy(false);
      setTimeout(() => area.current?.focus(), 0); // поле снова доступно после снятия readOnly
    }
  }

  function cancel() {
    if (!busy) onClose();
  }

  const latest = useRef({ save, canSave });
  latest.current = { save, canSave };

  // Ctrl/Cmd+Enter - сохранить; Tab - по кругу внутри окна
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault();
        if (latest.current.canSave) void latest.current.save();
        return;
      }
      if (ev.key !== "Tab") return;
      const dlg = document.querySelector<HTMLElement>('[role="dialog"]');
      if (!dlg) return;
      const items = Array.from(dlg.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const cur = document.activeElement;
      if (ev.shiftKey && (cur === first || !dlg.contains(cur))) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && (cur === last || !dlg.contains(cur))) {
        ev.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const stamp = formatNoteStamp(emp.note_updated_at);
  const meta = saved ? `Изменена: ${[emp.note_author, stamp].filter(Boolean).join(", ")}` : "Заметки пока нет";

  const footer = (
    <>
      <button
        type="button"
        className="note-dlg__clear"
        disabled={busy || text === ""}
        onClick={() => {
          setText("");
          setError("");
          area.current?.focus();
        }}
      >
        Очистить
      </button>
      <span className="modal__acts">
        {busy && (
          <span className="note-dlg__st">
            <span className="spinner" aria-hidden="true" /> Сохраняем…
          </span>
        )}
        <button type="button" onClick={cancel} disabled={busy}>
          Отмена
        </button>
        <button
          type="button"
          className={clearing ? "note-dlg__danger" : "primary"}
          disabled={!canSave}
          onClick={() => void save()}
        >
          {clearing ? "Очистить заметку" : "Сохранить"}
        </button>
      </span>
    </>
  );

  return (
    <Modal
      title={`Заметка: ${emp.full_name}`}
      wide
      size="note"
      meta={meta}
      footer={footer}
      closeLabel="Закрыть"
      closeDisabled={busy}
      onClose={cancel}
      onBackdropClick={() => {
        if (!busy && !changed) onClose();
      }}
    >
      <div className="note-dlg">
        <label htmlFor="note-text">Заметка</label>
        <textarea
          id="note-text"
          ref={area}
          rows={10}
          value={text}
          readOnly={busy}
          className={over ? "is-over" : undefined}
          placeholder="Например: ограничения по проектам или командировкам"
          aria-describedby="note-hint note-cnt"
          onChange={(e) => setText(e.target.value)}
        />
        <div className="note-dlg__row">
          <span id="note-hint" className={over ? "note-dlg__over" : "hint"}>
            {over
              ? `Сократите текст на ${len - NOTE_MAX} ${pluralSign(len - NOTE_MAX)}`
              : "Учитывается при сопоставлении с потребностью"}
          </span>
          <span
            id="note-cnt"
            aria-live="polite"
            className={over ? "note-dlg__cnt is-over" : len >= NOTE_WARN ? "note-dlg__cnt is-warn" : "note-dlg__cnt"}
          >
            {len} / {NOTE_MAX}
          </span>
        </div>
        {error && (
          <p className="error note-dlg__err" role="alert">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
