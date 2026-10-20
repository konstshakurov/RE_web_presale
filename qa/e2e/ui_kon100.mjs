// KON-101 (Pre.QA): приёмка UI окна «Назначить» (KON-100, KON-79) на dev-сервере vite с замоканным API.
// Запуск: vite на :5199 (cd frontend && npx vite --port 5199), затем node qa/e2e/ui_kon100.mjs
// Секретов нет: API целиком подменён page.route, Jira и бэкенд не используются.
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "ui_kon100");
mkdirSync(OUT, { recursive: true });
const BASE = process.env.UI_BASE || "http://127.0.0.1:5199";
const STAMP = { scope: "needs", state: "ok", stale: false, paused: false, code: "", message: "", interval_sec: 300 };
const ME = { login: "test.user", display_name: "Тест Пользователь", role: "user", jira_status: { state: "ok" }, sections: [], csrf_token: "FAKE_csrf" };
const file = (id, kind, name, title) => ({ id, employee_key: "EMP-1", kind, kind_title: title, file_name: name, mime_type: "application/pdf",
  size_bytes: 1, is_archived: false, uploaded_by: "x", uploaded_at: "2026-10-10T10:00:00Z", extraction_status: "ok", can_delete: true, can_restore: false });
const need = (proposer) => ({ jira_key: "AN-5919", title: "Аналитик 1С", status: "Поиск кандидата", epic_key: null,
  rate: { amount: 3600, currency: "RUB", includes_vat: true, proposer }, jira_created_at: "2026-10-01T08:30:00Z", jira_updated_at: "2026-10-09T09:00:00Z" });
const MATCH = { need_key: "AN-5919", status: "done", requested_by: "test.user", model: "qwen", started_at: "2026-10-09T10:00:00Z",
  calculated_at: "2026-10-09T10:01:00Z", stale: false, candidates_total: 1, evaluated: 1, total: 1,
  items: [{ employee_key: "EMP-1", full_name: "Тестов Тест", status: "Свободен", percent: 82, rationale: "Совпадают 1С и REST", cv_file_id: 5,
    calculated_at: "2026-10-09T10:00:00Z", stale: false, skills: [] }], tasks: [], batches_total: 1, batches_done: 1, batches_failed: 0, errors: [],
  can_evaluate_next: false, deep_size: 30, remaining: 0, run_id: null };
const HIRE = { need_key: "AN-5919", status: "Поиск кандидата", actions: [{ action: "hire", label: "Найм", target_status: "Найм", comment_required: false, fields: [
  { field_id: "customfield_20809", name: "Планируемая ставка", required: true, kind: "rate", options: [], prefill_id: null },
  { field_id: "customfield_20810", name: "Включает НДС", required: true, kind: "vat", options: [{ id: "20999", name: "Да" }, { id: "21000", name: "Нет" }], prefill_id: "20999" },
  { field_id: "customfield_20811", name: "Валюта", required: true, kind: "currency", options: [{ id: "21001", name: "руб." }], prefill_id: "21001" },
  { field_id: "customfield_20812", name: "Единица измерения", required: true, kind: "unit", options: [{ id: "21006", name: "ч (час)" }], prefill_id: "21006" }] }] };

const results = [];
const check = (name, ok, extra = "") => { results.push({ name, ok: !!ok, extra }); console.log((ok ? "PASS " : "FAIL ") + name + (extra ? " :: " + extra : "")); };

async function setup(page, proposer, replies) {
  const posts = [];
  await page.route((u) => u.pathname.startsWith("/api/"), async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname.replace(/^\/api/, "");
    const json = (b, s = 200) => route.fulfill({ status: s, contentType: "application/json", body: JSON.stringify(b) });
    if (req.method() === "POST") posts.push({ path: p, body: req.postData() ? JSON.parse(req.postData()) : null });
    if (p === "/auth/me") return json(ME);
    if (p === "/needs/match-counts") return json({ counts: { "AN-5919": 1 } });
    if (p === "/sync/needs/groups") return json({ groups: [{ key: "all", id: null, name: "Все статусы", count: 1 }], statuses: [], sync: STAMP });
    if (p === "/sync/needs") return json({ items: [need(proposer)], total: 1, limit: 100, offset: 0, sync: STAMP });
    if (p === "/needs/AN-5919/status-actions") return json(HIRE);
    if (p.startsWith("/needs/AN-5919/match")) return json(MATCH);
    if (p === "/llm/providers") return json([]);
    if (p === "/cv/employees/EMP-1/files") return json({ employee_key: "EMP-1", files: [file(1, "main_cv", "Main.docx", "Основное CV"), file(2, "extra_cv", "Eng.pdf", "Доп. CV"), file(3, "materials", "Works.pdf", "Материалы")], archive: [] });
    if (p === "/needs/AN-5919/assign" && req.method() === "POST") return json(replies.length > 1 ? replies.shift() : replies[0]);
    if (p === "/needs/AN-5919/status" && req.method() === "POST") return json({ need_key: "AN-5919", status: "Найм", refreshed: true, steps: ["transition"], warnings: [], rate_pending: false });
    return json({ detail: "не найдено" }, 404);
  });
  return posts;
}

