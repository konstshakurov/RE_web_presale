import { expect, test } from "@playwright/test";

import { DENIED, JIRA_RELOGIN_PW, PW, newPage, resetAllowList, uiLogin } from "./helpers";

// KON-58: E2E-приёмка входа и разделов. Требования: FR-024, FR-021, FR-031, FR-038, UC-006, FR-029.
// Пользователи и пароли - из QA-стенда (qa/stand), фейк Jira. Скриншоты без вводимых секретов.

test.beforeEach(async () => {
  await resetAllowList();
});

test("E2E-01 вход пользователя из списка: стартовая страница и три раздела без Настроек (FR-024, FR-031)", async ({ page }) => {
  await uiLogin(page, "qa-user");
  await expect(page).toHaveURL(/\/needs$/);
  await expect(page.getByRole("heading", { name: "Потребности" })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Разделы" });
  await expect(nav.getByRole("link")).toHaveText(["Потребности", "Сотрудники", "Резюме"]);
  await expect(nav.getByRole("link", { name: "Настройки" })).toHaveCount(0);
  await expect(page.locator(".topbar__user")).toHaveText("QA User"); // ФИО из LDAP (cn) без отчества
  await page.screenshot({ path: "artifacts/e2e-01-needs-user.png" });
});

test("E2E-02 верный LDAP, но не в списке: отказ тем же текстом, сессии нет (FR-021)", async ({ page }) => {
  await uiLogin(page, "qa-outsider");
  await expect(page.getByRole("alert")).toHaveText(DENIED);
  await expect(page).toHaveURL(/\/login$/);
  await page.screenshot({ path: "artifacts/e2e-02-denied.png" });
  await page.goto("/needs");
  await expect(page).toHaveURL(/\/login/); // защищённый маршрут не открылся
});

test("E2E-03 неверный пароль: тот же текст отказа (FR-024)", async ({ page }) => {
  await uiLogin(page, "qa-user", "FAKE_wrong_password_1");
  await expect(page.getByRole("alert")).toHaveText(DENIED);
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByLabel("Пароль")).toHaveValue(""); // пароль не остаётся в поле
});

test("E2E-04 админ добавляет и отзывает пользователя: отозванный выброшен на вход (FR-021, UC-006)", async ({ page, browser }) => {
  await uiLogin(page, "qa-admin");
  await page.getByRole("link", { name: "Настройки" }).click();
  await expect(page.getByRole("heading", { name: "Настройки" })).toBeVisible();

  // добавление qa-outsider через интерфейс
  await page.getByRole("button", { name: "Добавить", exact: true }).click();
  await page.getByRole("dialog").getByLabel("Логин").fill("qa-outsider");
  await page.getByRole("dialog").getByRole("button", { name: "Добавить" }).click();
  const row = page.getByRole("row", { name: /qa-outsider/ });
  await expect(row).toContainText("Допущен");

  // новый пользователь входит в своей сессии
  const victim = await newPage(browser);
  await uiLogin(victim.page, "qa-outsider");
  await expect(victim.page).toHaveURL(/\/needs$/);

  // админ отзывает доступ
  await row.getByRole("button", { name: "Отозвать" }).click();
  await page.getByRole("button", { name: "Подтвердить" }).click();
  await expect(row).toContainText("Отозван");
  await page.screenshot({ path: "artifacts/e2e-04-admin-revoked.png" });

  // следующее действие отозванного: переход на раздел -> на вход, без ожидания истечения сессии
  await victim.page.getByRole("link", { name: "Сотрудники" }).click();
  await expect(victim.page).toHaveURL(/\/login/);
  await expect(victim.page.getByRole("heading", { name: "Вход" })).toBeVisible();

  // повторный вход отозванного невозможен
  await uiLogin(victim.page, "qa-outsider");
  await expect(victim.page.getByRole("alert")).toHaveText(DENIED);
  await victim.ctx.close();
});

test("E2E-05 не-админ: нет Настроек в меню, по прямой ссылке - Нет доступа (FR-031)", async ({ page }) => {
  await uiLogin(page, "qa-user");
  await expect(page).toHaveURL(/\/needs$/);
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Нет доступа" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Настройки" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Настройки" })).toHaveCount(0);
  // сервер тоже отказывает, а не только интерфейс
  const r = await page.request.get("/api/admin/users");
  expect(r.status()).toBe(403);
  await page.screenshot({ path: "artifacts/e2e-05-forbidden.png" });
});

