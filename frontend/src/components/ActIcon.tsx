// Контурные иконки действий `.act` (UX-1 п. 3): 18x18, stroke currentColor 1.5.
const PATHS = {
  user: ["M20 21v-1a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v1", "M16 7a4 4 0 1 1-8 0a4 4 0 0 1 8 0z"],
  doc: ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z", "M14 3v5h5", "M9 13h6M9 17h6"],
  plus: ["M12 5v14M5 12h14"],
  pencil: ["M12 20h9", "M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z"],
  folder: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
} as const;

export default function ActIcon({ name, size = 18 }: { name: keyof typeof PATHS; size?: number }) {
  return (
    <svg className="ico" viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
