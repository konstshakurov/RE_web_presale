import type { ReactNode } from "react";

// Иконка-подсказка «i» (UX-1 п. 5): контурный SVG 18px, цель клика 24x24, не переносится от слова.
type Props = { label: string; onClick?: () => void }; // без onClick - только подсказка (aria-label/title)

export function HintButton({ label, onClick }: Props) {
  return (
    <button type="button" className="hint-btn" aria-label={label} title={label} onClick={onClick}>
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v5.5" />
        <path d="M12 7.6v.01" />
      </svg>
    </button>
  );
}

// Слово + «i» в одной неразрывной строке.
export function HintLabel({ children }: { children: ReactNode }) {
  return <span className="hint-label">{children}</span>;
}
