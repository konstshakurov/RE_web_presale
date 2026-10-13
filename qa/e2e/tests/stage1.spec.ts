import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { APIRequestContext, Page, expect, request, test } from "@playwright/test";

import { BASE_URL, ENV } from "../playwright.config";
import { PW, adminApi, resetAllowList, uiLogin } from "./helpers";

// KON-47 (Pre.QA-2): e2e этапа 1 - файлы CV, список потребностей и группы, сопоставление, предпросмотр
// внешней LLM. Требования: FR-022, FR-027, FR-039, FR-035, FR-005, FR-036, FR-023, FR-001.
// Стенд: bash qa/stand/stand.sh up (фейк Jira и фейк LLM); файлы: python qa/testdata/make_cv_files.py.

const CV = (name: string) => resolve(__dirname, "..", "..", "testdata", "cv", name);
const JIRA = ENV.QA_FAKE_JIRA_URL || "http://127.0.0.1:18090";
const LLM = ENV.QA_FAKE_LLM_URL || "http://127.0.0.1:18091";
const DB = "re_web_presale_qa-db-1";
const EMP_NAME = "Громов Артём Викторович"; // ANALYTICS-5007, статус «Скоро освобождается»
const EMP_KEY = "ANALYTICS-5007";
const CANDIDATES: Record<string, string> = { // дополнительные сотрудники с основным CV для сопоставления
  "ANALYTICS-5001": "cv_main_v2.docx",
  "ANALYTICS-5003": "cv_main_v3.docx",
  "ANALYTICS-5004": "cv_extra.docx",
};
const NEED = "ANALYTICS-1001";
const EXT = "QA-ext-e2e";

function sql(db: string, q: string) {
  execSync(`docker exec ${DB} psql -U postgres -d ${db} -v ON_ERROR_STOP=1 -q -c "${q}"`, { stdio: "pipe" });
}

async function csrf(api: APIRequestContext) {
  return { "X-CSRF-Token": (await (await api.get("auth/me")).json()).csrf_token };
}

async function waitSynced(login: string) {
  const api = await request.newContext({ baseURL: BASE_URL + "/api/" });
  expect((await api.post("auth/login", { data: { login, password: PW[login] } })).status()).toBe(200);
  for (const scope of ["employees", "needs"]) {
    await expect
      .poll(async () => (await api.post("sync/refresh", { data: { scope }, headers: await csrf(api) })).status(), {
        timeout: 60_000,
      })
      .toBe(200);
  }
  return api;
}

async function cleanup() {
  // файлы и прогоны тестового сотрудника: основное CV защищено триггером, поэтому временно отключаем
  try {
    sql("cv_db", "ALTER TABLE cv.employee_file DISABLE TRIGGER USER");
    const keys = [EMP_KEY, ...Object.keys(CANDIDATES)].map((k) => `'${k}'`).join(",");
    sql("cv_db", `DELETE FROM cv.employee_file WHERE employee_key IN (${keys})`);
    sql("cv_db", "DELETE FROM cv.match_run");
  } finally {
    sql("cv_db", "ALTER TABLE cv.employee_file ENABLE TRIGGER USER");
  }
  sql("app_db", `DELETE FROM app.llm_task WHERE owner_login LIKE 'qa-%'`);
  sql("app_db", `DELETE FROM app.secret_store WHERE kind='llm_key' AND provider_id IN (SELECT id FROM app.llm_provider WHERE name='${EXT}')`);
  sql("app_db", `DELETE FROM app.llm_provider WHERE name='${EXT}' AND NOT is_default`);
}

async function openResumeRow(page: Page) {
  await page.getByRole("navigation", { name: "Разделы" }).getByRole("link", { name: "Резюме" }).click();
  await expect(page.getByRole("heading", { name: "Резюме" })).toBeVisible();
  const row = page.getByRole("row").filter({ hasText: EMP_KEY });
  await expect(row).toBeVisible();
  return row;
}

test.beforeAll(async () => {
  await resetAllowList();
  await fetch(`${JIRA}/__qa/reset`, { method: "POST", body: "{}" });
  await fetch(`${LLM}/__qa/reset`, { method: "POST", body: "{}" });
  await cleanup();
  const api = await waitSynced("qa-user");
  for (const [key, file] of Object.entries(CANDIDATES)) {
    const r = await api.post(`cv/employees/${key}/files`, {
      params: { kind: "main_cv", file_name: file },
      data: readFileSync(CV(file)),
      headers: { ...(await csrf(api)), "Content-Type": "application/octet-stream" },
    });
    expect(r.status()).toBe(201);
  }
  await api.dispose();
});

