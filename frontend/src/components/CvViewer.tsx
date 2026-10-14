import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import type { CvText } from "../api/types";
import { findMatches, textSegments } from "../lib/employees";
import Modal from "./Modal";

interface Props {
  employeeKey: string;
  fileId?: number; // нет - актуальное основное CV сотрудника
  onClose: () => void;
}

// Просмотр CV в окне (FR-037): извлечённый текст, поиск с переходом между совпадениями, скачивание исходника.
// Подсветка технологий из потребности (предложение FR-037) - заглушка до сопоставления (KON-41).
export default function CvViewer({ employeeKey, fileId, onClose }: Props) {
  const [data, setData] = useState<CvText | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const activeRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    let alive = true;
    const req = fileId !== undefined ? api.fileText(fileId) : api.mainCv(employeeKey);
    req
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "Не удалось загрузить резюме"));
    return () => {
      alive = false;
    };
  }, [employeeKey, fileId]);

  const text = data?.text ?? null;
  const total = useMemo(() => (text ? findMatches(text, query).length : 0), [text, query]);
  const segments = useMemo(() => (text ? textSegments(text, query) : []), [text, query]);

  useEffect(() => {
    activeRef.current?.scrollIntoView?.({ block: "center" });
  }, [active, query]);

  function onQuery(v: string) {
    setQuery(v);
    setActive(0);
  }
  const step = (d: number) => {
    if (total > 0) setActive((active + d + total) % total);
  };

  const file = data?.file ?? null;
  const title = file ? `${employeeKey}  ${file.file_name}` : `${employeeKey}  Резюме`;
  const noText = data !== null && text === null;
  const failed = data?.extraction_status === "error" || file?.extraction_status === "error";

  return (
    <Modal
      title={title}
      wide
      onClose={onClose}
      footer={
        <>
          {file ? (
            <a className="button" href={api.downloadUrl(file.id)} download>
              Скачать исходный файл
            </a>
          ) : (
            <span />
          )}
          <button type="button" className="primary" onClick={onClose}>
            Закрыть
          </button>
        </>
      }
    >
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!data && !error && <p className="muted">Загрузка…</p>}
      {noText && <p className={failed ? "error" : "muted"}>{data?.message || "Текст недоступен"}</p>}
      {text !== null && (
        <>
          <div className="cv-search">
            <input
              type="search"
              aria-label="Поиск в резюме"
              placeholder="Поиск в резюме…"
              value={query}
              onChange={(e) => onQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") step(e.shiftKey ? -1 : 1);
              }}
            />
            <span className="muted" aria-live="polite">
              {query ? (total > 0 ? `${active + 1} из ${total}` : "не найдено") : ""}
            </span>
            <button type="button" aria-label="Предыдущее совпадение" disabled={total === 0} onClick={() => step(-1)}>
              ↑
            </button>
            <button type="button" aria-label="Следующее совпадение" disabled={total === 0} onClick={() => step(1)}>
              ↓
            </button>
            <button type="button" aria-label="Очистить поиск" disabled={!query} onClick={() => onQuery("")}>
              ×
            </button>
          </div>
          <pre className="cv-text">
            {segments.map((s, i) =>
              s.hit === null ? (
                <span key={i}>{s.text}</span>
              ) : (
                <mark
                  key={i}
                  className={s.hit === active ? "hit hit--active" : "hit"}
                  ref={
                    s.hit === active
                      ? (el) => {
                          activeRef.current = el;
                        }
                      : undefined
                  }
                >
                  {s.text}
                </mark>
              ),
            )}
          </pre>
        </>
      )}
    </Modal>
  );
}
