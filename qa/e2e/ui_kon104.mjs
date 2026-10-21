// KON-104 (Pre.QA): текст подсказки диалога «Найм» на стенде :8080, API замокан page.route (без Jira и бэкенда).
// KON-101 (Pre.QA): приёмка UI окна «Назначить» (KON-100, KON-79) на dev-сервере vite с замоканным API.
// Запуск: vite на :5199 (cd frontend && npx vite --port 5199), затем node qa/e2e/ui_kon100.mjs
// Секретов нет: API целиком подменён page.route, Jira и бэкенд не используются.
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "ui_kon104");
mkdirSync(OUT, { recursive: true });
const BASE = process.env.UI_BASE || "http://127.0.0.1:8080";
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
  const delay = replies.delay || 0;
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
    if (p === "/needs/AN-5919/assign" && req.method() === "POST") { if (delay) await new Promise((r) => setTimeout(r, delay)); return json(replies.length > 1 ? replies.shift() : replies[0]); }
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
    const page = await ctx.newPage();
    await setup(page, "от производства", [OK]);
    await page.goto(BASE + "/needs");
    await page.getByRole("button", { name: "Найм, AN-5919" }).click();
    const dlg = page.getByRole("dialog", { name: "Найм" });
    await dlg.getByLabel(/Включает НДС/).waitFor();
    const hints = await dlg.locator(".hint").allInnerTexts();
    check(`[${tag}] подсказка = «Предзаполнено из потребности. Обязательно в Jira.»`, hints.includes("Предзаполнено из потребности. Обязательно в Jira."), JSON.stringify(hints));
    check(`[${tag}] нет старого текста «Поля ставки»`, !(await dlg.innerText()).includes("Поля ставки"));
    check(`[${tag}] нет поля «Планируемая ставка»`, !/планируемая ставка/i.test((await dlg.locator("label").allInnerTexts()).join("|")));
    await page.screenshot({ path: `${OUT}/hire_${tag}.png` });
  } catch (e) { check(`[${tag}] без исключений`, false, String(e).split("\n")[0]); }
  await ctx.close();
}
await browser.close();
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
console.log(`ИТОГО: ${results.filter((r) => r.ok).length}/${results.length} PASS`);
