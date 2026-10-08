import type { LoginRequest, MeResponse } from "./types";

// nginx проксирует /api/ на api; сессия — HttpOnly cookie, CSRF — заголовок на изменяющих запросах.
export const API_BASE = "/api";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
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

function errorText(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) return detail;
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
    throw new ApiError(res.status, errorText(res.status, data));
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
};
