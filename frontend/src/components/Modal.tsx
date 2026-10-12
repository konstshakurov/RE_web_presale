import type { ReactNode } from "react";

// Простое модальное окно: затемнение, заголовок, содержимое. Закрытие - кнопкой в содержимом.
export default function Modal({ title, children, wide }: { title: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className="modal-backdrop">
      <div className={wide ? "modal modal--wide" : "modal"} role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}
