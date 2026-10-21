// KON-109 (Pre.QA): приёмка п. 9-11 дополнения KON-79 на стенде :8080 (UI-бандл стенда, API замокан page.route; на базе ui_kon100.mjs).
// KON-101 (Pre.QA): приёмка UI окна «Назначить» (KON-100, KON-79) на dev-сервере vite с замоканным API.
// Запуск: vite на :5199 (cd frontend && npx vite --port 5199), затем node qa/e2e/ui_kon100.mjs
// Секретов нет: API целиком подменён page.route, Jira и бэкенд не используются.
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "ui_kon109");
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
const SEND = "Подтвердить и отправить в Jira";
for (const theme of ["dark", "light"]) for (const width of [1280, 1024]) {
  const tag = `${theme}_${width}`;
  const ctx = await browser.newContext({ viewport: { width, height: 800 }, colorScheme: theme, locale: "ru-RU" });
  try {
    // П.9 Клиент
    {
      const page = await ctx.newPage();
      const posts = await setup(page, "от клиента", [OK]);
      const dlg = await openAssign(page);
      const rate = dlg.getByLabel(/Ставка/);
      check(`[${tag}] п.9 disabled`, await rate.isDisabled());
      check(`[${tag}] п.9 без *`, !(await dlg.locator("label[for=assign-rate]").innerText()).includes("*"));
      const ph = await rate.getAttribute("placeholder");
      check(`[${tag}] п.9 без placeholder`, !ph, String(ph));
      const hint = await dlg.locator("#assign-rate-hint").innerText();
      check(`[${tag}] п.9 подсказка «В потребности:» + фраза клиента`, hint.includes("В потребности:") && hint.includes("Ставку предлагает клиент — здесь не вводится."), hint);
      await page.screenshot({ path: `${OUT}/assign_client_${tag}.png` });
      await dlg.getByRole("button", { name: "Назначить…" }).click();
      const conf = page.getByRole("dialog", { name: "Подтвердите назначение" });
      check(`[${tag}] п.9 подтверждение «ставка клиента, не передаётся»`, (await conf.innerText()).includes("ставка клиента, не передаётся"));
      await page.screenshot({ path: `${OUT}/confirm_client_${tag}.png` });
      await conf.getByRole("button", { name: SEND }).click();
      await page.getByRole("dialog").waitFor({ state: "detached" });
      const as = posts.filter((x) => x.path === "/needs/AN-5919/assign");
      check(`[${tag}] п.9 rate_rub=null в запросе`, as[0]?.body?.rate_rub === null, JSON.stringify(as[0]?.body));
      await page.close();
    }
    // П.10 Производство + прогресс
    {
      const page = await ctx.newPage();
      const rp = [OK]; rp.delay = 1500;
      await setup(page, "от производства", rp);
      const dlg = await openAssign(page);
      const rate = dlg.getByLabel(/Ставка/);
      check(`[${tag}] п.10 активно, *`, (await rate.isEnabled()) && (await dlg.locator("label[for=assign-rate]").innerText()).includes("*"));
      check(`[${tag}] п.10 placeholder «например, 3 800»`, (await rate.getAttribute("placeholder")) === "например, 3 800");
      await rate.fill("3 800");
      await dlg.getByRole("button", { name: "Назначить…" }).click();
      const conf = page.getByRole("dialog", { name: "Подтвердите назначение" });
      check(`[${tag}] п.10 подтверждение «3 800 руб. с НДС»`, /3\s800 руб\. с НДС/.test(await conf.innerText()));
      await conf.getByRole("button", { name: SEND }).click();
      await page.locator(".steps-list").waitFor();
      const items = await page.locator(".steps-list li").allInnerTexts();
      check(`[${tag}] п.10 шаг «Ставка» последним в прогрессе (после «Комментарий и статус…»)`, items.length > 1 && /Ставка$/.test(items[items.length - 1].trim()) && /Комментарий и статус/.test(items[items.length - 2]), JSON.stringify(items));
      await page.screenshot({ path: `${OUT}/progress_prod_${tag}.png` });
      await page.getByRole("dialog").waitFor({ state: "detached" });
      await page.close();
    }
    // П.10: при «Клиент» шага «Ставка» нет
    {
      const page = await ctx.newPage();
      const rp = [OK]; rp.delay = 1200;
      await setup(page, "от клиента", rp);
      const dlg = await openAssign(page);
      await dlg.getByRole("button", { name: "Назначить…" }).click();
      await page.getByRole("dialog", { name: "Подтвердите назначение" }).getByRole("button", { name: SEND }).click();
      await page.locator(".steps-list").waitFor();
      const items = await page.locator(".steps-list li").allInnerTexts();
      check(`[${tag}] п.9/10 при «Клиент» шага «Ставка» в прогрессе нет`, !items.some((t) => /Ставка$/.test(t.trim())), JSON.stringify(items));
      await page.getByRole("dialog").waitFor({ state: "detached" });
      await page.close();
    }
    // П.11 частичный успех
    {
      const page = await ctx.newPage();
      const posts = await setup(page, "от производства", [PART, OK]);
      const dlg = await openAssign(page);
      await dlg.getByRole("checkbox", { name: /Works/ }).check();
      await dlg.getByLabel("Комментарий (уйдёт комментарием в потребность)").fill("тест");
      await dlg.getByLabel(/Ставка/).fill("3 800");
      await dlg.getByRole("button", { name: "Назначить…" }).click();
      await page.getByRole("dialog", { name: "Подтвердите назначение" }).getByRole("button", { name: SEND }).click();
      const warn = page.locator(".box--warn");
      await warn.waitFor();
      const d = page.getByRole("dialog");
      check(`[${tag}] п.11 окно не закрылось`, await d.isVisible());
      const wt = (await warn.innerText()).replace(/\n/g, " / ");
      check(`[${tag}] п.11 box--warn role=alert, заголовок и причина`, (await warn.getAttribute("role")) === "alert" && wt.includes("Кандидат назначен, ставка не записана") && wt.includes("Jira ответила 200"), wt);
      check(`[${tag}] п.11 Резюме заблокировано`, await d.getByLabel("Резюме").isDisabled());
      check(`[${tag}] п.11 Комментарий заблокирован`, await d.getByLabel("Комментарий (уйдёт комментарием в потребность)").isDisabled());
      const cbs = d.locator("fieldset input[type=checkbox]");
      const n = await cbs.count();
      let allDis = n > 0; for (let i = 0; i < n; i++) allDis = allDis && (await cbs.nth(i).isDisabled());
      check(`[${tag}] п.11 все чекбоксы Файлов заблокированы`, allDis, `чекбоксов: ${n}`);
      const tplBtn = d.getByRole("button", { name: /CV по шаблону клиента/ });
      console.log(`INFO [${tag}] «CV по шаблону клиента…» disabled=${await tplBtn.isDisabled()}`);
      check(`[${tag}] п.11 ставка редактируема, значение сохранено`, (await d.getByLabel(/Ставка/).isEnabled()) && (await d.getByLabel(/Ставка/).inputValue()) === "3 800");
      const btns = (await d.locator("button").allInnerTexts()).join("|");
      check(`[${tag}] п.11 «Повторить», «Закрыть», нет «Отмена»`, (await d.getByRole("button", { name: "Повторить" }).isVisible()) && (await d.getByRole("button", { name: "Закрыть", exact: true }).count()) >= 1 && (await d.getByRole("button", { name: "Отмена", exact: true }).count()) === 0, btns);
      await page.screenshot({ path: `${OUT}/partial_${tag}.png` });
      await d.getByLabel(/Ставка/).fill("3 900");
      await d.getByRole("button", { name: "Повторить" }).click();
      await d.waitFor({ state: "detached" });
      const as = posts.filter((x) => x.path === "/needs/AN-5919/assign");
      check(`[${tag}] п.11 повтор: rate_rub=3900, completed без rate (вложения не дублируются)`, as[1]?.body?.rate_rub === 3900 && JSON.stringify(as[1].body.completed) === JSON.stringify(["fields", "attach:1", "transition"]), JSON.stringify(as[1]?.body));
      await page.close();
    }
    // KON-106: отступы и «×»
    {
      const page = await ctx.newPage();
      await setup(page, "от производства", [OK]);
      const dlg = await openAssign(page);
      const m = await dlg.evaluate((el) => {
        const f = [...el.querySelectorAll(".assign-form > .field")];
        const gaps = f.slice(0, -1).map((n, i) => Math.round(f[i + 1].getBoundingClientRect().top - n.getBoundingClientRect().bottom));
        const x = [...el.querySelectorAll("button")].find((b) => /^[×✕x]$/i.test(b.textContent.trim()) || /закрыть/i.test(b.getAttribute("aria-label") || ""));
        return { n: f.length, gaps, x: x ? { border: getComputedStyle(x).borderTopWidth, bg: getComputedStyle(x).backgroundColor } : null };
      });
      check(`[${tag}] KON-106 зазоры между полями 14px по макету (допуск 12-20)`, m.gaps.length >= 3 && m.gaps.every((g) => g >= 12 && g <= 20), JSON.stringify(m));
      await page.screenshot({ path: `${OUT}/spacing_${tag}.png` });
      await page.close();
    }
  } catch (e) { check(`[${tag}] сценарий без исключений`, false, String(e).split("\n")[0]); }
  await ctx.close();
}
// Высота 400: частичный успех
for (const theme of ["dark", "light"]) for (const width of [1280, 1024]) {
  const tag = `${theme}_${width}_h400`;
  const ctx = await browser.newContext({ viewport: { width, height: 400 }, colorScheme: theme, locale: "ru-RU" });
  try {
    const page = await ctx.newPage();
    await setup(page, "от производства", [PART, OK]);
    const dlg = await openAssign(page);
    await dlg.getByLabel(/Ставка/).fill("3 800");
    await dlg.getByRole("button", { name: "Назначить…" }).click();
    await page.getByRole("dialog", { name: "Подтвердите назначение" }).getByRole("button", { name: SEND }).click();
    await page.locator(".box--warn").waitFor();
    const d = page.getByRole("dialog");
    const box = await d.boundingBox();
    const btn = await d.getByRole("button", { name: "Повторить" }).boundingBox();
    const cl = await d.getByRole("button", { name: "Закрыть", exact: true }).last().boundingBox();
    check(`[${tag}] п.11 окно в 400px, футер («Повторить»/«Закрыть») видим`, box.y >= 0 && box.y + box.height <= 400 && btn.y + btn.height <= 400 && cl.y + cl.height <= 400, JSON.stringify({ box, btn, cl }));
    const info = await d.evaluate((el) => { const w = el.querySelector(".box--warn"); const sc = [...el.querySelectorAll("*")].find((n) => /auto|scroll/.test(getComputedStyle(n).overflowY) && n.scrollHeight > n.clientHeight + 2); return { scroll: !!sc, warnOverflowX: w.scrollWidth > w.clientWidth + 1, warnH: Math.round(w.getBoundingClientRect().height) }; });
    check(`[${tag}] п.11 тело прокручивается, блок не ломается по ширине`, info.scroll && !info.warnOverflowX, JSON.stringify(info));
    await page.screenshot({ path: `${OUT}/partial_${tag}.png` });
    await page.close();
  } catch (e) { check(`[${tag}] сценарий без исключений`, false, String(e).split("\n")[0]); }
  await ctx.close();
}
await browser.close();
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
console.log(`ИТОГО: ${results.filter((r) => r.ok).length}/${results.length} PASS`);
