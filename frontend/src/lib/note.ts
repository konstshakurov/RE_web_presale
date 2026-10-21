export const NOTE_MAX = 2000; // как NOTE_MAX на сервере (backend/app/cv/router.py), KON-94
export const NOTE_WARN = 1900;

// Длина как на сервере: pydantic считает кодпоинты, а не UTF-16 единицы (эмодзи = 1 знак)
export function noteLength(s: string): number {
  return Array.from(s).length;
}

// «знак / знака / знаков»
export function pluralSign(n: number): string {
  const m100 = n % 100;
  const m10 = n % 10;
  if (m100 >= 11 && m100 <= 14) return "знаков";
  if (m10 === 1) return "знак";
  if (m10 >= 2 && m10 <= 4) return "знака";
  return "знаков";
}

const stamp = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

// «дд.мм.гг, чч:мм»
export function formatNoteStamp(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : stamp.format(d);
}
