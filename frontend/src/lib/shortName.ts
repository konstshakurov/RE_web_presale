// Имя в шапке: «Имя Фамилия» без отчества. В БД и API хранится полное cn.
export function shortName(displayName: string | null | undefined, login: string): string {
  const words = (displayName ?? "").trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return login;
  return words.slice(0, 2).join(" ");
}
