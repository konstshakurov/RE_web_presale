import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

// Стек открытых окон: Esc закрывает только верхнее.
const stack: symbol[] = [];

interface Props {
  title: string;
  children: ReactNode;
  wide?: boolean; // широкая модалка: шапка и футер закреплены, прокручивается только тело (UX-1 п. 9)
  size?: "profile" | "note"; // 720px (п. 8) / 560px (окно заметки) вместо 960px
  meta?: ReactNode; // строка под заголовком в шапке (статус «Профиля»)
  footer?: ReactNode; // футер широкой модалки
  onClose?: () => void; // Esc (и × в широкой модалке)
  onBackdropClick?: () => void; // щелчок по подложке (по умолчанию не закрывает)
  closeLabel?: string; // aria-label кнопки ×
  closeDisabled?: boolean; // × недоступен (идёт сохранение)
}

// Модальное окно. Узкое: заголовок и содержимое, структура прежняя, Esc закрывает при onClose.
// Широкое (wide): слоты шапка / тело / футер, × и Esc.
export default function Modal({ title, children, wide, size, meta, footer, onClose, onBackdropClick, closeLabel, closeDisabled }: Props) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const id = Symbol("modal");
    stack.push(id);
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== "Escape" || stack[stack.length - 1] !== id) return;
      ev.preventDefault();
      closeRef.current?.();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      const i = stack.indexOf(id);
      if (i >= 0) stack.splice(i, 1);
    };
  }, []);

  if (!wide) {
    return (
      <div className="modal-backdrop">
        <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
          <h2>{title}</h2>
          {children}
        </div>
      </div>
    );
  }
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(ev) => {
        if (ev.target === ev.currentTarget) onBackdropClick?.();
      }}
    >
      <div
        className={size ? `modal modal--wide modal--${size}` : "modal modal--wide"}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal__head">
          <div className="modal__title">
            <h2>{title}</h2>
            {meta && <div className="modal__meta">{meta}</div>}
          </div>
          {onClose && (
            <button type="button" className="modal__x" aria-label={closeLabel ?? "Закрыть окно"} title="Закрыть" disabled={closeDisabled} onClick={onClose}>
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          )}
        </div>
        <div className="modal__body" tabIndex={0}>
          {children}
        </div>
        {footer && <div className="modal__foot">{footer}</div>}
      </div>
    </div>
  );
}
