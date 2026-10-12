import { APIRequestContext, Browser, BrowserContext, Page, expect, request } from "@playwright/test";

import { BASE_URL, ENV } from "../playwright.config";

export const PW: Record<string, string> = {
  "qa-admin": ENV.QA_LDAP_PW_ADMIN,
  "qa-user": ENV.QA_LDAP_PW_USER,
  "qa-outsider": ENV.QA_LDAP_PW_OUTSIDER,
  "qa-revoked": ENV.QA_LDAP_PW_REVOKED,
  "qa-jirablock": ENV.QA_LDAP_PW_JIRABLOCK,
};
export const JIRA_RELOGIN_PW = ENV.QA_JIRA_RELOGIN_PW;
export const DENIED = "Неверный логин или пароль, либо нет доступа";

/** Вход через форму интерфейса. */
export async function uiLogin(page: Page, login: string, password: string = PW[login]) {
  await page.goto("/login");
  await page.getByLabel("Логин").fill(login);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти" }).click();
}

export async function newPage(browser: Browser): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext({ baseURL: BASE_URL, locale: "ru-RU" });
  return { ctx, page: await ctx.newPage() };
}

type AdminApi = {
  api: APIRequestContext;
  patch: (login: string, body: object) => Promise<number>;
  add: (login: string) => Promise<number>;
};

/** API-клиент админа: приводит стенд к исходному состоянию перед сценарием. */
export async function adminApi(): Promise<AdminApi> {
  const api = await request.newContext({ baseURL: BASE_URL + "/api/" });
  const r = await api.post("auth/login", { data: { login: "qa-admin", password: PW["qa-admin"] } });
  expect(r.status()).toBe(200);
  const csrf = async () => ({ "X-CSRF-Token": (await (await api.get("auth/me")).json()).csrf_token });
  return {
    api,
    patch: async (login, body) =>
      (await api.patch(`admin/users/${login}`, { data: body, headers: await csrf() })).status(),
    add: async (login) =>
      (await api.post("admin/users", { data: { login }, headers: await csrf() })).status(),
  };
}

/** Исходное состояние: qa-admin - админ; qa-user, qa-revoked, qa-jirablock допущены; qa-outsider - нет. */
export async function resetAllowList() {
  const a = await adminApi();
  for (const u of ["qa-user", "qa-revoked", "qa-jirablock"]) {
    await a.add(u);
    await a.patch(u, { active: true, role: "user" });
  }
  const list = await (await a.api.get("admin/users")).json();
  if (list.some((u: { login: string; active: boolean }) => u.login === "qa-outsider" && u.active)) {
    await a.patch("qa-outsider", { active: false });
  }
  await a.api.dispose();
}
