import type { ReactNode } from "react";

// Простое модальное окно: затемнение, заголовок, содержимое. Закрытие - кнопкой в содержимом.
export default function Modal({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="modal-backdrop">
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}