test("E2E-06 логотип в шапке и на странице входа (FR-038)", async ({ page }) => {
  await page.goto("/login");
  const logo = page.getByRole("img", { name: "RE" });
  await expect(logo).toBeVisible();
  expect(await logo.evaluate((el) => (el as HTMLImageElement).complete && (el as HTMLImageElement).naturalWidth > 0)).toBe(true);
  await page.screenshot({ path: "artifacts/e2e-06-login-logo.png" });
  await uiLogin(page, "qa-user");
  await expect(page).toHaveURL(/\/needs$/);
  const logo2 = page.locator("header .topbar__logo");
  await expect(logo2).toBeVisible();
  expect(await logo2.evaluate((el) => (el as HTMLImageElement).complete && (el as HTMLImageElement).naturalWidth > 0)).toBe(true);
});

test("E2E-07 админ видит четыре раздела, каждый открывается (FR-031)", async ({ page }) => {
  await uiLogin(page, "qa-admin");
  const nav = page.getByRole("navigation", { name: "Разделы" });
  await expect(nav.getByRole("link")).toHaveText(["Потребности", "Сотрудники", "Резюме", "Настройки"]);
  const sections: [string, RegExp][] = [
    ["Потребности", /\/needs$/],
    ["Сотрудники", /\/employees$/],
    ["Резюме", /\/resumes$/],
    ["Настройки", /\/settings$/],
  ];
  for (const [title, url] of sections) {
    await nav.getByRole("link", { name: title }).click();
    await expect(page).toHaveURL(url);
    await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();
  }
  await page.screenshot({ path: "artifacts/e2e-07-settings-admin.png" });
});

test("E2E-08 отказ Jira: баннер, повторный ввод пароля снимает его, разделы работают (FR-029)", async ({ page }) => {
  await uiLogin(page, "qa-jirablock");
  await expect(page).toHaveURL(/\/needs$/); // вход в приложение прошёл
  const banner = page.getByRole("region", { name: "Состояние Jira" });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("Работа с Jira недоступна");
  await page.screenshot({ path: "artifacts/e2e-08-jira-banner.png" });

  await page.getByRole("link", { name: "Сотрудники" }).click();
  await expect(page.getByRole("heading", { name: "Сотрудники", level: 1 })).toBeVisible();
  await expect(banner).toBeVisible(); // баннер сохраняется на разделах

  // неверный пароль Jira: баннер остаётся, поле очищено
  await banner.getByLabel("Пароль Jira").fill("FAKE_wrong_jira_pw");
  await banner.getByRole("button", { name: "Ввести пароль повторно" }).click();
  await expect(banner).toBeVisible();
  await expect(banner.getByLabel("Пароль Jira")).toHaveValue("");

  // верный: баннер исчезает
  await banner.getByLabel("Пароль Jira").fill(JIRA_RELOGIN_PW);
  await banner.getByRole("button", { name: "Ввести пароль повторно" }).click();
  await expect(page.getByRole("region", { name: "Состояние Jira" })).toHaveCount(0);
});

test("E2E-09 logout: на вход, защищённые страницы недоступны, сессия отозвана на сервере (FR-024)", async ({ page }) => {
  await uiLogin(page, "qa-user");
  await expect(page).toHaveURL(/\/needs$/);
  const cookies = await page.context().cookies();
  const session = cookies.find((c) => c.name === "re_session");
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Lax");
  await page.getByRole("button", { name: "Выйти" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/employees");
  await expect(page).toHaveURL(/\/login/);
  // повтор прежней cookie на сервере: 401
  const replay = await page.request.get("/api/auth/me", { headers: { Cookie: `re_session=${session!.value}` } });
  expect(replay.status()).toBe(401);
});

test("E2E-10 пароль не попадает в URL и Web Storage, поле типа password (FR-024, NFR-006)", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByLabel("Пароль")).toHaveAttribute("type", "password");
  await page.getByLabel("Логин").fill("qa-user");
  await page.getByLabel("Пароль").fill(PW["qa-user"]);
  const sent = page.waitForRequest((r) => r.url().includes("/api/auth/login"));
  await page.getByRole("button", { name: "Войти" }).click();
  const req = await sent;
  expect(req.method()).toBe("POST");
  expect(req.url()).not.toContain(PW["qa-user"]);
  await expect(page).toHaveURL(/\/needs$/);
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(stored).not.toContain(PW["qa-user"]);
});