test.afterAll(async () => {
  await cleanup();
  await fetch(`${LLM}/__qa/reset`, { method: "POST", body: "{}" });
});

test("E2E-11 загрузка основного CV: окно, тип обязателен, файл в списке, текст доступен (FR-022, FR-027)", async ({ page }) => {
  await uiLogin(page, "qa-user");
  const row = await openResumeRow(page);
  await row.getByRole("button", { name: "Добавить" }).click();
  const dialog = page.locator(".modal");
  await expect(dialog.getByRole("button", { name: "Загрузить" })).toBeDisabled(); // без типа и файла нельзя
  await dialog.getByLabel("Тип файла").selectOption("main_cv");
  await dialog.locator('input[type="file"]').setInputFiles(CV("cv_main_ok.docx"));
  await expect(dialog.getByText("Допустимые форматы: .doc, .docx")).toBeVisible();
  await dialog.getByRole("button", { name: "Загрузить" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(row.getByText("cv_main_ok.docx")).toBeVisible();
  await expect(row.getByRole("button", { name: "Показать текст" })).toBeVisible();
  await row.getByRole("button", { name: "Показать текст" }).click();
  await expect(page.getByText("Системный аналитик, опыт работы 7 лет")).toBeVisible();
  await page.screenshot({ path: "artifacts/e2e-11-cv-text.png" });
});

test("E2E-12 недопустимые файлы не попадают в карточку: PDF как CV и размер (FR-022 п. 3)", async ({ page }) => {
  await uiLogin(page, "qa-user");
  const row = await openResumeRow(page);
  const before = await row.locator(".file").count();
  await row.getByRole("button", { name: "Добавить" }).click();
  const dialog = page.locator(".modal");
  await dialog.getByLabel("Тип файла").selectOption("main_cv");
  await dialog.locator('input[type="file"]').setInputFiles(CV("cv_as_pdf.pdf"));
  await dialog.getByRole("button", { name: "Загрузить" }).click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await dialog.locator('input[type="file"]').setInputFiles(CV("cv_big.docx"));
  await dialog.getByRole("button", { name: "Загрузить" }).click();
  await expect(dialog.getByRole("alert")).toContainText("МБ");
  await dialog.getByRole("button", { name: "Отмена" }).click();
  await expect(row.locator(".file")).toHaveCount(before);
});

test("E2E-13 замена основного CV: прежняя версия в архиве, восстановление (FR-039)", async ({ page }) => {
  await uiLogin(page, "qa-user");
  const row = await openResumeRow(page);
  for (const v of ["cv_main_v2.docx", "cv_main_v3.docx"]) {
    await row.getByRole("button", { name: "Добавить" }).click();
    const dialog = page.locator(".modal");
    await dialog.getByLabel("Тип файла").selectOption("main_cv");
    await dialog.locator('input[type="file"]').setInputFiles(CV(v));
    await expect(dialog.getByText("сохранится в архиве")).toBeVisible();
    await dialog.getByRole("button", { name: "Загрузить" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(row.getByText(v).first()).toBeVisible();
  }
  await row.getByRole("button", { name: "Архив основного CV" }).click();
  const archived = row.locator(".files__archive");
  await expect(archived.getByText("cv_main_ok.docx")).toBeVisible();
  await expect(archived.getByText("cv_main_v2.docx")).toBeVisible();
  await archived.getByRole("listitem").filter({ hasText: "cv_main_ok.docx" }).getByRole("button", { name: "Восстановить" }).click();
  await expect(row.locator(".files__list").first().getByText("cv_main_ok.docx")).toBeVisible();
  await page.screenshot({ path: "artifacts/e2e-13-archive.png" });
});

test("E2E-14 группы потребностей: счётчики, выбор запоминается после повторного входа (FR-035, FR-001)", async ({ page }) => {
  await uiLogin(page, "qa-user");
  await expect(page).toHaveURL(/\/needs$/);
  const nav = page.getByRole("navigation", { name: "Группы статусов" });
  await expect(nav.getByRole("button").first()).toBeVisible();
  const names = await nav.getByRole("button").allInnerTexts();
  expect(names.some((n) => n.startsWith("Все статусы"))).toBeTruthy();
  expect(names.some((n) => n.startsWith("Прочие"))).toBeTruthy();
  await nav.getByRole("button", { name: /^Все статусы/ }).click();
  await expect(nav.getByRole("button", { name: /^Все статусы/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("row").filter({ hasText: "Системный аналитик в проект" }).first()).toBeVisible();
  // выход и повторный вход: выбранная группа сохраняется (localStorage)
  await page.evaluate(() => fetch("/api/auth/me").then(() => undefined));
  await page.context().clearCookies();
  await uiLogin(page, "qa-user");
  await expect(page.getByRole("navigation", { name: "Группы статусов" }).getByRole("button", { name: /^Все статусы/ }))
    .toHaveAttribute("aria-pressed", "true");
  await page.screenshot({ path: "artifacts/e2e-14-groups.png" });
});

test("E2E-15 сопоставление: «Сопоставить», % по убыванию, «Показать ещё», «Пересчитать» (FR-005, FR-036)", async ({ page }) => {
  await uiLogin(page, "qa-user");
  await page.getByRole("navigation", { name: "Группы статусов" }).getByRole("button", { name: /^Все статусы/ }).click();
  await page.getByRole("searchbox", { name: "Поиск по ключу и названию" }).fill(NEED);
  await page.getByRole("button", { name: "Найти" }).click();
  await expect(page.locator("table.needs > tbody > tr")).toHaveCount(1);
  const row = page.locator("table.needs > tbody > tr").first();
  await row.getByRole("button", { name: /Сопоставить|кандидат/ }).first().click();
  const block = page.locator(".candidates-block");
  await expect(block).toBeVisible();
  await expect(block.getByText(/Показано \d+ из \d+/)).toBeVisible({ timeout: 60_000 });
  const pcts = (await block.locator("tbody tr").allInnerTexts())
    .map((t) => /(\d{1,3})\s?%/.exec(t)?.[1])
    .filter(Boolean)
    .map(Number);
  expect(pcts.length).toBeGreaterThanOrEqual(3);
  expect([...pcts].sort((a, b) => b - a)).toEqual(pcts);
  await expect(block.getByRole("button", { name: "Показать ещё" })).toBeVisible();
  await expect(block.getByRole("button", { name: "Пересчитать" })).toBeVisible();
  await page.screenshot({ path: "artifacts/e2e-15-matching.png" });
});

test("E2E-16 внешний провайдер: предпросмотр обезличенного текста и подтверждение (FR-023)", async ({ page }) => {
  // «Внешний» провайдер на фейке LLM: создан через API админа, признак внешнего - в БД стенда (HTTPS недоступен)
  const a = await adminApi();
  const r = await a.api.post("admin/llm/providers", {
    data: { name: EXT, url: "http://fake-llm:8080/v1", is_external: false, api_key: "FAKE_ext_e2e_key", model: "fake-ext", timeout_sec: 30 },
    headers: await csrf(a.api),
  });
  expect(r.status()).toBe(201);
  sql("app_db", `UPDATE app.llm_provider SET is_external = true WHERE name = '${EXT}'`);
  await a.api.dispose();

  await uiLogin(page, "qa-user");
  await page.getByRole("navigation", { name: "Группы статусов" }).getByRole("button", { name: /^Все статусы/ }).click();
  await page.getByRole("searchbox", { name: "Поиск по ключу и названию" }).fill("ANALYTICS-1002");
  await page.getByRole("button", { name: "Найти" }).click();
  await expect(page.locator("table.needs > tbody > tr")).toHaveCount(1);
  const row = page.locator("table.needs > tbody > tr").first();
  await row.getByRole("button", { name: /Сопоставить|кандидат/ }).first().click();
  const block = page.locator(".candidates-block");
  // из строки расчёт стартует сразу на модели по умолчанию; внешняя выбирается и запускается «Пересчитать»
  await expect(block.getByText(/Показано \d+ из \d+/)).toBeVisible({ timeout: 60_000 });
  await block.getByLabel("Модель").selectOption({ label: `${EXT} (внешняя, с обезличиванием)` });
  await block.getByRole("button", { name: "Пересчитать" }).click();
  const dialog = page.locator(".modal");
  await expect(dialog.getByRole("button", { name: /Подтвердить и отправить/ })).toBeVisible({ timeout: 30_000 });
  await expect(dialog).not.toContainText(EMP_NAME);
  const wire = (await (await fetch(`${LLM}/__qa/captured`)).json()) as { kind: string; auth: string }[];
  // до подтверждения на внешний провайдер ничего не ушло (запросы по умолчанию - только с ключом внутренней модели)
  expect(wire.filter((c) => c.kind === "match" && c.auth.includes("FAKE_ext_e2e_key")).length).toBe(0);
  await page.screenshot({ path: "artifacts/e2e-16-preview.png" });
  await dialog.getByRole("button", { name: /Подтвердить и отправить/ }).click();
  await expect(block.getByText(/Показано \d+ из \d+/)).toBeVisible({ timeout: 60_000 });
});
