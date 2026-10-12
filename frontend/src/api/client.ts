import type {
  AdminUser,
  EpicOut,
  GroupOut,
  LoginRequest,
  MeResponse,
  NeedGroups,
  NeedsPage,
  NeedsQuery,
  RefreshResult,
  StatusGroupsData,
  SyncErrorBody,
  SyncScope,
} from "./types";

// nginx проксирует /api/ на api; сессия — HttpOnly cookie, CSRF — заголовок на изменяющих запросах.
export const API_BASE = "/api";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail: SyncErrorBody | null = null, // разобранный detail отказа /sync (state, code, message)
  ) {
    super(message);
  }
}

let csrfToken = "";
let onUnauthorized: (() => void) | null = null;

export function setCsrfToken(token: string) {
  csrfToken = token;
}

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

function isSyncError(detail: unknown): detail is SyncErrorBody {
  return typeof detail === "object" && detail !== null && typeof (detail as SyncErrorBody).state === "string";
}

function errorText(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (isSyncError(detail) && detail.message) return detail.message; // текст отказа Jira как есть
  if (status === 422) return "Проверьте введённые данные";
  if (status >= 500) return "Сервис временно недоступен, повторите позже";
  return `Ошибка запроса (${status})`;
}

export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: { redirectOn401?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (!SAFE_METHODS.has(method.toUpperCase()) && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let res: Response;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Нет связи с сервером, повторите позже");
  }

  if (res.status === 401 && opts.redirectOn401 !== false) onUnauthorized?.();
  if (!res.ok) {
    let data: unknown = null;
    try {
      data = await res.json();
    } catch {
      /* тело не JSON */
    }
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new ApiError(res.status, errorText(res.status, data), isSyncError(detail) ? detail : null);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  me: () => request<MeResponse>("GET", "/auth/me", undefined, { redirectOn401: false }),
  // 401 при входе — это отказ с текстом ошибки, а не потеря сессии
  login: (data: LoginRequest) =>
    request<MeResponse>("POST", "/auth/login", data, { redirectOn401: false }),
  logout: () => request<void>("POST", "/auth/logout"),
  // Пароль живёт только в аргументе и теле запроса; отказ Jira приходит как jira_status, не как ошибка
  jiraRelogin: (password: string) => request<MeResponse>("POST", "/auth/jira-relogin", { password }),
  adminUsers: () => request<AdminUser[]>("GET", "/admin/users"),
  addUser: (login: string) => request<AdminUser>("POST", "/admin/users", { login }),
  patchUser: (login: string, body: { active?: boolean; role?: "user" | "admin" }) =>
    request<AdminUser>("PATCH", `/admin/users/${encodeURIComponent(login)}`, body),
  statusGroups: () => request<StatusGroupsData>("GET", "/admin/status-groups"),
  createGroup: (name: string) => request<GroupOut>("POST", "/admin/status-groups", { name }),
  renameGroup: (id: number, name: string) =>
    request<GroupOut>("PATCH", `/admin/status-groups/${id}`, { name }),
  deleteGroup: (id: number) => request<void>("DELETE", `/admin/status-groups/${id}`),
  addGroupStatus: (id: number, status: string) =>
    request<GroupOut>("POST", `/admin/status-groups/${id}/statuses`, { status }),
  removeGroupStatus: (id: number, status: string) =>
    request<GroupOut>("DELETE", `/admin/status-groups/${id}/statuses?status=${encodeURIComponent(status)}`),
  orderGroups: (ids: number[]) => request<GroupOut[]>("PUT", "/admin/status-groups/order", { ids }),
  needs: (q: NeedsQuery) => {
    const p = new URLSearchParams();
    p.set("group", q.group);
    if (q.q) p.set("q", q.q);
    p.set("limit", String(Math.min(q.limit, 500)));
    p.set("offset", String(q.offset));
    return request<NeedsPage>("GET", `/sync/needs?${p}`);
  },
  needGroups: () => request<NeedGroups>("GET", "/sync/needs/groups"),
  refresh: (scope: SyncScope) => request<RefreshResult>("POST", "/sync/refresh", { scope }),
  epic: (key: string) => request<EpicOut>("GET", `/sync/epics/${encodeURIComponent(key)}`),
};