async function openAssign(page) {
  await page.goto(BASE + "/needs");
  await page.getByRole("button", { name: /^1 кандидат.*, AN-5919$/ }).click();
  await page.getByRole("button", { name: "Назначить", exact: true }).click();
  const dlg = page.getByRole("dialog", { name: "Назначить кандидата" });
  await dlg.getByRole("checkbox", { name: /Works/ }).waitFor();
  return dlg;
}

const OK = { need_key: "AN-5919", status: "Кандидат назначен", refreshed: true, steps: ["fields", "attach:1", "transition", "rate"], warnings: [], rate_pending: false };
const PART = { ...OK, steps: ["fields", "attach:1", "transition"], rate_pending: true, warnings: ["Кандидат назначен, но ставка не записана в Jira: Jira ответила 200, но ставка не записана: значение в потребности не изменилось. Введённое сохранено, повторите запись ставки"] };

const browser = await chromium.launch();
for (const theme of ["dark", "light"]) for (const width of [1280, 1024]) {
  const tag = `${theme}_${width}`;
  const ctx = await browser.newContext({ viewport: { width, height: 800 }, colorScheme: theme, locale: "ru-RU" });
  try {
    // 1. Производство: форма, обязательность, подтверждение, отправка, частичный успех, повтор
    {
      const page = await ctx.newPage();
      const posts = await setup(page, "от производства", [PART, OK]);
      const dlg = await openAssign(page);
      const rate = dlg.getByLabel(/Ставка/);
      check(`[${tag}] производство: поле ставки активно, звёздочка обязательности`, (await rate.isEnabled()) && (await dlg.locator("label[for=assign-rate]").innerText()).includes("*"));
      check(`[${tag}] производство: ориентир "В потребности"`, (await dlg.locator("#assign-rate-hint").innerText()).includes("В потребности"), await dlg.locator("#assign-rate-hint").innerText());
      await page.screenshot({ path: `${OUT}/assign_prod_${tag}.png` });
      await dlg.getByRole("button", { name: "Назначить…" }).click();
      check(`[${tag}] производство: пустая ставка -> «Укажите ставку»`, (await dlg.getByRole("alert").innerText()).includes("Укажите ставку"));
      await page.screenshot({ path: `${OUT}/assign_prod_err_${tag}.png` });
      await rate.fill("3 800");
      await dlg.getByRole("button", { name: "Назначить…" }).click();
      const conf = page.getByRole("dialog", { name: "Подтвердите назначение" });
      check(`[${tag}] подтверждение: ставка руб. с НДС`, /3\s800 руб\. с НДС/.test(await conf.innerText()));
      await page.screenshot({ path: `${OUT}/confirm_prod_${tag}.png` });
      await conf.getByRole("button", { name: "Подтвердить и отправить в Jira" }).click();
      const warn = page.locator(".box--warn");
      await warn.waitFor();
      check(`[${tag}] частичный успех: блок предупреждения, role=alert`, (await warn.getAttribute("role")) === "alert" && (await warn.innerText()).includes("ставка не записана"));
      check(`[${tag}] частичный успех: введённая ставка сохранена`, (await page.getByRole("dialog").getByLabel(/Ставка/).inputValue()) === "3 800");
      check(`[${tag}] частичный успех: основное действие «Повторить»`, await page.getByRole("button", { name: "Повторить" }).isVisible());
      await page.screenshot({ path: `${OUT}/partial_${tag}.png` });
      await page.getByRole("button", { name: "Повторить" }).click();
      await page.getByRole("dialog").waitFor({ state: "detached" });
      const as = posts.filter((x) => x.path === "/needs/AN-5919/assign");
      check(`[${tag}] тело 1: rate_rub=3800, completed=[]`, as[0]?.body?.rate_rub === 3800 && as[0].body.completed.length === 0, JSON.stringify(as[0]?.body));
      check(`[${tag}] тело 2 (повтор): completed без rate`, JSON.stringify(as[1]?.body?.completed) === JSON.stringify(["fields", "attach:1", "transition"]), JSON.stringify(as[1]?.body?.completed));
      await page.close();
    }
    // 2. Клиент
    {
      const page = await ctx.newPage();
      const posts = await setup(page, "от клиента", [OK]);
      const dlg = await openAssign(page);
      const rate = dlg.getByLabel(/Ставка/);
      check(`[${tag}] клиент: поле ставки disabled`, await rate.isDisabled());
      check(`[${tag}] клиент: без звёздочки`, !(await dlg.locator("label[for=assign-rate]").innerText()).includes("*"));
      check(`[${tag}] клиент: ставка клиента справочно`, (await dlg.locator("#assign-rate-hint").innerText()).includes("3"), await dlg.locator("#assign-rate-hint").innerText());
      console.log(`INFO [${tag}] клиент: placeholder у disabled-поля = «${await rate.getAttribute("placeholder")}» (предложение UX №1, решает владелец)`);
      await page.screenshot({ path: `${OUT}/assign_client_${tag}.png` });
      await dlg.getByRole("button", { name: "Назначить…" }).click();
      const conf = page.getByRole("dialog", { name: "Подтвердите назначение" });
      check(`[${tag}] клиент: подтверждение «Ставка: не указана»`, (await conf.innerText()).includes("не указана"));
      await conf.getByRole("button", { name: "Подтвердить и отправить в Jira" }).click();
      await page.getByRole("dialog").waitFor({ state: "detached" });
      const as = posts.filter((x) => x.path === "/needs/AN-5919/assign");
      check(`[${tag}] клиент: rate_rub=null в запросе`, as[0]?.body?.rate_rub === null, JSON.stringify(as[0]?.body));
      await page.close();
    }
    // 3. Найм: без поля ставки
    {
      const page = await ctx.newPage();
      const posts = await setup(page, "от производства", [OK]);
      await page.goto(BASE + "/needs");
      await page.getByRole("button", { name: "Найм, AN-5919" }).click();
      const dlg = page.getByRole("dialog", { name: "Найм" });
      await dlg.getByLabel(/Включает НДС/).waitFor();
      const labels = (await dlg.locator("label").allInnerTexts()).join(" | ");
      check(`[${tag}] найм: нет поля «Планируемая ставка»`, !/планируемая ставка/i.test(labels), labels);
      check(`[${tag}] найм: НДС, валюта, единица предзаполнены`, (await dlg.getByLabel(/Включает НДС/).inputValue()) === "20999" && (await dlg.getByLabel(/Валюта/).inputValue()) === "21001" && (await dlg.getByLabel(/Единица/).inputValue()) === "21006");
      check(`[${tag}] найм: подсказка не говорит про «поля ставки»`, !(await dlg.innerText()).includes("Поля ставки"), "текст в диалоге: " + ((await dlg.locator(".hint").allInnerTexts()).join(" / ")));
      await page.screenshot({ path: `${OUT}/hire_${tag}.png` });
      await dlg.getByRole("button", { name: /Перевести в/ }).click();
      await page.getByRole("dialog").waitFor({ state: "detached" });
      const st = posts.find((x) => x.path === "/needs/AN-5919/status");
      check(`[${tag}] найм: POST /status без customfield_20809`, st && !JSON.stringify(st.body).includes("20809"), JSON.stringify(st?.body));
      await page.close();
    }
  } catch (e) {
    check(`[${tag}] сценарий выполнен без исключений`, false, String(e).split("\n")[0]);
  }
  await ctx.close();
}
// 4. Критерий 8: высота окна 400px - шапка и футер на месте, тело прокручивается
for (const theme of ["dark", "light"]) for (const width of [1280, 1024]) {
  const tag = `${theme}_${width}_h400`;
  const ctx = await browser.newContext({ viewport: { width, height: 400 }, colorScheme: theme, locale: "ru-RU" });
  try {
    const page = await ctx.newPage();
    await setup(page, "от производства", [OK]);
    const dlg = await openAssign(page);
    const box = await dlg.boundingBox();
    const btn = await dlg.getByRole("button", { name: "Назначить…" }).boundingBox();
    const close = await dlg.getByRole("button", { name: /Закрыть|×/ }).first().boundingBox().catch(() => null);
    check(`[${tag}] окно умещается по высоте, футер с кнопкой в видимой области`, box && box.y >= 0 && box.y + box.height <= 400 && btn && btn.y + btn.height <= 400, JSON.stringify({ box, btn }));
    const scrollable = await dlg.evaluate((el) => [...el.querySelectorAll("*")].some((n) => n.scrollHeight > n.clientHeight + 2 && /auto|scroll/.test(getComputedStyle(n).overflowY)));
    check(`[${tag}] тело окна прокручивается`, scrollable);
    await page.screenshot({ path: `${OUT}/assign_${tag}.png` });
    await page.close();
  } catch (e) { check(`[${tag}] сценарий выполнен без исключений`, false, String(e).split("\n")[0]); }
  await ctx.close();
}
await browser.close();
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
console.log(`ИТОГО: ${results.filter((r) => r.ok).length}/${results.length} PASS`);
